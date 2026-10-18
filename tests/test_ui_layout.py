"""主窗口布局回归：两级导航、专注模式、状态条。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.novel_writer.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_layout_and_focus_mode(qapp):
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    qapp.processEvents()

    assert w.editor.tab_bar.count() == 3
    assert w.editor.planning_list.count() == 9
    assert w.splitter.count() == 3

    w._toggle_focus_mode(True)
    qapp.processEvents()
    assert w._focus_mode is True
    assert not w.sidebar.isVisible()
    assert not w.agent_panel.isVisible()
    assert w._focus_action.isChecked()

    w._toggle_focus_mode(False)
    qapp.processEvents()
    assert w._focus_mode is False
    assert w.sidebar.isVisible()
    assert w.agent_panel.isVisible()
    assert not w._focus_action.isChecked()
    w.close()


def test_agent_strip_caps_at_two_rows(qapp):
    from src.novel_writer.ui.agent.office_scene import OfficeScene
    from PySide6.QtCore import Qt

    office = OfficeScene()
    office.setup_agents({f"agent{i}": {"title": f"角色{i}"} for i in range(12)}, {})
    assert office.height() == 182  # 两行上限，不无限长高
    assert office._scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded

    office.setup_agents({f"agent{i}": {"title": f"角色{i}"} for i in range(3)}, {})
    assert office.height() == 104
    assert office._scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_character_dialog_lists_records(qapp, tmp_path):
    from src.novel_writer.core.characters.registry import CharacterRegistry, CharacterTier
    from src.novel_writer.ui.character.character_dialog import CharacterDialog

    reg = CharacterRegistry(tmp_path)
    reg.create("林牧", chapter=1, tier=CharacterTier.CORE, auto_sim=True)
    dlg = CharacterDialog(tmp_path)
    assert dlg.list.count() == 1
    assert dlg.name_label.text() == "林牧"
    assert dlg.auto_sim.isChecked()
    dlg.close()


def test_workflow_bar_usage_label(qapp):
    from src.novel_writer.ui.workflow.workflow_bar import WorkflowMiniBar
    bar = WorkflowMiniBar()
    bar.set_usage({"total_tokens": 1234})
    assert bar._usage_label.isVisibleTo(bar)
    assert "1.2k tok" in bar._usage_label.text()
    bar.set_usage({})
    assert not bar._usage_label.isVisible()


def test_chat_db_is_opt_in(qapp, tmp_path):
    from src.novel_writer.ui.agent.agent_panel import AgentPanel

    panel = AgentPanel()
    panel.set_config({"save_chat_history": False})
    panel.set_project_db(tmp_path)
    assert panel._chat_db is None
    panel.add_user_message("hello")
    panel.save_history()
    assert not (tmp_path / "chat.db").exists()

    panel2 = AgentPanel()
    panel2.set_config({"save_chat_history": True})
    panel2.set_project_db(tmp_path)
    assert panel2._chat_db is not None
    panel2.add_user_message("hello")
    panel2.save_history()
    assert (tmp_path / "chat.db").exists()
    panel.close(); panel2.close()


def test_character_page_loads_project_roster(qapp, tmp_path):
    from src.novel_writer.core.characters.registry import CharacterRegistry, CharacterTier
    from src.novel_writer.ui.character.character_page import CharacterPage

    reg = CharacterRegistry(tmp_path)
    reg.create("林牧", chapter=1, tier=CharacterTier.CORE, auto_sim=True)
    page = CharacterPage()
    page.set_project_dir(tmp_path)
    assert page.list.count() == 1
    assert page.name_label.text() == "林牧"
    assert page.auto_sim.isChecked()
    page.close()


def test_free_chat_is_collapsed_by_default(qapp):
    from src.novel_writer.ui.agent.agent_panel import AgentPanel

    panel = AgentPanel()
    assert panel.scroll_area.isHidden()
    assert panel._input_area.isHidden()
    assert not panel.btn_chat_toggle.isChecked()

    panel._set_chat_expanded(True)
    assert not panel.scroll_area.isHidden()
    assert not panel._input_area.isHidden()
    assert panel.btn_chat_toggle.isChecked()

    panel._set_chat_expanded(False)
    assert panel.scroll_area.isHidden()
    assert panel._input_area.isHidden()
    panel.close()


def test_editor_streaming_preview(qapp):
    from src.novel_writer.ui.editor.editor_panel import EditorPanel

    editor = EditorPanel()
    editor.start_stream_chapter(1)
    editor.append_stream_chapter(1, "第一段。")
    editor.append_stream_chapter(1, "第二段。")
    assert "第一段。第二段。" in editor.content_edit.toPlainText()
    editor.end_stream_chapter()
    assert not editor.content_edit.isReadOnly()
    editor.close()


def test_world_page_items_locations_relations(qapp, tmp_path):
    from src.novel_writer.core.world_state import WorldState
    from src.novel_writer.ui.character.world_page import WorldPage

    gs = WorldState(tmp_path); gs.load()
    gs.add_location("药圃", {"type": "宗门", "description": "灵草园"})
    gs.add_character("林牧", {"location": "药圃"})
    gs.add_item("青霜剑", {"type": "法器", "location": "药圃"})
    gs.save()

    page = WorldPage(); page.set_project_dir(tmp_path)
    assert page.item_page.list.count() == 1
    assert page.location_page.list.count() == 1
    page.location_page.list.setCurrentRow(0); qapp.processEvents()
    assert "林牧" in page.location_page.occupants.text()
    assert "青霜剑" in page.location_page.items.text()
    page.close()
