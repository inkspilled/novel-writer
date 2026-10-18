"""正典投影 — 用事件日志重建世界状态快照。

world_state.json 只是缓存；`world_state_update` 事件才是历史。
带 checkpoint：第一次全量重放，之后只重放 checkpoint 之后的新事件。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..logger import get_logger
from ..world_state import WorldState
from .events import CanonLog

logger = get_logger(__name__)

CHECKPOINT_FILE = "canon/world_state.checkpoint.json"


def checkpoint_path(project_dir: Path) -> Path:
    return Path(project_dir) / CHECKPOINT_FILE


def world_state_event_count(project_dir: Path) -> int:
    return sum(1 for e in CanonLog(project_dir).all() if e.type == "world_state_update")


def _read_checkpoint(project_dir: Path) -> dict | None:
    path = checkpoint_path(project_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def project_world_state(project_dir: Path, save: bool = True) -> dict:
    """增量重放 world_state_update 事件，返回投影结果。"""
    canon = CanonLog(project_dir)
    events = canon.all()
    ws = WorldState(project_dir)
    cp = _read_checkpoint(project_dir)
    start = 0
    if cp:
        count = int(cp.get("event_count") or 0)
        last_id = str(cp.get("last_event_id") or "")
        state = cp.get("world_state")
        valid = (
            0 <= count <= len(events)
            and isinstance(state, dict)
            and (count == 0 or (events and events[count - 1].id == last_id))
        )
        if valid:
            ws._data = state  # noqa: SLF001 — checkpoint 就是快照
            start = count
            logger.debug("世界状态从 checkpoint 续放: %d/%d", start, len(events))
        else:
            logger.warning("世界状态 checkpoint 失效，回退全量重放")

    if start == 0:
        ws._data = ws._default_state()  # noqa: SLF001 — 投影从空状态开始
    for event in events[start:]:
        if event.type != "world_state_update":
            continue
        update = (event.payload or {}).get("update")
        if isinstance(update, dict):
            try:
                ws.apply_llm_update(update)
            except Exception as e:
                logger.warning("重放世界状态事件 %s 失败: %s", event.id, e)

    if save:
        ws.save()
        try:
            from .. import project_io
            project_io.write_json(checkpoint_path(project_dir), {
                "version": 1,
                "event_count": len(events),
                "last_event_id": events[-1].id if events else "",
                "world_state": ws._data,  # noqa: SLF001
            })
        except Exception as e:
            logger.warning("世界状态 checkpoint 写入失败: %s", e)
    return ws._data
