"""正典事件日志 — append-only，角色知识的唯一来源。

设计要点（对应“角色不能开上帝视角”）：
- 事件一旦写入不可修改/删除；纠错只能追加补偿事件；
- 每个事件带 `visibility` 与 `known_by`，角色视图只取自己知道的；
- `hidden` 字段（真实意图/秘密效果）永远不进别人的视图；
- 事件带 `prev_hash` 哈希链，便于审计与检测篡改。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..logger import get_logger

logger = get_logger(__name__)

CANON_DIR = "canon"
EVENTS_FILE = "events.jsonl"

VISIBILITIES = ("private", "participants", "public", "world")


@dataclass
class CanonEvent:
    id: str = ""
    chapter: int = 0
    scene: str = ""
    type: str = "event"  # action / dialogue / reveal / death / resurrect / knowledge / promotion
    actor: str = ""
    targets: list[str] = field(default_factory=list)
    observable: str = ""
    hidden: str = ""  # 仅 actor 自己知道；绝不进别人的视图
    visibility: str = "participants"
    known_by: list[str] = field(default_factory=list)
    causes: list[str] = field(default_factory=list)
    timeline: str = "main"
    payload: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {k: v for k, v in data.items() if v not in ("", [], {}, None)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanonEvent":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def _event_hash(event: CanonEvent) -> str:
    payload = event.to_dict()
    payload.pop("id", None)
    # prev_hash 参与哈希：同一内容在不同链位置也会得到不同 id
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class CanonLog:
    """项目级 append-only 事件日志（JSONL + hash chain）。"""

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / CANON_DIR / EVENTS_FILE
        self._events: list[CanonEvent] = []
        self._loaded = False

    # ── 读 ──

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self._events.append(CanonEvent.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning("正典事件行损坏，已跳过: %s", e)
        except OSError as e:
            logger.error("正典事件读取失败: %s", e)

    def all(self) -> list[CanonEvent]:
        self._load()
        return list(self._events)

    def get(self, event_id: str) -> CanonEvent | None:
        self._load()
        return next((e for e in self._events if e.id == event_id), None)

    def for_chapter(self, chapter: int) -> list[CanonEvent]:
        self._load()
        return [e for e in self._events if e.chapter == chapter]

    def has_percepts(self, chapter: int) -> bool:
        """该章是否已抽过感知事件（用于幂等重跑）。"""
        return any(
            (e.payload or {}).get("source") == "percept"
            for e in self.for_chapter(chapter)
        )

    def known_to(self, character_id: str, up_to_chapter: int,
                 names: Iterable[str] = ()) -> list[CanonEvent]:
        """角色视角可见事件：本人事件 + known_by 包含他 + 章节不晚于当前。

        `known_by` 允许写角色 ID，也允许写姓名/别名（创作时更自然）；
        `names` 由调用方传入该角色的姓名与别名。
        """
        self._load()
        keys = {character_id, *[n for n in names if n]}
        out: list[CanonEvent] = []
        for e in self._events:
            if e.chapter > up_to_chapter:
                continue
            if e.actor in keys:
                out.append(e)
            elif any(k in e.known_by for k in keys):
                out.append(e)
        return out

    def verify_chain(self) -> bool:
        """校验 prev_hash 链接与事件内容哈希。"""
        self._load()
        prev = ""
        for e in self._events:
            if e.prev_hash != prev:
                logger.warning("正典哈希链断裂于 %s", e.id)
                return False
            if e.id != f"evt_{_event_hash(e)}":
                logger.warning("正典事件内容被改动: %s", e.id)
                return False
            prev = e.id
        return True

    # ── 写 ──

    def append(self, event: CanonEvent | dict[str, Any]) -> CanonEvent:
        """追加事件；不允许更新/删除。"""
        self._load()
        ev = event if isinstance(event, CanonEvent) else CanonEvent.from_dict(event)
        if ev.chapter < 0:
            raise ValueError("chapter 不能为负")
        ev.prev_hash = self._events[-1].id if self._events else ""
        ev.id = ev.id or f"evt_{_event_hash(ev)}"
        if any(e.id == ev.id for e in self._events):
            raise ValueError(f"事件 id 重复: {ev.id}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
        self._events.append(ev)
        return ev

    def append_many(self, events: Iterable[CanonEvent | dict[str, Any]]) -> list[CanonEvent]:
        return [self.append(e) for e in events]


def seed_event(
    chapter: int,
    scene: str,
    actor: str,
    observable: str,
    known_by: Iterable[str],
    *,
    hidden: str = "",
    visibility: str = "participants",
    event_type: str = "action",
    targets: Iterable[str] = (),
    payload: dict[str, Any] | None = None,
) -> CanonEvent:
    """构造事件的便捷函数（不写盘，交给 CanonLog.append）。"""
    return CanonEvent(
        chapter=chapter,
        scene=scene,
        type=event_type,
        actor=actor,
        targets=list(targets),
        observable=observable,
        hidden=hidden,
        visibility=visibility,
        known_by=list(known_by),
        payload=payload or {},
    )


__all__ = ["CanonEvent", "CanonLog", "seed_event", "EVENTS_FILE"]
