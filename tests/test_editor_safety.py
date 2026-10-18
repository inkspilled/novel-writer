"""编辑器数据安全回归 — 切项目/清空不得把旧章节写成空文件。

回归背景：EditorPanel.clear() 直接 content_edit.clear()，会触发 textChanged →
content_changed；此时 _current_chapter 仍指向旧项目章节，MainWindow 的
_on_content_changed 会把空字符串写回旧章（切项目丢章）。
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.novel_writer.ui.editor.editor_panel import EditorPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeChapter:
    """最小 Chapter 替身：content setter 记录每次落盘。"""

    def __init__(self, content: str = "# 第1章 旧章\n\n旧正文"):
        self.number = 1
        self.title = "旧章"
        self._content = content
        self.writes: list[str] = []

    @property
    def content(self) -> str:
        return self._content

    @content.setter
    def content(self, value: str) -> None:
        self.writes.append(value)
        self._content = value


def _wire(panel: EditorPanel, chapter: FakeChapter):
    """复刻 MainWindow 的接线：content_changed → 写回当前 Chapter。"""
    panel.content_changed.connect(
        lambda: setattr(chapter, "content", panel.get_content())
    )


class TestEditorDetach:
    def test_clear_does_not_write_current_chapter(self, qapp):
        panel = EditorPanel()
        chapter = FakeChapter()
        panel._current_chapter = chapter
        _wire(panel, chapter)
        panel.load_chapter(chapter)

        panel.clear()

        assert chapter.writes == [], "clear() 不得触发 content_changed 写盘"
        assert chapter.content.startswith("# 第1章")
        assert panel._current_chapter is None

    def test_detach_chapter_drops_reference_without_write(self, qapp):
        panel = EditorPanel()
        chapter = FakeChapter()
        panel._current_chapter = chapter
        _wire(panel, chapter)
        panel.load_chapter(chapter)

        panel.detach_chapter()

        assert panel._current_chapter is None
        assert panel.get_content() == ""
        assert chapter.writes == []

    def test_user_edit_still_writes_after_detach_cycle(self, qapp):
        """卸载后重新加载新章，正常输入仍应实时落盘。"""
        panel = EditorPanel()
        old = FakeChapter("旧章正文")
        panel._current_chapter = old
        _wire(panel, old)
        panel.load_chapter(old)
        panel.detach_chapter()

        panel.content_changed.disconnect()  # 真实 MainWindow 只有一条接线
        new = FakeChapter("新章正文")
        panel._current_chapter = new
        _wire(panel, new)
        panel.load_chapter(new)
        panel.content_edit.setPlainText("新章正文（已编辑）")

        assert new.writes[-1] == "新章正文（已编辑）"
        assert old.writes == []
