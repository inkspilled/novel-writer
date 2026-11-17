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

    # 新增资产一行：列必须和 ASSET_COLUMNS 对齐（reference_image 列没填会整行错位）
    workspace.assets_page._add_asset()
    workspace.assets_page.save(silent=True)
    added = workspace.store.load_assets()[-1]
    assert added["status"] == "draft"
    assert added["reference_image"] == ""
    workspace.save_all(silent=True)

    reopened = DramaWorkspace()
    reopened.set_project(Project.load(tmp_path))
    assert reopened.storyboard_page.table.rowCount() == 1
    assert reopened.storyboard_page.table.item(0, 0).text().startswith("E01-S")
    workspace.close()
    reopened.close()


def test_asset_preview_and_episode_dialog(qapp, tmp_path, monkeypatch):
    """资产表格填满页面，按需弹窗预览；空剧集可打开预览。"""
    from PySide6.QtGui import QImage
    from src.novel_writer.ui.drama.drama_workspace import DramaWorkspace
    from src.novel_writer.ui.drama.preview import (
        EpisodePreviewDialog, ImagePreviewDialog, thumbnail,
    )

    project = _novel_project(tmp_path)
    workspace = DramaWorkspace()
    workspace.set_project(project)
    store = workspace.store
    generated = store.assets_dir / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    image_path = generated / "hero.png"
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(0xFF336699)
    assert image.save(str(image_path), "PNG")
    store.save_assets([{
        "kind": "character", "id": "char_1", "name": "顾言", "source": "manual",
        "prompt": "", "ref": "", "reference_image": "generated/hero.png",
        "status": "ready", "notes": "", "entity": "", "image": "generated/hero.png",
    }])
    page = workspace.assets_page
    page.load(store)
    workspace.nav.setCurrentRow(3)
    workspace.resize(1500, 800)
    workspace.show()
    qapp.processEvents()

    assert page.table.geometry().bottom() == page.rect().bottom() - page.layout().contentsMargins().bottom()
    assert not page.btn_preview.isEnabled()
    assert thumbnail(image_path, 40, 40) is not None
    opened = []
    monkeypatch.setattr(ImagePreviewDialog, "exec", lambda dialog: opened.append(dialog.path))
    page.table.setCurrentCell(0, 2)
    assert page.btn_preview.isEnabled()
    assert not opened
    page.btn_preview.click()
    page.table.cellDoubleClicked.emit(0, 2)
    assert opened == [image_path, image_path]

    episode = EpisodePreviewDialog(store, 1)
    assert "还没有视频" in episode.status.text()
    episode.close()
    workspace.close()


def test_asset_prompt_editor_and_regeneration(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog, QMessageBox, QPlainTextEdit
    from src.novel_writer.ui.drama.drama_workspace import DramaWorkspace

    workspace = DramaWorkspace()
    workspace.set_project(_novel_project(tmp_path))
    store = workspace.store
    store.save_assets([{"id": "hero", "name": "顾言", "prompt": "待修改"},
                       {"id": "other", "name": "苏月", "prompt": "保留"}])
    page = workspace.assets_page
    page.load(store)
    row = page.select_asset("hero")

    def edit(dialog):
        editor = next(edit for edit in dialog.findChildren(QPlainTextEdit) if not edit.isReadOnly())
        editor.setPlainText("顾言，青色交领长袍")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", edit)
    page.table.cellDoubleClicked.emit(row, 4)
    assert store.load_assets()[0]["prompt"] == "顾言，青色交领长袍"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    page._fill_prompts(regenerate=True)
    hero, other = store.load_assets()
    assert "头肩特写" in hero["prompt"]
    assert other["prompt"] == "保留"
    workspace.close()


def test_storyboard_export_and_check_actions(qapp, tmp_path, monkeypatch):
    """分镜页两个新入口真的能落文件/弹检查，不是只挂了个按钮。"""
    from PySide6.QtWidgets import QMessageBox
    from src.novel_writer.ui.drama.drama_workspace import DramaWorkspace

    workspace = DramaWorkspace()
    workspace.set_project(_novel_project(tmp_path))
    page = workspace.storyboard_page
    page._add_shot()
    page.save()

    shown = []
    monkeypatch.setattr(page, "_show_export", lambda path, kind: shown.append((path, kind)))
    page._export_video_prompts()
    assert shown and shown[0][1] == "视频提示词"
    assert shown[0][0].exists()

    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    page._check_storyboard()  # 新镜头没写动作，应走告警弹窗而不是崩
    workspace.close()


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
    from src.novel_writer.core.drama import SHOT_KEYS
    assert workspace.storyboard_page.table.item(0, SHOT_KEYS.index("location")).text() == "山门"
    workspace.close()


def test_drama_agent_panel_has_agents_and_actions(qapp, tmp_path):
    from src.novel_writer.ui.drama.drama_panel import DramaAgentPanel
    from src.novel_writer.ui.agent.office_scene import AgentState
    from src.novel_writer.ui.workflow.workflow_bar import WorkflowMiniBar

    panel = DramaAgentPanel()
    panel.set_project(_novel_project(tmp_path))
    assert {s.name for s in panel.office.iter_slots()} == {
        "showrunner", "writer", "storyboard", "prompt", "review", "supervisor", "agent",
    }
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
    combo = dialog._mode_combo
    assert [combo.itemData(i) for i in range(combo.count())] == ["full", "production"]
    dialog._advanced.setChecked(True)
    tasks = [combo.itemData(i) for i in range(combo.count())]
    assert {"production", "preflight", "story_review", "frame_images", "submit_review", "post", "final_review"} <= set(tasks)
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
    combo.setCurrentIndex(combo.findData("storyboard"))
    assert scope_visible()
    assert dialog.radio_all.text().startswith("全部集（3")
    assert dialog.get_result() == ("storyboard", "all")

    # keyframes：范围组显示
    combo.setCurrentIndex(combo.findData("keyframes"))
    assert scope_visible()
    assert dialog.get_result()[0] == "keyframes"

    # adapt/video/assets：范围组隐藏，scope 固定 all
    for task in ("production", "adapt", "video", "assets", "post"):
        combo.setCurrentIndex(combo.findData(task))
        assert not scope_visible()
        assert dialog.get_result() == (task, "all")
    dialog._advanced.setChecked(False)
    assert combo.count() == 2
    assert dialog.get_result()[0] == "full"
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


def test_drama_mode_dialog_defaults_and_scope(qapp):
    """默认选中「生成下一集」，范围与模式的组合要能取对。"""
    from src.novel_writer.ui.drama.drama_mode_dialog import DramaModeDialog

    dialog = DramaModeDialog()
    assert dialog.radio_current.isChecked()
    assert not dialog.radio_all.isChecked()
    assert dialog.get_result() == ("full", "current")

    dialog.radio_all.setChecked(True)
    assert dialog.get_result() == ("full", "all")

    dialog._mode_combo.setCurrentIndex(1)
    assert dialog.get_result() == ("production", "all")
    dialog.close()


def test_drama_mode_dialog_shows_resume_point(qapp):
    """全流程模式要先把「接续点」摆出来：几集有剧本、几章待用、几集缺分镜。"""
    from src.novel_writer.ui.drama.drama_mode_dialog import DramaModeDialog

    hint = "已有剧本 3 集 · 待用正文 12 章 · 缺分镜 1 集 · 圣经已有（跳过）。"
    dialog = DramaModeDialog(full_hint=hint)
    dialog._mode_combo.setCurrentIndex(0)  # 全流程
    assert hint in dialog._hint_label.text()
    dialog.close()


def test_drama_panel_workflow_fills_panel_without_status_strip(qapp):
    """工作流条紧跟智能体卡片，日志灰框一直填到面板底部。"""
    from src.novel_writer.ui.drama.drama_panel import DramaAgentPanel

    panel = DramaAgentPanel()
    panel.resize(340, 780)
    panel.show()
    qapp.processEvents()

    assert panel.office.isHidden()
    assert panel.workflow.y() == 0
    assert panel.workflow.y() < panel.height() // 2
    assert panel.workflow.y() + panel.workflow.height() == panel.height()

    top_before = panel.workflow.y()
    panel.append_log("开始 segment：范围=全部待用")
    qapp.processEvents()
    assert panel.workflow.y() == top_before  # 日志展开只向下长，不挤走卡片
    assert panel.workflow._log_area.height() > 300  # 灰框吃满剩余高度

    panel.close()


def test_assets_page_imports_canon_and_builds_prompts(qapp, tmp_path):
    """资产跟小说正典对应：从角色卡/世界状态导入，提示词按题材风格生成。"""
    import json as _json

    from src.novel_writer.ui.drama.drama_workspace import DramaWorkspace

    project = _novel_project(tmp_path)
    (tmp_path / "canon").mkdir(exist_ok=True)
    (tmp_path / "canon" / "characters.json").write_text(_json.dumps({
        "version": 1,
        "characters": [{"id": "c1", "name": "顾言",
                         "public_profile": {"身份": "主角"}}],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "world_state.json").write_text(_json.dumps({
        "version": 1, "world": {}, "characters": {}, "timeline": [],
        "location_log": [], "locations": {"藏经阁": {"desc": "藏书重地"}},
        "items_catalog": {},
    }, ensure_ascii=False), encoding="utf-8")

    workspace = DramaWorkspace()
    workspace.set_project(project)
    page = workspace.assets_page

    before = page.table.rowCount()
    page._import_from_canon()
    assert page.table.rowCount() >= before + 2  # 顾言 + 藏经阁

    page._fill_prompts()
    prompts = [page.table.item(r, 4).text() for r in range(page.table.rowCount())]  # 提示词列
    assert any("顾言" in text for text in prompts)
    assert any("仙侠" in text or "快节奏" in text for text in prompts)
    workspace.close()


def test_asset_preview_two_layers_and_prop_views(qapp, tmp_path, monkeypatch):
    """人物两层参考（头肩+全身）与道具三视图都能预览；标记重做按层清除。"""
    import io

    from PIL import Image as PILImage
    from src.novel_writer.ui.drama.drama_workspace import DramaWorkspace
    from src.novel_writer.ui.drama.preview import MakeupPreviewDialog

    project = _novel_project(tmp_path)
    workspace = DramaWorkspace()
    workspace.set_project(project)
    store = workspace.store

    def png(size, color="blue"):
        buffer = io.BytesIO()
        PILImage.new("RGB", size, color).save(buffer, format="PNG")
        return buffer.getvalue()

    store.save_assets([
        {"kind": "character", "id": "hero", "name": "顾言", "source": "manual",
         "prompt": "", "ref": "", "status": "draft", "notes": "", "entity": "", "image": ""},
        {"kind": "prop", "id": "prop_1", "name": "玄铁令", "source": "manual",
         "prompt": "", "ref": "", "status": "draft", "notes": "", "entity": "", "image": ""},
    ])
    store.save_makeup_view("hero", "front", png((96, 128)))
    store.save_fullbody_view("hero", "front", png((96, 128)))
    store.save_prop_view("prop_1", "front", png((64, 64)))
    store.save_prop_view("prop_1", "side", png((64, 64), "green"))

    page = workspace.assets_page
    page.load(store)
    shown = []

    def fake_exec(dialog):
        shown.append(dialog)
        dialog.redo_view = "fb_front"  # 模拟用户标记全身正面重做

    monkeypatch.setattr(MakeupPreviewDialog, "exec", fake_exec)
    page._open_asset_image(0)  # character：两行预览（头肩 + 全身）
    assert len(shown) == 1
    assert shown[0].angle.count() == 2  # 下拉只列已有图：头肩正面 + 全身正面
    saved = {a["id"]: a for a in store.load_assets()}
    assert "front" not in saved["hero"].get("fullbody_views", {})  # 全身正面已清
    assert "front" in saved["hero"].get("makeup_views", {})  # 头肩正面不受影响

    shown.clear()

    def fake_exec_prop(dialog):
        shown.append(dialog)
        dialog.redo_view = "side"

    monkeypatch.setattr(MakeupPreviewDialog, "exec", fake_exec_prop)
    page._open_asset_image(1)  # prop：三视图预览
    assert shown[0].windowTitle().endswith("道具三视图")
    saved = {a["id"]: a for a in store.load_assets()}
    assert "side" not in saved["prop_1"].get("prop_views", {})
    workspace.close()
