"""正典投影：world_state 可以从事件日志重建，手动篡改会被覆盖。"""
from __future__ import annotations

from pathlib import Path

from src.novel_writer.core.canon.events import CanonEvent, CanonLog
from src.novel_writer.core.canon.projection import project_world_state
from src.novel_writer.core.world_state import WorldState


def test_project_world_state_replays_updates(tmp_path: Path):
    canon = CanonLog(tmp_path)
    canon.append(CanonEvent(chapter=1, type="world_state_update", actor="system",
                            payload={"update": {"characters": {"林牧": {"cultivation": {"level": "炼气一层"}}}}}))
    canon.append(CanonEvent(chapter=2, type="world_state_update", actor="system",
                            payload={"update": {"characters": {"林牧": {"location": "药圃"}},
                                                "items": {"new": [{"name": "青霜剑"}]}}}))

    data = project_world_state(tmp_path)
    assert data["characters"]["林牧"]["location"] == "药圃"
    assert "青霜剑" in data["items_catalog"]

    # 手动篡改快照，再投影会恢复
    ws = WorldState(tmp_path); ws.load()
    ws.characters["林牧"]["location"] = "错误地点"
    ws.save()
    project_world_state(tmp_path)
    assert WorldState(tmp_path).load()["characters"]["林牧"]["location"] == "药圃"
