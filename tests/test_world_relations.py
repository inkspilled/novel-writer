"""人物-地点-物品多对多关系。"""
from pathlib import Path

from src.novel_writer.core.world_state import WorldState


def test_location_occupants_and_item_positions(tmp_path: Path):
    gs = WorldState(tmp_path); gs.load()
    gs.add_location("药圃", {"type": "宗门", "description": "灵草园"})
    gs.add_character("林牧", {"location": "药圃"})
    gs.add_character("楚夜", {"location": "山门"})
    gs.add_item("青霜剑", {"type": "法器", "location": "药圃"})
    gs.add_item("令牌", {"type": "信物"})
    gs.give_item("林牧", "令牌", gs.get_item("令牌"))

    assert gs.characters_at("药圃") == ["林牧"]
    assert gs.items_at("药圃") == ["青霜剑"]
    assert gs.item_holder("令牌") == "林牧"
    assert gs.get_item("令牌")["location"] == "药圃"

    gs.set_item_position("青霜剑", holder="楚夜")
    assert gs.item_holder("青霜剑") == "楚夜"
    assert gs.get_item("青霜剑")["location"] == "山门"
    assert "青霜剑" not in gs.items_at("药圃")
