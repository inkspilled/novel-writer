from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLabel, QPushButton,
    QTabWidget, QStackedWidget,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QTextOption

from ..locales import t
from ..core import project_io


# planning/ 下的 8 个文档（按依赖顺序）
PLANNING_DOCS = [
    ("立意", "planning/立意.md"),
    ("大纲", "planning/大纲.md"),
    ("人物设定", "planning/人物设定.md"),
    ("世界观", "planning/世界观.md"),
    ("时间线", "planning/时间线.md"),
    ("主线", "planning/主线.md"),
    ("支线", "planning/支线.md"),
    ("伏笔", "planning/伏笔.md"),
]


def _make_editor(placeholder: str = "") -> QTextEdit:
    """创建统一风格的编辑器。"""
    edit = QTextEdit()
    edit.setPlaceholderText(placeholder)
    edit.setAcceptRichText(False)
    edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    import sys
    _font = "Microsoft YaHei" if sys.platform == "win32" else "PingFang SC"
    font = QFont(_font, 16)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    edit.setFont(font)
    return edit


class EditorPanel(QWidget):

    content_changed = Signal()
    save_requested = Signal()
    planning_save_requested = Signal(str)  # 发送 planning 文档名称

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_chapter_idx = -1
        self._project_dir: Path | None = None
        self._planning_edits: dict[str, QTextEdit] = {}
        self._planning_loaded: set[str] = set()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标签栏
        self.tab_bar = QTabWidget()
        self.tab_bar.setDocumentMode(True)
        self.tab_bar.currentChanged.connect(self._on_tab_changed)

        # ── 正文标签 ──
        chapter_page = QWidget()
        chapter_layout = QVBoxLayout(chapter_page)
        chapter_layout.setContentsMargins(0, 0, 0, 0)
        chapter_layout.setSpacing(0)

        # 正文工具栏
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

        chapter_layout.addWidget(toolbar)

        self.content_edit = _make_editor(t("editor_ph_content"))
        self.content_edit.textChanged.connect(self._on_text_changed)
        chapter_layout.addWidget(self.content_edit)

        self.tab_bar.addTab(chapter_page, t("editor_tab_content"))

        # ── 规划文档标签 ──
        for label, _rel_path in PLANNING_DOCS:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.setSpacing(0)

            # 规划文档工具栏
            ptoolbar = QWidget()
            ptoolbar.setObjectName("editorToolbar")
            ptoolbar_layout = QHBoxLayout(ptoolbar)
            ptoolbar_layout.setContentsMargins(20, 10, 20, 10)

            status_label = QLabel()
            status_label.setStyleSheet("font-size: 12px; color: palette(placeholderText);")
            ptoolbar_layout.addWidget(status_label, 1)

            save_btn = QPushButton(t("editor_save"))
            save_btn.setObjectName("primary")
            save_btn.setFixedHeight(32)
            save_btn.setFixedWidth(72)
            save_btn.setStyleSheet("font-size: 12px; padding: 4px 12px;")
            save_btn.clicked.connect(lambda checked=False, n=label: self.planning_save_requested.emit(n))
            ptoolbar_layout.addWidget(save_btn)

            page_layout.addWidget(ptoolbar)

            edit = _make_editor(f"{label} — 尚未生成")
            edit.setReadOnly(True)
            edit.setStyleSheet("QTextEdit { color: palette(placeholderText); }")
            page_layout.addWidget(edit)

            self._planning_edits[label] = edit
            # 存引用方便 _on_tab_changed 查找
            page._status_label = status_label
            page._save_btn = save_btn

            self.tab_bar.addTab(page, label)

        layout.addWidget(self.tab_bar)

    def set_project_dir(self, project_dir: Path | None):
        """设置项目目录，刷新规划文档状态。"""
        self._project_dir = project_dir
        self._planning_loaded.clear()
        self._refresh_planning_states()

    def _refresh_planning_states(self):
        """根据文件是否存在，启用/禁用规划文档编辑器。"""
        if not self._project_dir:
            for label, _ in PLANNING_DOCS:
                edit = self._planning_edits[label]
                edit.setReadOnly(True)
                edit.setStyleSheet("QTextEdit { color: palette(placeholderText); }")
                edit.clear()
                edit.setPlaceholderText(f"{label} — 尚未生成")
            return

        for label, rel_path in PLANNING_DOCS:
            fpath = self._project_dir / rel_path
            edit = self._planning_edits[label]
            page = self.tab_bar.widget(PLANNING_DOCS.index((label, rel_path)) + 1)
            if fpath.exists() and fpath.stat().st_size > 0:
                edit.setReadOnly(False)
                edit.setStyleSheet("")
                edit.setPlaceholderText(f"编辑 {label}")
                page._status_label.setText("已生成")
                page._status_label.setStyleSheet("font-size: 12px; color: green;")
                page._save_btn.setEnabled(True)
            else:
                edit.setReadOnly(True)
                edit.setStyleSheet("QTextEdit { color: palette(placeholderText); }")
                edit.clear()
                edit.setPlaceholderText(f"{label} — 尚未生成")
                page._status_label.setText("未生成")
                page._status_label.setStyleSheet("font-size: 12px; color: palette(placeholderText);")
                page._save_btn.setEnabled(False)

    def _on_tab_changed(self, index: int):
        """切换标签时加载对应规划文档内容。"""
        if index == 0:
            # 正文标签
            return
        doc_idx = index - 1
        if doc_idx < 0 or doc_idx >= len(PLANNING_DOCS):
            return
        label, rel_path = PLANNING_DOCS[doc_idx]
        if label in self._planning_loaded:
            return
        if not self._project_dir:
            return
        fpath = self._project_dir / rel_path
        if fpath.exists() and fpath.stat().st_size > 0:
            content = project_io.read_md(fpath)
            self._planning_edits[label].blockSignals(True)
            self._planning_edits[label].setPlainText(content)
            self._planning_edits[label].blockSignals(False)
            self._planning_loaded.add(label)

    def refresh_planning(self, doc_name: str | None = None):
        """刷新指定规划文档（或全部）的内容和状态。"""
        if doc_name:
            self._planning_loaded.discard(doc_name)
        else:
            self._planning_loaded.clear()
        self._refresh_planning_states()
        # 如果当前在某个规划标签上，重新加载
        idx = self.tab_bar.currentIndex()
        if idx > 0:
            self._on_tab_changed(idx)

    def get_planning_content(self, doc_name: str) -> str:
        """获取指定规划文档的编辑器内容。"""
        edit = self._planning_edits.get(doc_name)
        return edit.toPlainText() if edit else ""

    def set_planning_content(self, doc_name: str, content: str):
        """设置指定规划文档的编辑器内容。"""
        edit = self._planning_edits.get(doc_name)
        if edit:
            edit.blockSignals(True)
            edit.setPlainText(content)
            edit.blockSignals(False)
            self._planning_loaded.add(doc_name)

    def load_chapter(self, chapter):
        self._current_chapter_idx = chapter.number - 1 if hasattr(chapter, 'number') else -1
        self.chapter_title.setText(f"第{chapter.number}章 {chapter.title}" if hasattr(chapter, 'number') else "")
        self.content_edit.blockSignals(True)
        self.content_edit.setPlainText(chapter.content or "")
        self.content_edit.blockSignals(False)
        self._update_word_count()
        # 切换到正文标签
        self.tab_bar.setCurrentIndex(0)

    def get_content(self) -> str:
        return self.content_edit.toPlainText()

    def set_content(self, text: str):
        self.content_edit.setPlainText(text)

    def is_planning_tab(self) -> bool:
        """当前是否在规划文档标签页。"""
        return self.tab_bar.currentIndex() > 0

    def current_planning_name(self) -> str | None:
        """当前选中的规划文档名称，不在规划标签时返回 None。"""
        idx = self.tab_bar.currentIndex()
        if idx <= 0:
            return None
        return PLANNING_DOCS[idx - 1][0]

    def retranslate(self):
        self.chapter_title.setText(t("editor_select_chapter"))
        self.content_edit.setPlaceholderText(t("editor_ph_content"))
        self.save_btn.setText(t("editor_save"))
        self.tab_bar.setTabText(0, t("editor_tab_content"))

    def clear(self):
        self.chapter_title.setText(t("editor_select_chapter"))
        self.content_edit.clear()
        self.word_count_label.setText(f"0 {t('editor_words')}")
        for label in self._planning_edits:
            self._planning_edits[label].clear()
        self._planning_loaded.clear()
        self._refresh_planning_states()

    def _on_text_changed(self):
        self._update_word_count()
        self.content_changed.emit()

    def _update_word_count(self):
        text = self.content_edit.toPlainText()
        count = len(text.replace(" ", "").replace("\n", ""))
        self.word_count_label.setText(f"{count:,} {t('editor_words')}")
