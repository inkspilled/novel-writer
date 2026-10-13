"""记忆暂存器 — 跨章事实的结构化长期存储。

11 个桶：
- character_state: 角色状态（等级/位置/伤势/情绪）
- story_facts: 剧情事实（关键事件/转折）
- world_rules: 世界规则（已揭示的力量体系/禁忌）
- timeline: 时间线事件
- open_loops: 未解伏笔/悬念
- reader_promises: 对读者的承诺（待兑现的期待）
- relationships: 人物关系变化
- resources: 资源账本（物品获得/失去）
- emotional_arcs: 情感弧线（角色情绪变化追踪）
- info_boundary: 信息边界（谁知道什么/谁不知道什么）
- subplots: 支线进度（感情线/复仇线/成长线等）

同键去重：新值覆盖旧值（旧值标记 outdated 保留审计）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

MEMORY_FILE = "memory.json"

BUCKETS = [
    "character_state", "story_facts", "world_rules",
    "timeline", "open_loops", "reader_promises", "relationships",
    "resources", "emotional_arcs", "info_boundary", "subplots",
]

# 伏笔紧急度达到该值时，上下文尾部标注「优先回收」（见 docs/因果相关性算法.md）
LOOP_OVERDUE_URGENCY = 0.75


def loop_urgency(item: dict, current_chapter: int = 0, total_chapters: int = 0) -> float:
    """计算伏笔的动态紧急度（0-1）。

    在途计算（知道当前章号）以「年龄」为主，不采用 payload.urgency：
    刚埋的伏笔不应催收，15 章未收即到达超期线 0.75。
    - 年龄：每章 +0.03，20 章后饱和于 +0.6（基准 0.3）
    - 剧情阶段：进度 ≥60% 加成 +0.15，≥90% 加成 +0.3

    静态回退（current_chapter<=0，如仅展示、未进入写作循环）才读
    payload.urgency（缺省/脏数据回退 0.6）。

    Args:
        item: open_loops 记忆项（读 source_chapter 与 payload.urgency）
        current_chapter: 当前写作章号；<=0 时退化为静态 urgency
        total_chapters: 目标总章数；<=0 时不计阶段加成
    """
    if current_chapter <= 0:
        try:
            return min(1.0, max(0.0, float(item.get("payload", {}).get("urgency", 0.6))))
        except (TypeError, ValueError):
            return 0.6
    planted = item.get("source_chapter", 0) or 0
    age = max(0, current_chapter - planted)
    urgency = 0.3 + min(0.6, age * 0.03)
    if total_chapters > 0:
        progress = current_chapter / total_chapters
        if progress >= 0.9:
            urgency += 0.3
        elif progress >= 0.6:
            urgency += 0.15
    return min(1.0, urgency)


@dataclass
class MemoryItem:
    id: str = ""
    category: str = ""
    subject: str = ""
    aspect: str = ""
    value: str = ""
    status: str = "active"  # active / outdated / contradicted
    source_chapter: int = 0
    evidence: str = ""
    payload: dict = field(default_factory=dict)

    def dedup_key(self) -> str:
        if self.category in ("open_loops", "reader_promises"):
            return f"{self.category}:{self.subject}"
        return f"{self.category}:{self.subject}:{self.aspect}"

    def ensure_id(self):
        if not self.id:
            raw = self.dedup_key() + str(self.source_chapter)
            self.id = hashlib.sha256(raw.encode()).hexdigest()[:16]


class MemoryScratchpad:
    """项目级记忆暂存器。"""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self._path = project_dir / MEMORY_FILE
        self._data: dict[str, list[dict]] = {b: [] for b in BUCKETS}
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for b in BUCKETS:
                    self._data[b] = raw.get(b, [])
            except (json.JSONDecodeError, KeyError):
                pass

    def save(self):
        from .project_io import write_json
        write_json(self._path, self._data)

    def upsert(self, item: MemoryItem):
        """插入或更新记忆项。同键旧值标记 outdated。"""
        item.ensure_id()
        key = item.dedup_key()
        bucket = self._data.get(item.category, [])
        # 同键去重
        for existing in bucket:
            if existing.get("status") == "active":
                exist_key = f"{existing.get('category', '')}:{existing.get('subject', '')}:{existing.get('aspect', '')}"
                if existing.get("category") in ("open_loops", "reader_promises"):
                    exist_key = f"{existing.get('category', '')}:{existing.get('subject', '')}"
                if exist_key == key:
                    existing["status"] = "outdated"
        # 插入新值
        bucket.append(asdict(item))
        self._data[item.category] = bucket

    def query(self, category: str = "", subject: str = "", status: str = "active") -> list[dict]:
        """查询记忆项。"""
        results = []
        buckets = [category] if category else BUCKETS
        for b in buckets:
            for item in self._data.get(b, []):
                if status and item.get("status") != status:
                    continue
                if subject and subject not in item.get("subject", ""):
                    continue
                results.append(item)
        return results

    def get_active(self, category: str) -> list[dict]:
        """获取指定桶的所有活跃项。"""
        return [i for i in self._data.get(category, []) if i.get("status") == "active"]

    def get_open_loops(self, limit: int = 10, current_chapter: int = 0,
                       total_chapters: int = 0) -> list[dict]:
        """获取未解伏笔，按动态紧急度降序排序（公式见 docs/因果相关性算法.md）。

        current_chapter=0 时退化为埋设时的静态 urgency 排序。
        """
        loops = self.get_active("open_loops")
        decorated = [
            (loop_urgency(item, current_chapter, total_chapters), item)
            for item in loops
        ]
        decorated.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in decorated[:limit]]

    def close_loop(self, subject: str, source_chapter: int):
        """关闭一个伏笔。"""
        for item in self._data.get("open_loops", []):
            if item.get("subject") == subject and item.get("status") == "active":
                item["status"] = "outdated"
                break
        # 记录关闭事件
        self.upsert(MemoryItem(
            category="story_facts",
            subject=subject,
            aspect="伏笔回收",
            value=f"伏笔「{subject}」在第{source_chapter}章回收",
            source_chapter=source_chapter,
        ))

    def compact(self):
        """压缩：清理过时项，保留每个同键最新一条 outdated。

        去重键与 MemoryItem.dedup_key 一致（open_loops/reader_promises 忽略 aspect）。
        """
        def _key(item: dict) -> str:
            cat = item.get("category", "")
            subj = item.get("subject", "")
            asp = item.get("aspect", "")
            if cat in ("open_loops", "reader_promises"):
                return f"{cat}:{subj}"
            return f"{cat}:{subj}:{asp}"

        for b in BUCKETS:
            items = self._data.get(b, [])
            keep_active = [item for item in items if item.get("status") == "active"]
            # 同键只保留最新一条 outdated：后出现的覆盖先出现的
            latest_outdated: dict[str, dict] = {}
            for item in items:
                if item.get("status") == "outdated":
                    latest_outdated[_key(item)] = item
            self._data[b] = keep_active + list(latest_outdated.values())
