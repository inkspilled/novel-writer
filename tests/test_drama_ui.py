"""短剧工作区 UI 回归：独立工作区、分镜表格、主窗口模式切换。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.novel_writer.models.project import Project  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _novel_project(tmp_path):
    project = Project(title="小说测试", genre="仙侠", style="快节奏")
    project.set_project_dir(tmp_path)
    project.save()
    return project


def test_drama_workspace_roundtrip(qapp, tmp_path):
    from src.novel_writer.ui.drama.drama_workspace import DramaWorkspace

    project = _novel_project(tmp_path)
    workspace = DramaWorkspace()
    workspace.set_project(project)
    assert workspace.pages.count() == 4
    assert workspace.nav.count() == 4
    assert workspace.script_page.list.count() == 1
    assert (tmp_path / "drama" / "series.json").exists()

    workspace.storyboard_page._add_shot()
    workspace.storyboard_page.save()
    assert workspace.storyboard_page.table.rowCount() == 1
    workspace.save_all(silent=True)

    reopened = DramaWorkspace()
    reopened.set_project(Project.load(tmp_path))
    assert reopened.storyboard_page.table.rowCount() == 1
    assert reopened.storyboard_page.table.item(0, 0).text().startswith("E01-S")
    workspace.close()
    reopened.close()


def test_deleted_episode_number_is_selected_by_identity(qapp, tmp_path):
    from src.novel_writer.ui.drama.drama_workspace import DramaWorkspace
    workspace = DramaWorkspace()
    workspace.set_project(_novel_project(tmp_path))
    store = workspace.store
    store.add_episode("第二集")
    store.add_episode("第三集")
    store.delete_episode(1)
    workspace.script_page.load(store, keep=2)
    assert workspace.script_page._current == 2
    store.delete_episode(2)
    store.delete_episode(3)
    workspace.script_page.load(store)
    workspace.storyboard_page.load(store)
    assert workspace.script_page._current == workspace.storyboard_page._current == 0
    workspace.close()


def test_unload_stops_delayed_saves(qapp, tmp_path):
    from src.novel_writer.ui.drama.drama_workspace import DramaWorkspace
    workspace = DramaWorkspace()
    workspace.set_project(_novel_project(tmp_path))
    workspace.script_page.editor.setPlainText("尚未保存的修改")
    assert workspace.script_page._autosave.isActive()
    workspace.set_project(None)
    assert not workspace.script_page._autosave.isActive()
    assert workspace.script_page._store is None
    assert not workspace.btn_export.isEnabled()
    workspace.close()


def test_adaptive_sources_and_pending_are_visible(qapp, tmp_path):
    from src.novel_writer.core import project_io
    from src.novel_writer.ui.drama.drama_workspace import DramaWorkspace
    workspace = DramaWorkspace()
    workspace.set_project(_novel_project(tmp_path))
    sources = [{"number": n, "text": f"素材{n}"} for n in range(1, 5)]
    plan = {"title": "追踪", "goal": "找到线索", "hook": "谁在门外", "source_chapters": [1, 2, 3, 4], "beats": ["出发", "追踪"]}
    workspace.store.commit_adaptive_episode(1, plan, sources, "# 第1集\n\n## 第1场 山门 · 夜\n\n张三：走。")
    project_io.write_json(workspace.store.root / "adaptation.json", {"pending_chapters": [5], "carry_reason": "新冲突顺延"})
    workspace.script_page.load(workspace.store)
    workspace._refresh_overview()
    assert "1、2、3、4" in workspace.script_page.source_label.text()
    assert "待用章节 5" in workspace.overview.text()
    assert workspace.overview.toolTip() == "新冲突顺延"
    workspace.close()


def test_drama_workspace_generates_from_script(qapp, tmp_path):
    from src.novel_writer.ui.drama.drama_workspace import DramaWorkspace

    project = _novel_project(tmp_path)
    workspace = DramaWorkspace()
    workspace.set_project(project)
    workspace.script_page.editor.setPlainText(
        "# 第1集\n\n## 第1场 山门 · 夜\n\n林乐天：走。\n\n## 第2场 石阶 · 晨\n\n苏月：等等。\n"
    )
    workspace.script_page.save()
    workspace.storyboard_page._generate_from_script()
    assert workspace.storyboard_page.table.rowCount() == 2
    assert workspace.storyboard_page.table.item(0, 5).text() == "山门"
    workspace.close()


def test_drama_agent_panel_has_agents_and_actions(qapp, tmp_path):
    from src.novel_writer.ui.drama.drama_panel import DramaAgentPanel
    from src.novel_writer.ui.agent.office_scene import AgentState
    from src.novel_writer.ui.workflow.workflow_bar import WorkflowMiniBar

    panel = DramaAgentPanel()
    panel.set_project(_novel_project(tmp_path))
    assert len(panel.office.iter_slots()) == 4
    # 工作流条复用小说侧 WorkflowMiniBar：单个「执行模式」入口，不再是一键按钮网格
    assert isinstance(panel.workflow, WorkflowMiniBar)
    assert panel.workflow._btn_start.text() == "执行模式"
    assert panel.workflow._btn_start.isEnabled()  # 有项目即可执行
    panel.set_agent_state("storyboard", AgentState.WORKING, "拆镜")
    assert any(s.name == "storyboard" and s.state == AgentState.WORKING
               for s in panel.office.iter_slots())
    panel.close()

    # 无项目时执行模式置灰
    panel2 = DramaAgentPanel()
    panel2.set_project(None)
    assert not panel2.workflow._btn_start.isEnabled()
    panel2.close()


def test_drama_mode_dialog_modes_and_scope(qapp):
    from src.novel_writer.ui.drama.drama_mode_dialog import DramaModeDialog

    dialog = DramaModeDialog(storyboard_all_label="全部集（3 集有剧本，逐集调用模型）")
    tasks = [dialog._mode_combo.itemData(i) for i in range(dialog._mode_combo.count())]
    assert tasks == ["full", "segment", "adapt", "storyboard", "assets"]
    assert "script" not in tasks  # 逐章转剧本已删除，统一走智能分集

    # offscreen 下 dialog 未 show，用 isVisibleTo 断言「假如对话框显示，控件是否可见」
    def scope_visible() -> bool:
        return dialog._scope_group.isVisibleTo(dialog)

    # full：范围组显示，默认「生成下一集」
    dialog._mode_combo.setCurrentIndex(0)
    assert scope_visible()
    assert dialog.get_result() == ("full", "current")
    dialog.radio_all.setChecked(True)
    assert dialog.get_result() == ("full", "all")

    # storyboard：范围组文案切到当前集/全部集
    dialog._mode_combo.setCurrentIndex(3)
    assert scope_visible()
    assert dialog.radio_all.text().startswith("全部集（3")
    assert dialog.get_result() == ("storyboard", "all")

    # adapt/assets：范围组隐藏，scope 固定 all
    for idx, task in ((2, "adapt"), (4, "assets")):
        dialog._mode_combo.setCurrentIndex(idx)
        assert not scope_visible()
        assert dialog.get_result() == (task, "all")
    dialog.close()


def test_new_project_dialog_is_novel_only(qapp):
    from src.novel_writer.ui.project.new_project_dialog import NewProjectDialog

    dialog = NewProjectDialog()
    assert not hasattr(dialog, "type_combo")  # 短剧不再单独建项目
    assert dialog.title_label.text() == "书名 *"
    assert dialog.chapters_spin.suffix() == " 章"
    dialog.close()


def test_main_window_has_novel_and_drama_tabs(qapp, tmp_path):
    from src.novel_writer.ui.main_window import MainWindow

    _novel_project(tmp_path)
    window = MainWindow()
    window.show()
    qapp.processEvents()
    window._load_project_from_dir(tmp_path)
    qapp.processEvents()

    assert window.mode_tabs.count() == 2
    assert [window.mode_tabs.tabText(i) for i in range(2)] == ["小说", "短剧"]
    assert window.mode_tabs.currentIndex() == 0

    window.mode_tabs.setCurrentIndex(1)
    qapp.processEvents()
    assert window.drama_workspace.store is not None
    assert (tmp_path / "drama" / "series.json").exists()  # 依附小说项目目录
    assert window.drama_workspace.script_page.list.count() == 1

    window.mode_tabs.setCurrentIndex(0)
    qapp.processEvents()
    assert window.mode_tabs.currentIndex() == 0
    window.close()
