"""正典投影 — 用事件日志重建世界状态快照。

world_state.json 只是缓存；`world_state_update` 事件才是历史。重建时从空状态
按章号顺序重放事件，任何手动篡改都能被下一次投影纠正。
"""
from __future__ import annotations

from pathlib import Path

from ..logger import get_logger
from ..world_state import WorldState
from .events import CanonLog

logger = get_logger(__name__)


def project_world_state(project_dir: Path, save: bool = True) -> dict:
    """按 CanonLog 重放 world_state_update 事件，返回投影结果。"""
    canon = CanonLog(project_dir)
    ws = WorldState(project_dir)
    ws._data = ws._default_state()  # noqa: SLF001 — 投影需要从空状态开始
    for event in canon.all():
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
    return ws._data


def world_state_event_count(project_dir: Path) -> int:
    return sum(1 for e in CanonLog(project_dir).all() if e.type == "world_state_update")
