from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLabel, QPushButton,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QTextOption

from ..locales import t


class EditorPanel(QWidget):

    content_changed = Signal()
    save_requested = Signal()

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

        self.save_btn = QPushButton(t("editor_save"))
        self.save_btn.setObjectName("primary")
        self.save_btn.setFixedHeight(32)
        self.save_btn.setFixedWidth(72)
        self.save_btn.setStyleSheet("font-size: 12px; padding: 4px 12px;")
        self.save_btn.clicked.connect(self.save_requested.emit)
        toolbar_layout.addWidget(self.save_btn)

        layout.addWidget(toolbar)

        # 正文编辑区（无 Tab）
        self.content_edit = QTextEdit()
        self.content_edit.setPlaceholderText(t("editor_ph_content"))
        self.content_edit.setAcceptRichText(False)
        self.content_edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        import sys
        _font = "Microsoft YaHei" if sys.platform == "win32" else "PingFang SC"
        font = QFont(_font, 16)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        self.content_edit.setFont(font)
        self.content_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.content_edit)

    def load_chapter(self, chapter):
        self._current_chapter_idx = chapter.number - 1 if hasattr(chapter, 'number') else -1
        self.chapter_title.setText(f"第{chapter.number}章 {chapter.title}" if hasattr(chapter, 'number') else "")
        self.content_edit.blockSignals(True)
        self.content_edit.setPlainText(chapter.content or "")
        self.content_edit.blockSignals(False)
        self._update_word_count()

    def get_content(self) -> str:
        return self.content_edit.toPlainText()

    def set_content(self, text: str):
        self.content_edit.setPlainText(text)

    def retranslate(self):
        self.chapter_title.setText(t("editor_select_chapter"))
        self.content_edit.setPlaceholderText(t("editor_ph_content"))
        self.save_btn.setText(t("editor_save"))

    def clear(self):
        self.chapter_title.setText(t("editor_select_chapter"))
        self.content_edit.clear()
        self.word_count_label.setText(f"0 {t('editor_words')}")

    def _on_text_changed(self):
        self._update_word_count()
        self.content_changed.emit()

    def _update_word_count(self):
        text = self.content_edit.toPlainText()
        count = len(text.replace(" ", "").replace("\n", ""))
        self.word_count_label.setText(f"{count:,} {t('editor_words')}")
