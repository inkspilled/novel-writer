"""故事线台账 — 时间线 / 主线 / 支线 / 伏笔的统一结构化视图。

定位（与已有模块的分工）：
- ``planning/时间线.md`` 等四份规划文档：**人读的规划稿**（作者与 LLM 写）
- ``memory.json``：跨章事实流水（open_loops / subplots / story_facts）
- ``world_state.json``：世界快照（含 timeline 实际发生事件）
- **本模块 = 四者的对账结果**：每条线一条记录，带状态、埋设/回收章、所属卷，
  供「世界」菜单的独立 UI 展示，也供工作流把台账摘要反哺回写作上下文。

数据落盘：``story_threads.json``（项目根目录，原子写）。

状态口径：``planned`` 未开始 / ``active`` 推进中 / ``done`` 已完成（伏笔=已回收）
/ ``abandoned`` 已放弃。伏笔的「超期」是**派生状态**（未回收且埋设 ≥15 章），不落盘。

反哺链路（单向、可重放）：
1. ``sync_from_planning``：解析四份规划文档 → 导入条目（LLM 规划完成后自动可读）
2. ``reconcile``：memory.subplots / memory.open_loops / world_state.timeline → 推进状态
3. ``build_context_block``：把「进行中的主线/支线 + 待回收伏笔 + 最近时间线」注入写作上下文
4. ``story_threads_changed`` 事件 → UI 刷新（见 ``core/workflow/runner.py``）

手工编辑优先：UI 改过的字段记在 ``user_edited``，后续自动对账不覆盖。
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)

STORY_THREADS_FILE = "story_threads.json"

# 规划文档 → 线程类型（文件名固定，与 project_io.PLANNING_FILES 一致）
DOC_THREAD_KINDS = {
    "timeline": "时间线.md",
    "main": "主线.md",
    "subplot": "支线.md",
    "foreshadow": "伏笔.md",
}

# 伏笔超期阈值（章）。与 memory.loop_urgency 的 15 章超期线一致
LOOP_OVERDUE_CHAPTERS = 15

# 单份规划文档最多导入条数（防止把纯散文误当成上千条）
MAX_ITEMS_PER_DOC = 300


class ThreadKind(str, Enum):
    TIMELINE = "timeline"
    MAIN = "main"
    SUBPLOT = "subplot"
    FORESHADOW = "foreshadow"


class ThreadStatus(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    DONE = "done"
    ABANDONED = "abandoned"


KIND_LABELS = {
    ThreadKind.TIMELINE.value: "时间线",
    ThreadKind.MAIN.value: "主线",
    ThreadKind.SUBPLOT.value: "支线",
    ThreadKind.FORESHADOW.value: "伏笔",
}

STATUS_LABELS = {
    ThreadStatus.PLANNED.value: "未开始",
    ThreadStatus.ACTIVE.value: "推进中",
    ThreadStatus.DONE.value: "已完成",
    ThreadStatus.ABANDONED.value: "已放弃",
}


# ── 章节号解析 ──

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_NUM = r"[0-9零〇一二两三四五六七八九十百千]{1,6}"


def cn_to_int(text: str) -> int | None:
    """把「12」/「十二」/「一百零三」转成 int；无法解析返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            return None
    total, section, digit = 0, 0, 0
    for ch in text:
        if ch in _CN_DIGITS:
            digit = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            section += (digit or 1) * unit
            digit = 0
        else:
            return None
    return total + section + digit


def extract_chapters(text: str) -> list[int]:
    """提取文本中的章号引用（支持「第12章」「第十二章」「第12-15章」「12章」）。"""
    if not text:
        return []
    found: list[int] = []

    # 范围：第12-15章 / 第12~15章 / 第12至15章 / 第12到15章
    for m in re.finditer(rf"第?({_NUM})\s*[-~—–至到]\s*({_NUM})\s*章", text):
        a, b = cn_to_int(m.group(1)), cn_to_int(m.group(2))
        if a and b:
            found.append(a)
            if b != a:
                found.append(b)

    # 单点：第12章 / 第十二章 / 12章
    for m in re.finditer(rf"第?({_NUM})\s*章", text):
        value = cn_to_int(m.group(1))
        if value:
            found.append(value)

    # 去重保序
    seen: set[int] = set()
    result: list[int] = []
    for n in found:
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result


# ── 卷 / 人物识别（把「卷/组」「人物」字段接上规划与实际数据） ──

_VOLUME_HEADING_RE = re.compile(
    r"^#{1,4}\s*(?:第\s*([0-9零〇一二两三四五六七八九十百千]{1,6})\s*卷"
    r"|卷\s*([0-9零〇一二两三四五六七八九十百千]{1,6}))"
)
_VOLUME_NAME_RE = re.compile(
    r"(?:第\s*([0-9零〇一二两三四五六七八九十百千]{1,6})\s*卷"
    r"|卷\s*([0-9零〇一二两三四五六七八九十百千]{1,6}))"
)
_VOLUME_RANGE_RE = re.compile(
    r"[（(]\s*第?\s*[0-9零〇一二两三四五六七八九十百千]{1,6}\s*"
    r"[-~—–至到]\s*[0-9零〇一二两三四五六七八九十百千]{1,6}\s*章?\s*[）)]"
)


def volume_ordinal(name: str) -> int:
    """「卷一 外门断事」→ 1；无法识别返回 0。"""
    m = _VOLUME_NAME_RE.search(name or "")
    return (cn_to_int(m.group(1) or m.group(2)) or 0) if m else 0


def parse_volume_ranges(text: str, target_chapters: int = 0) -> list[tuple[int, str, int, int]]:
    """从标题解析卷：(序号, 卷名, 起始章, 结束章)。

    兼容 ``## 卷一 外门断事（1—100章）`` / ``## 第二卷 xxx（第101-200章）``；
    没写结束章的卷用下一卷起始 -1 补齐，最后一卷用目标章数。
    """
    entries: dict[int, tuple[int, str, int, int]] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        m = _VOLUME_HEADING_RE.match(line)
        if not m:
            continue
        ordinal = cn_to_int(m.group(1) or m.group(2))
        if not ordinal:
            continue
        name = re.sub(r"^#{1,4}\s*", "", line).strip()
        chapters = extract_chapters(name)
        start = chapters[0] if chapters else 0
        end = chapters[1] if len(chapters) > 1 else 0
        name = _VOLUME_RANGE_RE.sub("", name).strip()
        old = entries.get(ordinal)
        # 有章区间的条目优先；同类时保留信息更全的卷名
        if old is None or (start and not old[2]) or (start == old[2] and len(name) > len(old[1])):
            entries[ordinal] = (ordinal, name, start, end)
    ordered = [entries[k] for k in sorted(entries)]
    any_ranges = any(start for _, _, start, _ in ordered)
    # 第一遍：没写起始章的卷，用上一卷结束章 +1 补
    resolved: list[list] = []
    prev_end = 0
    for ordinal, name, start, end in ordered:
        if not start and any_ranges:
            start = prev_end + 1
        if start:
            prev_end = end or start
        resolved.append([ordinal, name, start, end])
    # 第二遍：没写结束章的卷，用下一卷起始 -1；最后一卷用目标章数
    for i, row in enumerate(resolved):
        if not row[3]:
            next_start = resolved[i + 1][2] if i + 1 < len(resolved) else 0
            row[3] = (next_start - 1) if next_start else target_chapters
    return [tuple(row) for row in resolved]


def infer_owner(text: str, names, limit: int = 4) -> str:
    """从标题/详情里认出已知角色名，拼成「顾言、赵九衡」（按出现顺序）。"""
    hay = text or ""
    hits: list[str] = []
    for name in sorted({str(n).strip() for n in names or [] if n}, key=len, reverse=True):
        if len(name) >= 2 and name in hay and name not in hits:
            hits.append(name)
    hits.sort(key=lambda n: hay.find(n))
    return "、".join(hits[:limit])


# ── 状态词判定 ──

# 明确「已经做完」的词：规划稿里写这些才算完成（「第20章回收」只是计划，见 _PLAN_RESOLVE_WORDS）
_DONE_WORDS = ("已回收", "已揭晓", "已解决", "已完成", "已兑现", "已收束", "已结束",
               "已完结", "已闭合", "已揭示", "已圆", "已填坑", "已收线")
# 只表达「计划在这章收」的词：需要章号 <= 当前章才能算完成
_PLAN_RESOLVE_WORDS = ("回收", "揭晓", "解决", "收束", "兑现", "完结", "闭合")
_ABANDON_WORDS = ("放弃", "废弃", "砍掉", "删除", "弃用", "移除")
_ACTIVE_WORDS = ("推进中", "进行中", "推进", "展开中", "正在", "已埋设", "已埋", "埋设")


def detect_status(text: str) -> ThreadStatus | None:
    """从一句规划描述里粗判状态；无信号返回 None。"""
    if not text:
        return None
    if any(w in text for w in _ABANDON_WORDS):
        return ThreadStatus.ABANDONED
    if any(w in text for w in _DONE_WORDS):
        return ThreadStatus.DONE
    # 「第20章回收」：规划意图，落成 DONE 前还要过「章号是否已写到」这一关
    if any(w in text for w in _PLAN_RESOLVE_WORDS):
        return ThreadStatus.DONE
    if any(w in text for w in _ACTIVE_WORDS):
        return ThreadStatus.ACTIVE
    return None


# ── 模型 ──

@dataclass
class StoryThread:
    """一条故事线（时间线节点 / 主线节点 / 支线 / 伏笔）。"""

    id: str = ""
    kind: str = ThreadKind.MAIN.value
    title: str = ""
    detail: str = ""
    status: str = ThreadStatus.PLANNED.value
    group: str = ""                 # 所属卷/分组（来自规划文档标题层级）
    owner: str = ""                 # 涉及角色 / 线索归属
    location: str = ""              # 时间线地点
    chapters: dict = field(default_factory=dict)   # {"plant": n, "resolve": n}
    happened: bool = False          # 时间线：是否已实际发生（来自 world_state）
    source: str = "manual"          # planning / world_state / memory / manual
    planned_status: str = ""        # 规划稿里写的状态（区分「计划回收」与「已回收」）
    active: bool = True             # False = 规划稿里已删除（不展示，可恢复）
    user_edited: list = field(default_factory=list)
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    # ── 派生 ──

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def planted_chapter(self) -> int:
        return int(self.chapters.get("plant") or 0)

    @property
    def resolve_chapter(self) -> int:
        return int(self.chapters.get("resolve") or 0)

    def is_overdue(self, current_chapter: int) -> bool:
        """伏笔超期：未回收且埋设已过 LOOP_OVERDUE_CHAPTERS 章。"""
        if self.kind != ThreadKind.FORESHADOW.value:
            return False
        if self.status in (ThreadStatus.DONE.value, ThreadStatus.ABANDONED.value):
            return False
        planted = self.planted_chapter
        return bool(planted and current_chapter - planted >= LOOP_OVERDUE_CHAPTERS)

    def age(self, current_chapter: int) -> int:
        planted = self.planted_chapter
        return max(0, current_chapter - planted) if planted else 0

    def ui_status(self, current_chapter: int) -> tuple[str, str]:
        """UI 展示用 (状态文本, 语义色标签)。语义色由 styles 映射。"""
        if not self.active:
            return "已移除", "muted"
        if self.is_overdue(current_chapter):
            return f"超期 {self.age(current_chapter)} 章", "danger"
        if self.kind == ThreadKind.TIMELINE.value:
            if self.happened:
                return "已发生", "ok"
            return "计划中", "muted"
        if self.status == ThreadStatus.DONE.value:
            if self.kind == ThreadKind.FORESHADOW.value:
                return (f"已回收·第{self.resolve_chapter}章" if self.resolve_chapter else "已回收"), "ok"
            return "已完成", "ok"
        if self.status == ThreadStatus.ACTIVE.value:
            if self.kind == ThreadKind.FORESHADOW.value and self.resolve_chapter:
                return f"待回收·第{self.resolve_chapter}章", "active"
            return "推进中", "active"
        if self.status == ThreadStatus.ABANDONED.value:
            return "已放弃", "muted"
        return "未开始", "muted"

    def chapter_text(self) -> str:
        planted, resolved = self.planted_chapter, self.resolve_chapter
        if self.kind == ThreadKind.FORESHADOW.value:
            if planted and resolved:
                if self.status != ThreadStatus.DONE.value:
                    return f"第{planted}章埋·计划{resolved}收"
                return f"第{planted}→{resolved}章"
            if planted:
                return f"第{planted}章埋"
            if resolved:
                return f"第{resolved}章收"
            return "—"
        if self.kind == ThreadKind.TIMELINE.value:
            return f"第{planted}章" if planted else "—"
        if planted and resolved and resolved != planted:
            return f"第{planted}→{resolved}章"
        if planted:
            return f"第{planted}章起"
        if resolved:
            return f"第{resolved}章完"
        return "—"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "status": self.status,
            "group": self.group,
            "owner": self.owner,
            "location": self.location,
            "chapters": dict(self.chapters or {}),
            "happened": bool(self.happened),
            "source": self.source,
            "planned_status": self.planned_status,
            "active": bool(self.active),
            "user_edited": list(self.user_edited or []),
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StoryThread":
        if not isinstance(data, dict):
            return cls(title=str(data or ""))
        chapters = data.get("chapters")
        return cls(
            id=str(data.get("id") or ""),
            kind=str(data.get("kind") or ThreadKind.MAIN.value),
            title=str(data.get("title") or ""),
            detail=str(data.get("detail") or ""),
            status=str(data.get("status") or ThreadStatus.PLANNED.value),
            group=str(data.get("group") or ""),
            owner=str(data.get("owner") or ""),
            location=str(data.get("location") or ""),
            chapters=dict(chapters) if isinstance(chapters, dict) else {},
            happened=bool(data.get("happened")),
            source=str(data.get("source") or "manual"),
            planned_status=str(data.get("planned_status") or ""),
            active=bool(data.get("active", True)),
            user_edited=list(data.get("user_edited") or []),
            notes=str(data.get("notes") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


@dataclass
class StoryProgress:
    """四类线程的进度快照（UI 卡片 + 摘要行共用一份口径）。"""

    current_chapter: int = 0
    total_chapters: int = 0
    main_total: int = 0
    main_done: int = 0
    subplot_total: int = 0
    subplot_done: int = 0
    foreshadow_total: int = 0
    foreshadow_done: int = 0
    foreshadow_overdue: int = 0
    timeline_total: int = 0
    timeline_happened: int = 0

    @property
    def main_percent(self) -> int:
        return _percent(self.main_done, self.main_total)

    @property
    def subplot_percent(self) -> int:
        return _percent(self.subplot_done, self.subplot_total)

    @property
    def foreshadow_percent(self) -> int:
        return _percent(self.foreshadow_done, self.foreshadow_total)

    @property
    def timeline_percent(self) -> int:
        return _percent(self.timeline_happened, self.timeline_total)

    @property
    def chapter_percent(self) -> int:
        return _percent(self.current_chapter, self.total_chapters)

    def headline(self) -> str:
        """一句话进度：当前第 N 章 / 目标，四类完成度。"""
        if not any((self.main_total, self.subplot_total,
                    self.foreshadow_total, self.timeline_total)):
            pos = f"第 {self.current_chapter} 章" if self.current_chapter else "还没有故事线"
            return f"{pos} · 同步规划文档或新增一条线程"
        pos = f"第 {self.current_chapter} 章"
        if self.total_chapters:
            pos += f" / {self.total_chapters}（{self.chapter_percent}%）"
        return (
            f"{pos} · 主线 {self.main_done}/{self.main_total}"
            f" · 支线 {self.subplot_done}/{self.subplot_total}"
            f" · 伏笔 {self.foreshadow_done}/{self.foreshadow_total} 已回收"
            + (f"（超期 {self.foreshadow_overdue}）" if self.foreshadow_overdue else "")
            + f" · 时间线 {self.timeline_happened}/{self.timeline_total} 已发生"
        )


def _percent(done: int, total: int) -> int:
    if total <= 0:
        return 0
    return int(round(done / total * 100))


# ── 规划文档解析 ──

_LIST_RE = re.compile(r"^\s{0,3}(?:[-*+•]|\d{1,3}[.、)）]|[（(]\d{1,3}[）)])\s+(.+)$")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
_BOLD_ONLY_RE = re.compile(r"^\s*(?:\*\*|__)(.+?)(?:\*\*|__)\s*[:：]?\s*$")
_SKIP_TITLE_RE = re.compile(r"^(说明|注|备注|提示|注意|总体|原则|附注|总计|合计|以上|如下|详见)")
# 规划文档里常见的通用标题，不能拿来当「卷/分组」名
_GENERIC_DOC_TITLES = {"时间线", "主线", "支线", "伏笔", "剧情", "故事结构", "总览", "目录"}
_TITLE_SPLIT_RE = re.compile(
    r"\s+[—–-]{1,2}\s+|[：:]\s*|\s*[（(]\s*(?=第?\s*[0-9零〇一二两三四五六七八九十百千]{1,6}\s*章)"
)
_CHAPTER_HINT_RE = re.compile(r"第?\s*[0-9零〇一二两三四五六七八九十百千]{1,6}\s*章")


def _clean_md(text: str) -> str:
    """去掉 markdown 行内标记，保留可读文本。"""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text or "")
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def _split_row(line: str) -> list[str]:
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    return [_clean_md(c) for c in raw.split("|")]


def _column_roles(header: list[str]) -> dict[str, int]:
    """表头语义映射：把中文列名映射到 title/detail/plant/resolve/status/owner/group。"""
    roles: dict[str, int] = {}
    for i, cell in enumerate(header):
        c = cell or ""
        if any(k in c for k in ("埋设", "埋下", "起点", "开始", "出处", "首次")):
            roles.setdefault("plant", i)
        elif any(k in c for k in ("回收", "揭晓", "解决", "完成", "终点", "收束")):
            roles.setdefault("resolve", i)
        elif any(k in c for k in ("状态", "进度")):
            roles.setdefault("status", i)
        elif any(k in c for k in ("归属", "涉及", "负责", "角色", "人物", "线索")):
            roles.setdefault("owner", i)
        elif any(k in c for k in ("卷", "阶段", "分组", "时期")):
            roles.setdefault("group", i)
        elif any(k in c for k in ("内容", "描述", "说明", "备注", "事件", "剧情")):
            roles.setdefault("detail", i)
        elif any(k in c for k in ("名称", "伏笔", "标题", "节点", "主线", "支线", "时间")):
            roles.setdefault("title", i)
    return roles


def _item_title_and_detail(text: str) -> tuple[str, str]:
    """把一条列表文本拆成 (短标题, 详情)。"""
    clean = _clean_md(text)
    clean = re.sub(r"^(?:【[^】]*】|\[[^\]]*\])\s*", "", clean)
    if not clean:
        return "", ""
    # 短标题可能自带「—— 」「 ：」「（第N章」分隔
    split = _TITLE_SPLIT_RE.split(clean, maxsplit=1)
    if len(split) >= 2:
        title, detail = split[0], split[1]
    elif len(clean) <= 28:
        title, detail = clean, ""
    else:
        # 长句：第一个分句做标题（保留完整句做详情，便于回看原规划）
        parts = re.split(r"[，,；;。]", clean, maxsplit=1)
        title = parts[0].strip()
        detail = clean if len(parts) > 1 else ""
    title = title.strip(" 　:：,，.。;；、-—")
    if len(title) > 40:
        title = title[:40]
    return title, detail.strip()


def parse_planning_doc(content: str, kind: str) -> list[dict]:
    """解析一份规划文档，返回条目（标题/详情/分组/章号/状态）。

    兼容四种常见写法：
    1. ``- 伏笔A（第3章埋，第12章回收）`` 列表项
    2. ``### 第1卷：宗门风波`` + 其下列表 → 标题作为分组
    3. ``| 伏笔 | 埋设 | 回收 |`` 表格（按表头语义取列）
    4. 纯 ``**节点名**`` 行

    散文段落不会被硬拆成条目（宁可少显示，不制造噪声）。
    """
    items: list[dict] = []
    if not content:
        return items

    heading_stack: list[tuple[int, str]] = []
    parent_groups: list[tuple[int, str]] = []  # 生效中的「父项当分组」：(父项缩进, 名称)
    table_header: list[str] | None = None
    table_roles: dict[str, int] = {}
    in_fence = False
    raw_items: list[tuple[int, str, str, str, str]] = []  # 列表项（缩进/标题/详情/原文/标题分组）

    def group_label() -> str:
        for _level, text in reversed(heading_stack):
            if text:
                return text
        return ""

    for raw in content.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            text = _clean_md(heading.group(2))
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            # 一级标题是文档名（# 主线），通用名（伏笔/支线…）也不当分组
            if level > 1 and text and text not in _GENERIC_DOC_TITLES:
                heading_stack.append((level, text))
            table_header = None
            continue

        # ── 表格 ──
        if stripped.startswith("|"):
            if table_header is None:
                table_header = _split_row(stripped)
                table_roles = _column_roles(table_header)
                continue
            if _TABLE_SEP_RE.match(stripped):
                continue
            cells = _split_row(stripped)
            if not any(cells):
                continue
            title_idx = table_roles.get("title", 0)
            title = cells[title_idx] if title_idx < len(cells) else cells[0]
            detail = "；".join(
                c for i, c in enumerate(cells)
                if c and i not in (title_idx,) and i < len(table_header)
            )
            if not title:
                continue
            status_text = ""
            if "status" in table_roles and table_roles["status"] < len(cells):
                status_text = cells[table_roles["status"]]
            items.append(_make_item(
                kind=kind, title=title, detail=detail, group=group_label(),
                text=" ".join(cells), status_text=status_text,
                plant_text=cells[table_roles["plant"]] if table_roles.get("plant", 99) < len(cells) else "",
                resolve_text=cells[table_roles["resolve"]] if table_roles.get("resolve", 99) < len(cells) else "",
                owner=cells[table_roles["owner"]] if table_roles.get("owner", 99) < len(cells) else "",
            ))
            continue
        table_header = None

        # ── 列表项 ──
        m = _LIST_RE.match(line)
        if m:
            text = m.group(1)
            indent = len(line) - len(line.lstrip())
            if _SKIP_TITLE_RE.match(_clean_md(text)):
                continue
            title, detail = _item_title_and_detail(text)
            if len(title) < 2:
                continue
            raw_items.append((indent, title, detail, text, group_label()))
            continue

        # ── 独立加粗行 ──
        bold = _BOLD_ONLY_RE.match(stripped)
        if bold:
            text = bold.group(1)
            title, detail = _item_title_and_detail(text)
            if len(title) >= 2 and not _SKIP_TITLE_RE.match(title):
                items.append(_make_item(
                    kind=kind, title=title, detail=detail, group=group_label(),
                    text=text,
                ))
    # ── 列表嵌套收尾：父项（下一条更深缩进）升级为分组，子项归入该分组 ──
    for idx, (indent, title, detail, text, heading_group) in enumerate(raw_items):
        # 回到父项同级或更浅 → 该父项分组作用域结束
        while parent_groups and indent <= parent_groups[-1][0]:
            parent_groups.pop()
        has_child = idx + 1 < len(raw_items) and raw_items[idx + 1][0] > indent
        is_group = has_child and (not detail or len(detail) <= 4)
        if is_group:
            parent_groups.append((indent, _group_name(title)))
            continue
        group = parent_groups[-1][1] if parent_groups else heading_group
        items.append(_make_item(
            kind=kind, title=title, detail=detail, group=group,
            text=text,
        ))

    return items[:MAX_ITEMS_PER_DOC]


def _group_name(text: str) -> str:
    """父项升级为分组名时，去掉「（第1-5章）」这类章号脚注。"""
    return re.sub(r"[（(]\s*第?\s*[0-9零〇一二两三四五六七八九十百千]{1,6}\s*章[^）)]*[）)]", "", text or "").strip() or (text or "").strip()


def _clean_detail(text: str) -> str:
    """详情清洗：去掉拆分标题时遗留的尾括号与开头标点。"""
    text = (text or "").strip()
    text = re.sub(r"^[，,。；;、）)\]】\s]+", "", text)
    text = re.sub(r"[（(【\[]\s*$", "", text)
    text = re.sub(r"[）)\]】]\s*$", "", text)
    text = re.sub(r"[（(【\[]\s*第?\s*[0-9零〇一二两三四五六七八九十百千]{1,6}\s*章?\s*$", "", text)
    return text.strip()


def _make_item(kind: str, title: str, detail: str, group: str, text: str,
               status_text: str = "", plant_text: str = "",
               resolve_text: str = "", owner: str = "") -> dict:
    """把解析到的原始字段归一成条目 dict。"""
    title = _clean_md(title).strip(" 　:：,，.。;；、-—")
    detail = _clean_detail(_clean_md(detail))
    status = detect_status(status_text) or detect_status(f"{detail} {text}")

    explicit_plant = extract_chapters(plant_text)
    explicit_resolve = extract_chapters(resolve_text)
    refs = []
    if not explicit_plant:
        refs = extract_chapters(f"{title} {detail} {text}")

    plant = explicit_plant[0] if explicit_plant else 0
    resolve = explicit_resolve[-1] if explicit_resolve else 0
    if not plant and refs:
        plant = refs[0]
    if not resolve and refs and kind != ThreadKind.TIMELINE.value:
        later = [n for n in refs if n > plant] if plant else refs
        if later:
            resolve = later[-1]
    # 时间线节点只有「发生章」
    if kind == ThreadKind.TIMELINE.value:
        resolve = 0
        plant = plant or (refs[0] if refs else 0)

    chapters: dict[str, int] = {}
    if plant:
        chapters["plant"] = plant
    if resolve and resolve != plant:
        chapters["resolve"] = resolve

    return {
        "kind": kind,
        "title": title,
        "detail": detail,
        "group": group,
        "status": status.value if status else "",
        "chapters": chapters,
        "owner": _clean_md(owner),
        "location": "",
    }


# ── 台账 ──

def _thread_id(kind: str, title: str, seq: int = 0) -> str:
    raw = f"{kind}:{title}" + (f"#{seq}" if seq else "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _normalize_title(text: str) -> str:
    return re.sub(r"[\s\u3000，。,.、；;：:！!？?“”\"'‘’（）()【】\[\]—–\-~·…]+", "", text or "")


def _looks_same(a: str, b: str) -> bool:
    """两条文本是否指同一件事：去标点后互相包含（短串 ≥3 字）。"""
    na, nb = _normalize_title(a), _normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long_ = sorted((na, nb), key=len)
    return len(short) >= 3 and short in long_


class StoryLedger:
    """项目级故事线台账（四类线程 + 进度统计 + 规划/记忆/世界状态对账）。"""

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self._path = self.project_dir / STORY_THREADS_FILE
        self.threads: dict[str, StoryThread] = {}
        self.meta: dict = {}

    # ── 读写 ──

    def load(self) -> "StoryLedger":
        if self._path.exists():
            try:
                import json
                data = json.loads(self._path.read_text(encoding="utf-8"))
                raw = data.get("threads") or []
                self.meta = data.get("meta") or {}
                for item in raw:
                    thread = StoryThread.from_dict(item)
                    if thread.id and thread.title:
                        self.threads[thread.id] = thread
                logger.debug("故事线台账已加载: %d 条", len(self.threads))
            except (ValueError, OSError) as e:
                logger.warning("故事线台账加载失败（按空台账处理）: %s", e)
                self.threads = {}
                self.meta = {}
        else:
            self.threads = {}
            self.meta = {}
        return self

    def save(self) -> None:
        from .project_io import write_json
        write_json(self._path, {
            "version": 1,
            "meta": self.meta,
            "threads": [t.to_dict() for t in self.threads.values()],
        })

    def __len__(self) -> int:
        return len(self.threads)

    def __bool__(self) -> bool:
        # 显式真值：空台账仍是可用对象（不能因 __len__==0 被判 False）
        return True

    # ── 查询 ──

    def all(self, kind: str | None = None, include_inactive: bool = False) -> list[StoryThread]:
        items = [t for t in self.threads.values() if include_inactive or t.active]
        if kind:
            items = [t for t in items if t.kind == kind]
        return sorted(items, key=self._sort_key)

    @staticmethod
    def _sort_key(t: StoryThread):
        order = {"timeline": 0, "main": 1, "subplot": 2, "foreshadow": 3}
        chapter = t.planted_chapter or 999999
        return (order.get(t.kind, 9), t.group, chapter, t.title)

    def get(self, thread_id: str) -> StoryThread | None:
        return self.threads.get(thread_id)

    def find_by_title(self, title: str, kind: str | None = None) -> StoryThread | None:
        norm = _normalize_title(title)
        if not norm:
            return None
        for thread in self.threads.values():
            if kind and thread.kind != kind:
                continue
            if _normalize_title(thread.title) == norm:
                return thread
        return None

    # ── 手工编辑 ──

    def add(self, kind: str, title: str, detail: str = "", group: str = "",
            status: str = ThreadStatus.PLANNED.value, plant: int = 0,
            resolve: int = 0, owner: str = "", source: str = "manual") -> StoryThread:
        title = (title or "").strip()
        if not title:
            raise ValueError("故事线标题不能为空")
        seq = 0
        thread_id = _thread_id(kind, title, seq)
        while thread_id in self.threads:
            seq += 1
            thread_id = _thread_id(kind, title, seq)
        chapters: dict[str, int] = {}
        if plant:
            chapters["plant"] = int(plant)
        if resolve:
            chapters["resolve"] = int(resolve)
        thread = StoryThread(
            id=thread_id, kind=kind, title=title, detail=detail,
            status=status, group=group, owner=owner, chapters=chapters,
            source=source,
        )
        self.threads[thread.id] = thread
        return thread

    def update(self, thread_id: str, fields: dict, mark_user_edited: bool = True) -> StoryThread | None:
        """更新字段；默认记入 user_edited，后续自动对账不覆盖。"""
        thread = self.threads.get(thread_id)
        if not thread:
            return None
        allowed = {"title", "detail", "status", "group", "owner", "location",
                   "notes", "chapters", "happened", "active"}
        for key, value in (fields or {}).items():
            if key not in allowed:
                continue
            if key == "chapters":
                merged = dict(thread.chapters or {})
                for ck, cv in (value or {}).items():
                    try:
                        cv_int = int(cv or 0)
                    except (TypeError, ValueError):
                        continue
                    if cv_int > 0:
                        merged[ck] = cv_int
                    else:
                        merged.pop(ck, None)
                thread.chapters = merged
            elif key == "title":
                thread.title = str(value or "").strip() or thread.title
            elif key == "status":
                thread.status = str(value or thread.status)
            elif key == "happened":
                thread.happened = bool(value)
            elif key == "active":
                thread.active = bool(value)
            else:
                setattr(thread, key, str(value or ""))
            if mark_user_edited and key not in thread.user_edited:
                thread.user_edited.append(key)
        thread.updated_at = datetime.now().isoformat(timespec="seconds")
        return thread

    def remove(self, thread_id: str) -> bool:
        return self.threads.pop(thread_id, None) is not None

    # ── 时钟与进度 ──

    def planning_volume_ranges(self) -> list[tuple[int, str, int, int]]:
        """从主线/大纲/时间线解析卷区间（实例内缓存）。"""
        cached = getattr(self, "_volume_ranges", None)
        if cached is not None:
            return cached
        from . import project_io

        target = 0
        try:
            target = int(project_io.load_meta(self.project_dir).get("target_chapters") or 0)
        except Exception:
            target = 0
        texts = []
        for name in ("主线.md", "大纲.md", "时间线.md"):
            path = self.project_dir / "planning" / name
            if path.exists():
                texts.append(project_io.read_md(path))
        self._volume_ranges = parse_volume_ranges("\n".join(texts), target)
        return self._volume_ranges

    def volume_for_chapter(self, chapter: int) -> str:
        """按章号找所属卷；优先复用台账里已有的卷名（避免同一卷两种写法）。"""
        if not chapter:
            return ""
        for ordinal, name, start, end in self.planning_volume_ranges():
            if not start or chapter < start:
                continue
            if end and chapter > end:
                continue
            same = [
                t.group for t in self.threads.values()
                if t.group and volume_ordinal(t.group) == ordinal
            ]
            if same:
                # 同一卷可能有两种写法（规划稿带章区间、时间线带主题），取用得最多的
                counts = Counter(same)
                return max(counts.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
            return name
        return ""

    def known_character_names(self) -> list[str]:
        """台账可识别的角色名：角色注册表 + 世界状态 + 人物设定。"""
        from . import project_io

        names: set[str] = set()
        try:
            from .characters.registry import CharacterRegistry
            for rec in CharacterRegistry(self.project_dir).all():
                if rec.name:
                    names.add(rec.name)
                names.update(a for a in (rec.aliases or []) if a)
        except Exception as e:
            logger.debug("读取角色注册表失败（忽略）: %s", e)
        try:
            from .world_state import WorldState
            gs = WorldState(self.project_dir)
            gs.load()
            names.update(gs.characters.keys())
        except Exception as e:
            logger.debug("读取世界状态角色失败（忽略）: %s", e)
        try:
            from .character_sim import parse_characters
            content = project_io.read_md(self.project_dir / "planning" / "人物设定.md")
            names.update(p.name for p in parse_characters(content) if p.name)
        except Exception as e:
            logger.debug("解析人物设定失败（忽略）: %s", e)
        return sorted(n for n in names if n and len(n) >= 2)

    def clock(self) -> tuple[int, int]:
        """(当前章号, 目标章数)。

        当前章号取已有章节最大号（最可靠的「写到哪了」），无章节时回退 meta.current_chapter。
        """
        from . import project_io

        current = 0
        chapters_dir = self.project_dir / project_io.CHAPTERS_DIR
        if chapters_dir.exists():
            for path in chapters_dir.iterdir():
                if not path.is_file():
                    continue
                m = project_io._CHAPTER_RE.match(path.name)
                if m and path.stat().st_size > 0:
                    try:
                        current = max(current, int(m.group(1)))
                    except ValueError:
                        continue
        total = 0
        try:
            meta = project_io.load_meta(self.project_dir)
            total = int(meta.get("target_chapters") or 0)
            if not current:
                current = int(meta.get("current_chapter") or 0)
        except Exception as e:  # 元数据坏掉不应阻断 UI
            logger.debug("读取项目 meta 失败: %s", e)
        return current, total

    def progress(self) -> StoryProgress:
        current, total = self.clock()
        p = StoryProgress(current_chapter=current, total_chapters=total)
        for thread in self.all():
            if thread.kind == ThreadKind.MAIN.value:
                p.main_total += 1
                if thread.status == ThreadStatus.DONE.value:
                    p.main_done += 1
            elif thread.kind == ThreadKind.SUBPLOT.value:
                p.subplot_total += 1
                if thread.status == ThreadStatus.DONE.value:
                    p.subplot_done += 1
            elif thread.kind == ThreadKind.FORESHADOW.value:
                p.foreshadow_total += 1
                if thread.status == ThreadStatus.DONE.value:
                    p.foreshadow_done += 1
                elif thread.is_overdue(current):
                    p.foreshadow_overdue += 1
            elif thread.kind == ThreadKind.TIMELINE.value:
                p.timeline_total += 1
                if thread.happened or thread.status == ThreadStatus.DONE.value:
                    p.timeline_happened += 1
        return p

    # ── 规划文档导入 ──

    def _doc_signature(self, kind: str) -> str:
        path = self.project_dir / "planning" / DOC_THREAD_KINDS[kind]
        try:
            if not path.exists():
                return ""
            stat = path.stat()
            return f"{int(stat.st_mtime)}:{stat.st_size}"
        except OSError:
            return ""

    def planning_changed(self) -> bool:
        """四份规划文档是否有变化（相对上次导入）。"""
        signatures = self.meta.get("planning_sigs") or {}
        return any(self._doc_signature(k) != signatures.get(k, "") for k in DOC_THREAD_KINDS)

    def sync_from_planning(self, force: bool = False) -> int:
        """把 planning/{时间线,主线,支线,伏笔}.md 导入台账。返回新增条数。

        已存在的条目只补空字段（不覆盖 UI 手工编辑），规划稿删掉的条目标 ``active=False``。
        """
        from . import project_io

        signatures = dict(self.meta.get("planning_sigs") or {})
        added = 0
        for kind, filename in DOC_THREAD_KINDS.items():
            signature = self._doc_signature(kind)
            if not force and signatures.get(kind, "") == signature:
                continue
            signatures[kind] = signature
            path = self.project_dir / "planning" / filename
            content = project_io.read_md(path) if path.exists() else ""
            parsed = parse_planning_doc(content, kind) if content else []

            existing = {t.id: t for t in self.threads.values() if t.source == "planning" and t.kind == kind}
            current_chapter, _total = self.clock()
            parsed_ids: set[str] = set()
            for seq, item in enumerate(parsed):
                base_id = _thread_id(kind, item["title"])
                thread_id = base_id
                bump = 0
                while thread_id in parsed_ids or (
                    thread_id in self.threads and self.threads[thread_id].source != "planning"
                ):
                    bump += 1
                    thread_id = _thread_id(kind, item["title"], bump)
                parsed_ids.add(thread_id)
                if thread_id in existing:
                    self._merge_imported(existing[thread_id], item, current_chapter)
                else:
                    thread = self._new_from_item(item, thread_id, current_chapter)
                    self.threads[thread.id] = thread
                    added += 1

            # 规划稿里已不存在的条目：软删除，保留手工备注
            for thread_id, thread in existing.items():
                if thread_id not in parsed_ids:
                    thread.active = False
                    thread.updated_at = datetime.now().isoformat(timespec="seconds")

        self.meta["planning_sigs"] = signatures
        self.meta["last_sync"] = datetime.now().isoformat(timespec="seconds")
        return added

    def _new_from_item(self, item: dict, thread_id: str, current_chapter: int = 0) -> StoryThread:
        chapters = dict(item.get("chapters") or {})
        status = item.get("status") or ThreadStatus.PLANNED.value
        if status == ThreadStatus.DONE.value:
            resolve = int(chapters.get("resolve") or 0)
            # 计划章还没写到 → 只是「待回收」，不是「已回收」
            if resolve and (not current_chapter or resolve > current_chapter):
                status = ThreadStatus.ACTIVE.value
        return StoryThread(
            id=thread_id,
            kind=item["kind"],
            title=item["title"],
            detail=item.get("detail", ""),
            status=status,
            group=item.get("group", ""),
            owner=item.get("owner", ""),
            chapters=chapters,
            source="planning",
            planned_status=item.get("status") or "",
        )

    def _merge_imported(self, thread: StoryThread, item: dict, current_chapter: int = 0) -> None:
        """导入合并：规划稿是「计划」，不能把已经在正文兑现的状态改回去。"""
        edited = set(thread.user_edited or [])
        if "detail" not in edited and item.get("detail"):
            thread.detail = _clean_detail(item["detail"])
        if "group" not in edited and item.get("group") and item.get("group") != thread.group:
            thread.group = item["group"]
        if "owner" not in edited and item.get("owner"):
            thread.owner = item["owner"]
        if "chapters" not in edited:
            for key, value in (item.get("chapters") or {}).items():
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    continue
                old = int(thread.chapters.get(key) or 0)
                if not old:
                    thread.chapters[key] = value
                elif key == "resolve":
                    # 回收章只向前推进，避免重复导入把已回收打回原状
                    thread.chapters[key] = min(old, value)
                elif key == "plant":
                    thread.chapters[key] = min(old, value) if old else value
        thread.planned_status = item.get("status") or thread.planned_status
        if "status" in edited:
            return
        planned = item.get("status") or ""
        if planned == ThreadStatus.DONE.value:
            resolve = int(thread.chapters.get("resolve") or 0)
            # 有章号就必须写到那一章才算真发生；无章号视为规划稿已经确认完成
            if resolve and current_chapter and resolve > current_chapter:
                thread.status = ThreadStatus.ACTIVE.value
            elif resolve and not current_chapter:
                thread.status = ThreadStatus.ACTIVE.value
            else:
                thread.status = ThreadStatus.DONE.value
        elif planned == ThreadStatus.ABANDONED.value:
            thread.status = ThreadStatus.ABANDONED.value
        elif planned == ThreadStatus.ACTIVE.value and thread.status == ThreadStatus.PLANNED.value:
            thread.status = ThreadStatus.ACTIVE.value
        elif thread.status == ThreadStatus.PLANNED.value and item.get("chapters"):
            thread.status = ThreadStatus.ACTIVE.value
        thread.active = True
        thread.updated_at = datetime.now().isoformat(timespec="seconds")

    # ── memory / world_state 对账 ──

    def reconcile(self) -> dict:
        """用记忆流水与世界快照推进台账状态。返回改动计数。"""
        from .memory import MemoryScratchpad
        from .world_state import WorldState

        current, _total = self.clock()
        stats = {"subplots": 0, "loops_new": 0, "loops_closed": 0, "timeline": 0}

        try:
            mem = MemoryScratchpad(self.project_dir)
        except Exception as e:
            logger.warning("读取记忆失败，跳过记忆对账: %s", e)
            mem = None

        if mem is not None:
            stats["subplots"] = self._reconcile_subplots(mem, current)
            loops = self._reconcile_loops(mem, current)
            stats["loops_new"], stats["loops_closed"] = loops

        try:
            gs = WorldState(self.project_dir)
            gs.load()
            stats["timeline"] = self._reconcile_timeline(gs)
        except Exception as e:
            logger.warning("读取大世界状态失败，跳过时间线对账: %s", e)

        # 伏笔：规划写明的回收章已经写过去（且规划状态就是已回收），补标已回收
        if current:
            for thread in self.all(ThreadKind.FORESHADOW.value):
                if thread.status == ThreadStatus.DONE.value:
                    continue
                if thread.planned_status != ThreadStatus.DONE.value:
                    continue
                resolve = thread.resolve_chapter
                passed = (0 < resolve <= current) if resolve else True
                if passed and "status" not in thread.user_edited:
                    thread.status = ThreadStatus.DONE.value
                    thread.updated_at = datetime.now().isoformat(timespec="seconds")
                    stats["loops_closed"] += 1

        # 兜底：已写过的章节对应的计划节点视为已发生（世界状态未必逐节点记录）
        if current:
            for thread in self.all(ThreadKind.TIMELINE.value):
                if thread.happened:
                    continue
                planned = thread.planted_chapter
                if planned and 0 < planned <= current:
                    thread.happened = True
                    if thread.status != ThreadStatus.DONE.value:
                        thread.status = ThreadStatus.DONE.value
                    thread.updated_at = datetime.now().isoformat(timespec="seconds")
                    stats["timeline"] += 1

            # 主线/支线：开写之后未完成的线一律进入「推进中」（未开始只适用于还没动笔）
            for thread in self.all():
                if thread.kind not in (ThreadKind.MAIN.value, ThreadKind.SUBPLOT.value):
                    continue
                if thread.status != ThreadStatus.PLANNED.value:
                    continue
                if "status" in thread.user_edited:
                    continue
                planted = thread.planted_chapter
                if not planted or planted <= current:
                    thread.status = ThreadStatus.ACTIVE.value
                    thread.updated_at = datetime.now().isoformat(timespec="seconds")

        # 人物/卷组：空字段按「正文提到的角色 + 章号所属卷」补齐，
        # 让 UI 里这两个字段有真实可选值，也让写作上下文能带上涉及人物。
        names = self.known_character_names()
        filled = 0
        for thread in self.all():
            if not thread.group:
                chapter = thread.planted_chapter or thread.resolve_chapter
                group = self.volume_for_chapter(chapter)
                if group:
                    thread.group = group
                    filled += 1
            if not thread.owner and "owner" not in thread.user_edited:
                owner = infer_owner(f"{thread.title} {thread.detail}", names)
                if owner:
                    thread.owner = owner
                    filled += 1
        if filled:
            stats["fields"] = filled

        if any(stats.values()):
            self.meta["last_sync"] = datetime.now().isoformat(timespec="seconds")
        return stats

    def _reconcile_subplots(self, mem, current: int) -> int:
        """memory.subplots：最新进度 → 支线状态与最近推进章。"""
        changed = 0
        rows = mem.get_active("subplots") or []
        latest: dict[str, dict] = {}
        for row in rows:
            subject = str(row.get("subject") or "").strip()
            if not subject:
                continue
            old = latest.get(subject)
            if old is None or int(row.get("source_chapter") or 0) >= int(old.get("source_chapter") or 0):
                latest[subject] = row
        for subject, row in latest.items():
            thread = self.find_by_title(subject, ThreadKind.SUBPLOT.value)
            if thread is None:
                continue
            chapter = int(row.get("source_chapter") or 0)
            touched = False
            chapters_edited = "chapters" in thread.user_edited
            if not chapters_edited and chapter and not thread.chapters.get("plant"):
                thread.chapters["plant"] = chapter
                touched = True
            if not chapters_edited and chapter and chapter > int(thread.chapters.get("resolve") or 0):
                thread.chapters["resolve"] = chapter
                touched = True
            value = str(row.get("value") or "")
            status = str((row.get("payload") or {}).get("status") or "")
            planned = detect_status(f"{status} {value}")
            if "status" in thread.user_edited:
                planned = None
            if planned in (ThreadStatus.DONE.value, ThreadStatus.ABANDONED.value):
                if thread.status != planned:
                    thread.status = planned
                    touched = True
            elif thread.status == ThreadStatus.PLANNED.value:
                thread.status = ThreadStatus.ACTIVE.value
                touched = True
            if touched:
                thread.updated_at = datetime.now().isoformat(timespec="seconds")
                changed += 1
        # 规划里没写、但正文已经推进的支线：补进台账（否则即兴支线永远不可见）
        for subject, row in latest.items():
            if self.find_by_title(subject, ThreadKind.SUBPLOT.value) is not None:
                continue
            status_text = str((row.get("payload") or {}).get("status") or "")
            value = str(row.get("value") or "")
            if not status_text and not any(w in value for w in _ACTIVE_WORDS + _PLAN_RESOLVE_WORDS):
                continue
            try:
                created = self.add(
                    kind=ThreadKind.SUBPLOT.value,
                    title=subject,
                    detail=value[:120],
                    status=ThreadStatus.ACTIVE.value,
                    plant=int(row.get("source_chapter") or 0),
                    source="memory",
                )
            except ValueError:
                continue
            created.user_edited = []
            changed += 1
        return changed

    def _reconcile_loops(self, mem, current: int) -> tuple[int, int]:
        """open_loops → 伏笔状态：活跃的在途伏笔入账，已回收的标记 DONE。"""
        new_count = closed = 0

        # 1) 活跃伏笔：规划里没写到的，也补进台账（否则伏笔清单会漏掉即兴埋设）
        for row in mem.get_active("open_loops") or []:
            value = str(row.get("value") or "").strip()
            subject = str(row.get("subject") or "").strip()
            if not value:
                continue
            existing = self.find_by_title(value, ThreadKind.FORESHADOW.value)
            if existing is None:
                existing = self.find_by_title(subject, ThreadKind.FORESHADOW.value)
            if existing is not None:
                if existing.status == ThreadStatus.PLANNED.value and "status" not in existing.user_edited:
                    existing.status = ThreadStatus.ACTIVE.value
                    existing.updated_at = datetime.now().isoformat(timespec="seconds")
                planted = int(row.get("source_chapter") or 0)
                if planted and "chapters" not in existing.user_edited and not existing.chapters.get("plant"):
                    existing.chapters["plant"] = planted
                continue
            try:
                thread = self.add(
                    kind=ThreadKind.FORESHADOW.value,
                    title=value[:60],
                    detail=subject or value,
                    status=ThreadStatus.ACTIVE.value,
                    plant=int(row.get("source_chapter") or 0),
                    source="memory",
                )
            except ValueError:
                continue
            thread.user_edited = []
            new_count += 1

        # 2) 已回收伏笔：story_facts「伏笔回收」→ 对应条目 DONE
        closed_rows = [
            row for row in mem.query("story_facts")
            if row.get("aspect") == "伏笔回收" and row.get("status") == "active"
        ]
        for row in closed_rows:
            subject = str(row.get("subject") or "")
            chapter = int(row.get("source_chapter") or 0)
            if not subject:
                continue
            target = self.find_by_title(subject, ThreadKind.FORESHADOW.value)
            if target is None:
                # 未解伏笔的关闭记录里 subject 可能是「第N章伏笔K·片段」
                for thread in self.all(ThreadKind.FORESHADOW.value):
                    if _looks_same(thread.title, subject) or _looks_same(thread.title, row.get("value", "")):
                        target = thread
                        break
            if target is None:
                continue
            touched = False
            if "status" not in target.user_edited and target.status != ThreadStatus.DONE.value:
                target.status = ThreadStatus.DONE.value
                touched = True
            if chapter and "chapters" not in target.user_edited:
                old = int(target.chapters.get("resolve") or 0)
                if not old or chapter < old:
                    target.chapters["resolve"] = chapter
                    touched = True
            if touched:
                target.updated_at = datetime.now().isoformat(timespec="seconds")
                closed += 1
        return new_count, closed

    def _reconcile_timeline(self, gs) -> int:
        """world_state.timeline（实际发生）→ 时间线节点标记已发生。"""
        changed = 0
        for event in gs.timeline or []:
            if not isinstance(event, dict):
                continue
            text = str(event.get("event") or "").strip()
            if not text:
                continue
            chapter = int(event.get("chapter") or 0)
            match = None
            for thread in self.all(ThreadKind.TIMELINE.value):
                if _looks_same(thread.title, text) or _looks_same(thread.detail, text):
                    match = thread
                    break
            if match is None and chapter:
                # 章号相近（±2）且内容有交集，视为同一节点
                for thread in self.all(ThreadKind.TIMELINE.value):
                    planted = thread.planted_chapter
                    if planted and abs(planted - chapter) <= 2 and (
                        _looks_same(thread.title, text) or _looks_same(thread.detail, text)
                    ):
                        match = thread
                        break
            if match is None:
                match = self.add(
                    kind=ThreadKind.TIMELINE.value,
                    title=text[:40],
                    detail=text,
                    status=ThreadStatus.DONE.value,
                    plant=chapter,
                    source="world_state",
                )
                match.happened = True
                match.location = str(event.get("location") or "")
                changed += 1
                continue
            touched = False
            if not match.happened:
                match.happened = True
                touched = True
            if match.status != ThreadStatus.DONE.value and "status" not in match.user_edited:
                match.status = ThreadStatus.DONE.value
                touched = True
            if chapter and "chapters" not in match.user_edited:
                # 实际发生章优先：规划写第 10 章、实际第 12 章发生，以正文为准
                old = int(match.chapters.get("plant") or 0)
                if not old or abs(chapter - old) > 1:
                    match.chapters["plant"] = chapter
                    touched = True
            if not match.location and event.get("location"):
                match.location = str(event["location"])
                touched = True
            if touched:
                changed += 1
        return changed

    # ── 反哺：写作上下文 ──

    def build_context_block(self, chapter: int = 0, max_items: int = 14) -> str:
        """写作上下文注入块：进行中的主线/支线 + 待回收伏笔 + 最近时间线。"""
        current, _ = self.clock()
        n = chapter or current
        lines: list[str] = []

        def owner_tag(t: StoryThread) -> str:
            return f"〔{t.owner}〕" if t.owner else ""

        mains = [t for t in self.all(ThreadKind.MAIN.value)
                 if t.status in (ThreadStatus.ACTIVE.value, ThreadStatus.PLANNED.value)][:4]
        if mains:
            lines.append("【主线进行中】")
            for t in mains:
                mark = "▶" if t.status == ThreadStatus.ACTIVE.value else "·"
                lines.append(f"{mark} {t.title}{owner_tag(t)}（{t.chapter_text()}）"
                             f"{('：' + t.detail[:60]) if t.detail else ''}")

        subs = [t for t in self.all(ThreadKind.SUBPLOT.value)
                if t.status in (ThreadStatus.ACTIVE.value, ThreadStatus.PLANNED.value)][:4]
        if subs:
            lines.append("【支线推进中】")
            for t in subs:
                lines.append(f"· {t.title}{owner_tag(t)}（{t.chapter_text()}）"
                             f"{('：' + t.detail[:60]) if t.detail else ''}")

        loops = [t for t in self.all(ThreadKind.FORESHADOW.value)
                 if t.status in (ThreadStatus.ACTIVE.value, ThreadStatus.PLANNED.value)]
        if loops:
            loops.sort(key=lambda x: (not x.is_overdue(n), -(n - x.planted_chapter)))
            lines.append("【待回收伏笔】")
            for t in loops[:6]:
                if t.is_overdue(n):
                    lines.append(f"⚠ {t.title}{owner_tag(t)}（第{t.planted_chapter}章埋，"
                                 f"已过{n - t.planted_chapter}章，优先回收）")
                else:
                    lines.append(f"· {t.title}{owner_tag(t)}（{t.chapter_text()}）")

        timeline = [t for t in self.all(ThreadKind.TIMELINE.value) if t.happened][-5:]
        if timeline:
            lines.append("【时间线·最近已发生】")
            for t in timeline:
                lines.append(f"· {t.chapter_text()} {t.title}{owner_tag(t)}")

        if not lines:
            return ""
        body = "\n".join(lines[:max_items + 8])
        return "=== 故事线台账（反哺自规划/记忆/世界状态） ===\n" + body[:2600]

    def status_line(self) -> str:
        return self.progress().headline()


# ── 便捷入口 ──

def sync_story_threads(project_dir: Path, force_planning: bool = False) -> StoryLedger:
    """工作流/UI 共用的同步入口：规划导入 + 记忆/世界状态对账 + 落盘。"""
    ledger = StoryLedger(project_dir).load()
    ledger.sync_from_planning(force=force_planning)
    ledger.reconcile()
    try:
        ledger.save()
    except OSError as e:
        logger.warning("故事线台账保存失败: %s", e)
    return ledger


def load_story_threads(project_dir: Path) -> StoryLedger:
    """只读加载（不触发对账），供上下文组装等热路径使用。"""
    return StoryLedger(project_dir).load()


__all__ = [
    "STORY_THREADS_FILE", "DOC_THREAD_KINDS", "LOOP_OVERDUE_CHAPTERS",
    "ThreadKind", "ThreadStatus", "KIND_LABELS", "STATUS_LABELS",
    "StoryThread", "StoryProgress", "StoryLedger",
    "cn_to_int", "extract_chapters", "detect_status", "parse_planning_doc",
    "sync_story_threads", "load_story_threads",
]
