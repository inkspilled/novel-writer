"""UI 第一轮精简的回归测试：标题去重、自动保存状态、空日志占位、人物导入。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class TestChapterTitle:
    def test_strip_duplicate_prefix(self):
        from src.novel_writer.ui.common.titles import (
            chapter_display_title,
            clean_chapter_title,
        )

        assert clean_chapter_title("第四十章：第九人来过") == "第九人来过"
        assert clean_chapter_title("第40章 · 第九人来过") == "第九人来过"
        assert clean_chapter_title("第 12 章、初遇") == "初遇"
        assert clean_chapter_title("第九人来过") == "第九人来过"
        # 只有章号时视为没有题名，避免「第40章 · 第40章」
        assert clean_chapter_title("第40章") == ""
        assert chapter_display_title(40, "第40章") == "第40章"

        assert chapter_display_title(40, "第四十章：第九人来过") == "第40章 · 第九人来过"
        assert chapter_display_title(40, "") == "第40章"
        assert chapter_display_title(3, "黎明前夜") == "第3章 · 黎明前夜"


class TestEditorAutosave:
    def test_label_follows_edit_and_save(self, qapp):
        from src.novel_writer.models.chapter import Chapter
        from src.novel_writer.ui.editor.editor_panel import EditorPanel

        editor = EditorPanel()
        chapter = Chapter(number=40, title="第四十章：第九人来过")
        editor.load_chapter(chapter)

        assert editor.chapter_title.text() == "第40章 · 第九人来过"
        assert editor.autosave_label.text() == "已自动保存"

        editor.content_edit.setPlainText("改一段")
        assert editor.autosave_label.text() == "编辑中…"

        editor.mark_saved()
        assert editor.autosave_label.text() == "已自动保存"
        editor.close()

    def test_no_labels_before_chapter(self, qapp):
        from src.novel_writer.ui.editor.editor_panel import EditorPanel

        editor = EditorPanel()
        editor.detach_chapter()
        assert editor.autosave_label.text() == ""
        editor.close()


class TestMarkdownPreview:
    def test_toggle_renders_markdown_read_only(self, qapp):
        from src.novel_writer.models.chapter import Chapter
        from src.novel_writer.ui.editor.editor_panel import EditorPanel

        editor = EditorPanel()
        editor.load_chapter(Chapter(number=1, title="初章"))
        editor.content_edit.setPlainText("## 第一节\n\n**重要**情报")

        editor.preview_btn.setChecked(True)
        assert editor.content_stack.currentWidget() is editor.preview_view
        assert editor.preview_btn.text() == "编辑"
        assert editor.content_edit.isReadOnly()
        rendered = editor.preview_view.toPlainText()
        assert "第一节" in rendered
        assert "**" not in rendered

        editor.preview_btn.setChecked(False)
        assert editor.content_stack.currentWidget() is editor.content_edit
        assert not editor.content_edit.isReadOnly()
        editor.close()


class TestWorkflowLogPlaceholder:
    def test_empty_log_uses_hint_not_gray_box(self, qapp):
        from src.novel_writer.ui.workflow.workflow_bar import WorkflowMiniBar

        bar = WorkflowMiniBar()
        bar.set_fill_available(True)
        assert bar._log_hint.isVisibleTo(bar)
        assert not bar._log_area.isVisibleTo(bar)

        bar.append_log("▸ 写手 开始 chapter")
        assert bar._log_area.isVisibleTo(bar)
        assert not bar._log_hint.isVisibleTo(bar)
        bar.close()


class TestAgentStrip:
    def test_collapsed_to_one_line_by_default(self, qapp):
        from src.novel_writer.ui.agent.agent_panel import AgentPanel

        panel = AgentPanel()
        panel.set_config({})
        panel.update_agent_buttons({"a": {"title": "主编"}, "b": {"title": "写手"}})
        assert panel.office.isHidden()
        assert panel._strip_label.isVisibleTo(panel)
        assert "2 个" in panel._strip_label.text()

        panel.btn_strip.setChecked(True)
        assert panel.office.isVisibleTo(panel)
        assert not panel._strip_label.isVisibleTo(panel)
        panel.close()


class TestCharacterImport:
    def test_imports_world_state_characters_on_demand(self, qapp, tmp_path):
        from src.novel_writer.core.world_state import WorldState
        from src.novel_writer.ui.character.character_page import CharacterPage

        gs = WorldState(tmp_path)
        gs.load()
        gs.add_character("林乐天", {"location": "山门"})
        gs.add_character("药老", {"location": "山门"})
        gs.save()

        page = CharacterPage()
        page.set_project_dir(tmp_path)
        assert page.list.count() == 0
        assert page.btn_import.isVisibleTo(page)
        assert "2" in page.btn_import.text()

        page._import_from_world_state()
        assert page.list.count() == 2
        assert not page.btn_import.isVisibleTo(page)
        page.close()
