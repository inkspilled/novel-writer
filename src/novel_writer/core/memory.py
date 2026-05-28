"""记忆暂存器 — 跨章事实的结构化长期存储。

7 个桶：
- character_state: 角色状态（等级/位置/伤势/情绪）
- story_facts: 剧情事实（关键事件/转折）
- world_rules: 世界规则（已揭示的力量体系/禁忌）
- timeline: 时间线事件
- open_loops: 未解伏笔/悬念
- reader_promises: 对读者的承诺（待兑现的期待）
- relationships: 人物关系变化

同键去重：新值覆盖旧值（旧值标记 outdated 保留审计）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

MEMORY_FILE = "memory.json"

BUCKETS = [
    "character_state", "story_facts", "world_rules",
    "timeline", "open_loops", "reader_promises", "relationships",
    "resources", "emotional_arcs", "info_boundary", "subplots",
]


@dataclass
class MemoryItem:
    id: str = ""
    category: str = ""
    subject: str = ""
    field: str = ""
    value: str = ""
    status: str = "active"  # active / outdated / contradicted
    source_chapter: int = 0
    evidence: str = ""
    payload: dict = field(default_factory=dict)

    def dedup_key(self) -> str:
        if self.category in ("open_loops", "reader_promises"):
            return f"{self.category}:{self.subject}"
        return f"{self.category}:{self.subject}:{self.field}"

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
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def upsert(self, item: MemoryItem):
        """插入或更新记忆项。同键旧值标记 outdated。"""
        item.ensure_id()
        key = item.dedup_key()
        bucket = self._data.get(item.category, [])
        # 同键去重
        for existing in bucket:
            if existing.get("status") == "active":
                exist_key = f"{existing.get('category', '')}:{existing.get('subject', '')}:{existing.get('field', '')}"
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

    def get_open_loops(self, limit: int = 10) -> list[dict]:
        """获取未解伏笔，按紧急度排序。"""
        loops = self.get_active("open_loops")
        loops.sort(key=lambda x: x.get("payload", {}).get("urgency", 0.5), reverse=True)
        return loops[:limit]

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
            field="伏笔回收",
            value=f"伏笔「{subject}」在第{source_chapter}章回收",
            source_chapter=source_chapter,
        ))

    def compact(self):
        """压缩：清理过时项，保留每个同键最新一条 outdated。"""
        for b in BUCKETS:
            items = self._data.get(b, [])
            seen_keys: dict[str, int] = {}
            keep = []
            # 先保留所有 active
            for item in items:
                if item.get("status") == "active":
                    keep.append(item)
            # 再保留每个同键最新的一条 outdated
            for item in items:
                if item.get("status") == "outdated":
                    key = f"{item.get('subject', '')}:{item.get('field', '')}"
                    if key not in seen_keys:
                        seen_keys[key] = len(keep)
                        keep.append(item)
            self._data[b] = keep

    def build_memory_text(self, limit_per_bucket: int = 5) -> str:
        """构建记忆文本，用于注入 LLM 上下文。"""
        parts = []
        labels = {
            "character_state": "角色状态",
            "story_facts": "剧情事实",
            "world_rules": "世界规则",
            "timeline": "时间线",
            "open_loops": "未解伏笔",
            "reader_promises": "读者期待",
            "relationships": "人物关系",
            "resources": "资源账本",
            "emotional_arcs": "情感弧线",
            "info_boundary": "信息边界",
            "subplots": "支线进度",
        }
        for b in BUCKETS:
            items = self.get_active(b)[:limit_per_bucket]
            if not items:
                continue
            lines = [f"【{labels.get(b, b)}】"]
            for item in items:
                subj = item.get("subject", "")
                val = item.get("value", "")
                fld = item.get("field", "")
                ch = item.get("source_chapter", 0)
                if fld:
                    lines.append(f"- [{subj}] {fld}: {val}（第{ch}章）")
                else:
                    lines.append(f"- [{subj}] {val}（第{ch}章）")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)
