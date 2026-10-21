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

    assert w.editor.tab_bar.count() == 4
    assert [w.editor.tab_bar.tabText(i) for i in range(4)] == ["正文", "规划", "世界", "写作台"]
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


def test_world_menu_has_storyline_page(qapp, tmp_path):
    """世界菜单 = 人物/物品/地点/故事线 四页，故事线是第四个。"""
    from src.novel_writer.core.world_state import WorldState
    from src.novel_writer.ui.character.world_page import WorldPage

    gs = WorldState(tmp_path); gs.load(); gs.save()
    page = WorldPage()
    page.set_project_dir(tmp_path)
    assert page.list.count() == 4
    labels = [page.list.item(i).text() for i in range(page.list.count())]
    assert labels == ["人物", "物品", "地点", "故事线"]
    assert page.stack.count() == 4
    assert page.stack.widget(3) is page.storyline_page
    page.select(3)
    qapp.processEvents()
    assert page.stack.currentWidget() is page.storyline_page
    page.close()


def test_storyline_page_shows_progress_and_threads(qapp, tmp_path):
    """故事线页：KPI 卡/树/详情/双击跳章 全链路。"""
    from src.novel_writer.core.story_threads import sync_story_threads
    from src.novel_writer.ui.character.storyline_page import StorylinePage

    (tmp_path / "planning").mkdir()
    (tmp_path / "chapters").mkdir()
    (tmp_path / "meta.json").write_text('{"target_chapters": 50}', encoding="utf-8")
    for n in (1, 2):
        (tmp_path / "chapters" / f"{n}_第{n}章.txt").write_text("正文", encoding="utf-8")
    (tmp_path / "planning" / "主线.md").write_text(
        "# 主线\n## 第一卷\n- 拜入宗门（第1章）\n- 禁地试炼（第2章，进行中）\n", encoding="utf-8")
    (tmp_path / "planning" / "伏笔.md").write_text(
        "# 伏笔\n- 剑灵（第1章埋设，第20章回收）\n", encoding="utf-8")
    sync_story_threads(tmp_path)

    page = StorylinePage()
    page.set_project_dir(tmp_path)
    assert "第 2 章 / 50" in page.headline.text()
    assert page.tree.topLevelItemCount() == 2  # 主线 + 伏笔
    assert page.cards["main"].value.text() == "0 / 2"
    assert page.cards["foreshadow"].value.text() == "0 / 1"

    main_group = page.tree.topLevelItem(0)
    thread_item = main_group.child(0).child(0)
    page.tree.setCurrentItem(thread_item)
    qapp.processEvents()
    assert page.edit_title.text() == "拜入宗门"

    jumps: list[int] = []
    page.chapter_activate_requested.connect(jumps.append)
    page._on_tree_activated(thread_item, 0)
    assert jumps == [1]

    # 手工标记完成 → 落盘 → 卡片与标题同步
    page.edit_status.setCurrentIndex(page.edit_status.findData("done"))
    page._save_detail()
    qapp.processEvents()
    assert page.cards["main"].value.text() == "1 / 2"
    assert "主线 1/2" in page.headline.text()
    page.close()


def test_storyline_empty_project_and_overdue_filter(qapp, tmp_path):
    """空项目给出引导文案；伏笔超期可筛选出来。"""
    from src.novel_writer.core.story_threads import sync_story_threads
    from src.novel_writer.ui.character.storyline_page import StorylinePage

    empty = StorylinePage()
    empty.set_project_dir(tmp_path)
    assert "还没有故事线" in empty.headline.text()
    empty.close()

    (tmp_path / "planning").mkdir(exist_ok=True)
    (tmp_path / "chapters").mkdir(exist_ok=True)
    for n in range(1, 21):
        (tmp_path / "chapters" / f"{n}_第{n}章.txt").write_text("正文", encoding="utf-8")
    (tmp_path / "planning" / "伏笔.md").write_text(
        "# 伏笔\n- 陈年旧事（第1章埋设）\n", encoding="utf-8")
    sync_story_threads(tmp_path)

    page = StorylinePage()
    page.set_project_dir(tmp_path)
    assert page.cards["foreshadow"].hint.text().startswith("超期未回收 1 条")
    page.filter_combo.setCurrentIndex(3)  # 伏笔超期
    qapp.processEvents()
    assert page.tree.topLevelItemCount() == 1
    assert page.tree.topLevelItem(0).childCount() == 1
    row = page.tree.topLevelItem(0).child(0)
    assert "超期" in row.text(1) and row.text(0) == "陈年旧事"
    page.close()


def test_splash_finds_project_logo(qapp):
    """启动页必须找得到 logo（曾因路径少一级导致只有文字没有图标）。"""
    from src.novel_writer.core.app_config import LOGO_PATH
    from src.novel_writer.ui.shell.splash import SplashScreen

    found = SplashScreen._find_logo()
    assert found is not None
    assert found.exists()
    if LOGO_PATH.exists():
        assert found == LOGO_PATH


def test_storyline_fields_are_wired(qapp, tmp_path):
    """人物/卷组/回收章必须接上真实数据，而不是摆设输入框。"""
    import json

    from PySide6.QtCore import Qt

    from src.novel_writer.core.story_threads import sync_story_threads
    from src.novel_writer.core.world_state import WorldState
    from src.novel_writer.ui.character.storyline_page import StorylinePage

    (tmp_path / "planning").mkdir()
    (tmp_path / "chapters").mkdir()
    (tmp_path / "meta.json").write_text(
        json.dumps({"title": "测试书", "target_chapters": 100}, ensure_ascii=False),
        encoding="utf-8",
    )
    for n in range(1, 4):
        (tmp_path / "chapters" / f"{n}_第{n}章.txt").write_text("正文", encoding="utf-8")
    (tmp_path / "planning" / "主线.md").write_text(
        "# 主线\n## 卷一 外门断事（1—50章）\n- 拜入宗门（第1章）\n",
        encoding="utf-8",
    )
    (tmp_path / "planning" / "人物设定.md").write_text(
        "## 顾言\n- 主角\n## 赵九衡\n- 执事\n", encoding="utf-8"
    )
    (tmp_path / "planning" / "伏笔.md").write_text(
        "# 伏笔\n- 陈年旧事（第1章埋设）\n", encoding="utf-8"
    )
    gs = WorldState(tmp_path)
    gs.load()
    gs.add_timeline_event(3, "顾言立下申辩状，赵九衡收进袖中", "执事堂")
    gs.save()
    sync_story_threads(tmp_path)

    page = StorylinePage()
    page.set_project_dir(tmp_path)
    assert any("卷一" in page.edit_group.itemText(i) for i in range(page.edit_group.count()))
    assert "顾言" in page._owner_names and "赵九衡" in page._owner_names

    def find(title: str):
        stack = [page.tree.topLevelItem(i) for i in range(page.tree.topLevelItemCount())]
        while stack:
            item = stack.pop(0)
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if not data.get("group") and item.text(0) == title:
                return item
            stack.extend(item.child(i) for i in range(item.childCount()))
        return None

    timeline_item = find("顾言立下申辩状，赵九衡收进袖中")
    assert timeline_item is not None
    # 时间线也按卷分组（不再平铺）
    parent = timeline_item.parent()
    assert parent is not None and "卷一" in parent.text(0)

    page.tree.setCurrentItem(timeline_item)
    qapp.processEvents()
    assert page.edit_owner.text() == "顾言、赵九衡"
    assert page.edit_group.currentText().startswith("卷一")

    # 标完成且没填回收章 → 自动补当前章（第 3 章）
    foreshadow_item = find("陈年旧事")
    assert foreshadow_item is not None
    page.tree.setCurrentItem(foreshadow_item)
    qapp.processEvents()
    page.edit_status.setCurrentIndex(page.edit_status.findData("done"))
    page._save_detail()
    qapp.processEvents()
    thread = page.ledger.get(page._current_id)
    assert thread.resolve_chapter == 3
    assert thread.status == "done"

    # 列宽回归：标题列不能被长摘要/长人名挤没；窄窗口先收起人物列
    page.resize(900, 780)
    page.show()
    qapp.processEvents()
    assert page.tree.columnWidth(0) >= 200
    page.resize(680, 780)
    qapp.processEvents()
    assert page.tree.isColumnHidden(3)
    page.close()


def test_stream_chapter_does_not_hijack_other_chapter(qapp):
    """生成第14章时用户点了第9章：后续流式片段不能切回14，也不能写进9。"""
    from src.novel_writer.models.chapter import Chapter
    from src.novel_writer.ui.editor.editor_panel import EditorPanel

    editor = EditorPanel()
    editor.start_stream_chapter(14)
    editor.append_stream_chapter(14, "第十四章内容")
    assert editor.is_streaming_chapter(14)
    assert "第十四章内容" in editor.get_content()

    chapter9 = Chapter(number=9, title="第九章")
    chapter9._content_cache = "第九章正文"
    editor.load_chapter(chapter9)
    assert not editor.is_streaming_chapter(14)

    editor.append_stream_chapter(14, "不该出现的片段")
    assert editor._current_chapter.number == 9
    assert editor.get_content() == "第九章正文"
    editor.end_stream_chapter()
    editor.close()


def test_project_dialog_loads_target_chapters(qapp):
    """编辑项目时目标章节数必须回填，不能永远显示 100 并覆盖 500。"""
    from src.novel_writer.ui.project.new_project_dialog import NewProjectDialog

    dialog = NewProjectDialog(
        project_data={
            "title": "测试书",
            "genre": "仙侠",
            "style": "史诗宏大",
            "target_words": 1200000,
            "target_chapters": 500,
            "theme": "测试",
            "direction": "测试",
        },
        agents={},
    )
    assert dialog.chapters_spin.value() == 500
    assert dialog.get_data()["target_chapters"] == 500
    dialog.close()


def test_assist_page_reads_chapter_artifacts(qapp, tmp_path):
    """写作台：本章/全书 9 个子页都能读到对应产物。"""
    import json

    from src.novel_writer.ui.editor.assist_page import AssistPage

    (tmp_path / "inspiration").mkdir()
    (tmp_path / "inspiration" / "3_灵感.md").write_text("# 灵感\n\n让顾言在旧库发现残卷", encoding="utf-8")
    (tmp_path / "sim_cache" / "pov").mkdir(parents=True)
    (tmp_path / "sim_cache" / "sim_3.md").write_text("# 推演\n\n顾言会先试探", encoding="utf-8")
    (tmp_path / "sim_cache" / "pov" / "panel_3.json").write_text(
        json.dumps({"director": {"choice": "沈玄微", "reason": "压得住场"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "planning").mkdir()
    (tmp_path / "planning" / "章节细纲.md").write_text(
        "## 第3章 测试\n- 事件：旧库对峙\n- 转折：残卷出现\n- 章末钩子：门外有人\n",
        encoding="utf-8",
    )
    (tmp_path / "chapters" / "versions").mkdir(parents=True)
    (tmp_path / "chapters" / "3_第3章 测试.txt").write_text("# 第3章 测试\n\n正文", encoding="utf-8")
    (tmp_path / "chapters" / "3_第3章 测试.summary.md").write_text(
        "# 第3章 概要\n\n顾言进了旧库。", encoding="utf-8")
    (tmp_path / "chapters" / "versions" / "3_1_abcd.txt").write_text("# 旧版本\n\n修订前正文", encoding="utf-8")
    (tmp_path / "review").mkdir()
    (tmp_path / "review" / "质量检查_第3章.md").write_text("# 质量\n\n总分 80", encoding="utf-8")
    (tmp_path / "review" / "校对报告_第3章.md").write_text("# 校对\n\n第3段有错字", encoding="utf-8")
    (tmp_path / "review" / "审核报告.md").write_text("# 全书审核\n\n节奏偏慢", encoding="utf-8")
    (tmp_path / "summary").mkdir()
    (tmp_path / "summary" / "第1-3章摘要.md").write_text("# 滚动摘要\n\n前三章内容", encoding="utf-8")
    (tmp_path / "memory.json").write_text(json.dumps({
        "character_state": [{
            "subject": "顾言", "aspect": "位置", "value": "旧库",
            "status": "active", "source_chapter": 3,
        }]
    }, ensure_ascii=False), encoding="utf-8")

    page = AssistPage()
    page.set_project_dir(tmp_path)
    page.set_chapter(3, "测试")

    checks = {
        "inspiration": "旧库发现残卷",
        "sim": "顾言会先试探",
        "outline": "旧库对峙",
        "summary": "顾言进了旧库",
        "review": "第3段有错字",
        "rolling_summary": "前三章内容",
        "review_center": "节奏偏慢",
        "memory": "顾言",
        "versions": "修订前正文",
    }
    for key, expected in checks.items():
        page.select_key(key)
        qapp.processEvents()
        text = page.browser.toPlainText()
        assert expected in text, f"{key} 未显示 {expected!r}，实际：{text[:200]}"
    page.close()
