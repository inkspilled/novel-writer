"""写后沉淀 → memory.timeline：时间线不能只存在 world_state 里。"""
from __future__ import annotations

from src.novel_writer.core.memory import MemoryScratchpad
from src.novel_writer.core.world_state import WorldState
from src.novel_writer.core.workflow.sediment import sediment_chapter


def test_sediment_syncs_chapter_timeline_into_memory(tmp_path):
    gs = WorldState(tmp_path)
    gs.load()
    gs.add_timeline_event(1, "林乐天击败许承业", "擂台")
    gs.add_timeline_event(2, "赵无极查阅档案", "书房")
    gs.save()

    sediment_chapter(tmp_path, 1, "# 第一章 决战\n\n林乐天在擂台上击败了许承业。")

    mem = MemoryScratchpad(tmp_path)
    items = mem.get_active("timeline")
    assert items
    assert any("击败许承业" in item.get("value", "") for item in items)
    assert all("赵无极" not in item.get("value", "") for item in items)
