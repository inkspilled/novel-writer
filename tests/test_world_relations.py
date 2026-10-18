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


def test_location_move_records_ledger(tmp_path: Path):
    gs = WorldState(tmp_path); gs.load()
    gs.apply_llm_update({"characters": {"林牧": {"location": "药圃"}}}, chapter=1)
    gs.apply_llm_update({"characters": {"林牧": {"location": "山门"}}}, chapter=2)

    actions = [(e["chapter"], e["character"], e["action"], e["location"]) for e in gs.location_log]
    assert (1, "林牧", "enter", "药圃") in actions
    assert (2, "林牧", "leave", "药圃") in actions
    assert (2, "林牧", "enter", "山门") in actions


def test_location_page_shows_ledger(tmp_path):
    from PySide6.QtWidgets import QApplication
    from src.novel_writer.ui.character.world_page import WorldPage

    app = QApplication.instance() or QApplication([])

    gs = WorldState(tmp_path); gs.load()
    gs.apply_llm_update({"characters": {"林牧": {"location": "药圃"}}}, chapter=1)
    gs.apply_llm_update({"characters": {"林牧": {"location": "山门"}}}, chapter=2)
    gs.save()
    page = WorldPage(); page.set_project_dir(tmp_path)
    page.list.setCurrentRow(2); app.processEvents()
    names = [page.location_page.list.item(i).text().split("   ")[0] for i in range(page.location_page.list.count())]
    page.location_page.list.setCurrentRow(names.index("药圃")); app.processEvents()
    text = page.location_page.log.toPlainText()
    assert "林牧" in text and "离开" in text
    page.close()
