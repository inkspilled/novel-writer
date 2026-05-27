from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLabel, QTabWidget,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QTextOption

from ..locales import t


class EditorPanel(QWidget):

    content_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_chapter_idx = -1
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 工具栏
        toolbar = QWidget()
        toolbar.setObjectName("editorToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(20, 10, 20, 10)

        self.chapter_title = QLabel(t("editor_select_chapter"))
        self.chapter_title.setWordWrap(True)
        self.chapter_title.setStyleSheet("font-size: 18px; font-weight: 700; letter-spacing: -0.3px;")
        toolbar_layout.addWidget(self.chapter_title, 1)

        self.word_count_label = QLabel(f"0 {t('editor_words')}")
        self.word_count_label.setStyleSheet("font-size: 12px; font-weight: 500;")
        self.word_count_label.setMinimumWidth(60)
        toolbar_layout.addWidget(self.word_count_label)
        layout.addWidget(toolbar)

        # Tab
        self.tabs = QTabWidget()

        self.content_edit = QTextEdit()
        self.content_edit.setPlaceholderText(t("editor_ph_content"))
        self.content_edit.setAcceptRichText(False)
        self.content_edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        font = QFont("PingFang SC", 16)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        self.content_edit.setFont(font)
        self.content_edit.textChanged.connect(self._on_text_changed)
        self.tabs.addTab(self.content_edit, t("editor_tab_content"))

        self.outline_edit = QTextEdit()
        self.outline_edit.setPlaceholderText(t("editor_ph_outline"))
        self.outline_edit.setAcceptRichText(False)
        self.tabs.addTab(self.outline_edit, t("editor_tab_outline"))

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText(t("editor_ph_notes"))
        self.notes_edit.setAcceptRichText(False)
        self.tabs.addTab(self.notes_edit, t("editor_tab_notes"))

        layout.addWidget(self.tabs)

    def load_chapter(self, chapter):
        self._current_chapter_idx = chapter.number - 1 if hasattr(chapter, 'number') else -1
        self.chapter_title.setText(f"第{chapter.number}章 {chapter.title}" if hasattr(chapter, 'number') else "")
        for edit in (self.content_edit, self.outline_edit, self.notes_edit):
            edit.blockSignals(True)
        self.content_edit.setPlainText(chapter.content or "")
        self.outline_edit.setPlainText(chapter.outline or "")
        self.notes_edit.setPlainText(chapter.notes or "")
        for edit in (self.content_edit, self.outline_edit, self.notes_edit):
            edit.blockSignals(False)
        self._update_word_count()

    def get_content(self) -> str:
        return self.content_edit.toPlainText()

    def get_outline(self) -> str:
        return self.outline_edit.toPlainText()

    def get_notes(self) -> str:
        return self.notes_edit.toPlainText()

    def set_content(self, text: str):
        self.content_edit.setPlainText(text)

    def retranslate(self):
        """刷新文本。"""
        self.chapter_title.setText(t("editor_select_chapter"))
        self.content_edit.setPlaceholderText(t("editor_ph_content"))
        self.outline_edit.setPlaceholderText(t("editor_ph_outline"))
        self.notes_edit.setPlaceholderText(t("editor_ph_notes"))
        self.tabs.setTabText(0, t("editor_tab_content"))
        self.tabs.setTabText(1, t("editor_tab_outline"))
        self.tabs.setTabText(2, t("editor_tab_notes"))

    def clear(self):
        self.chapter_title.setText(t("editor_select_chapter"))
        self.content_edit.clear()
        self.outline_edit.clear()
        self.notes_edit.clear()
        self.word_count_label.setText(f"0 {t('editor_words')}")

    def _on_text_changed(self):
        self._update_word_count()
        self.content_changed.emit()

    def _update_word_count(self):
        text = self.content_edit.toPlainText()
        count = len(text.replace(" ", "").replace("\n", ""))
        self.word_count_label.setText(f"{count:,} {t('editor_words')}")
