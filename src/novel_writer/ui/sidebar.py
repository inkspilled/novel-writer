from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QMenu,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction

from ..locales import t


class Sidebar(QWidget):

    chapter_selected = Signal(int)
    new_project_requested = Signal()
    open_project_requested = Signal()
    chapter_add_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebarWidget")
        self.setMinimumWidth(220)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 20, 16, 16)
        layout.setSpacing(12)

        # 项目标题
        self.project_label = QLabel(t("sidebar_no_project"))
        self.project_label.setWordWrap(True)
        self.project_label.setStyleSheet("font-size: 17px; font-weight: 700; letter-spacing: -0.3px;")
        layout.addWidget(self.project_label)

        # 操作按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_new = QPushButton(t("sidebar_new"))
        self.btn_open = QPushButton(t("sidebar_open"))
        self.btn_new.setFixedHeight(34)
        self.btn_open.setFixedHeight(34)
        self.btn_new.setObjectName("primary")
        btn_row.addWidget(self.btn_new)
        btn_row.addWidget(self.btn_open)
        layout.addLayout(btn_row)
        self.btn_new.clicked.connect(self.new_project_requested.emit)
        self.btn_open.clicked.connect(self.open_project_requested.emit)

        # 章节标题
        self._section_label = QLabel(t("sidebar_chapters"))
        self._section_label.setStyleSheet("font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; padding: 8px 0 4px 0;")
        layout.addWidget(self._section_label)

        # 章节树
        self.chapter_tree = QTreeWidget()
        self.chapter_tree.setHeaderHidden(True)
        self.chapter_tree.setRootIsDecorated(False)
        self.chapter_tree.setIndentation(0)
        self.chapter_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chapter_tree.customContextMenuRequested.connect(self._on_context_menu)
        self.chapter_tree.currentItemChanged.connect(self._on_item_changed)
        layout.addWidget(self.chapter_tree)

        # 添加章节
        self.btn_add_chapter = QPushButton(t("sidebar_add_chapter"))
        self.btn_add_chapter.setFixedHeight(32)
        self.btn_add_chapter.clicked.connect(self.chapter_add_requested.emit)
        layout.addWidget(self.btn_add_chapter)

        # 统计
        self.stats_label = QLabel("")
        self.stats_label.setWordWrap(True)
        self.stats_label.setStyleSheet("font-size: 11px; padding: 4px 0;")
        layout.addWidget(self.stats_label)

        layout.addStretch()

    def load_chapters(self, chapters: list):
        self.chapter_tree.clear()
        icons = {"outlined": "📝", "drafted": "✍️", "proofread": "🔍",
                 "reviewed": "✅", "polished": "✨", "final": "📖"}
        for i, ch in enumerate(chapters):
            item = QTreeWidgetItem()
            icon = icons.get(ch.status.value if hasattr(ch.status, 'value') else ch.status, "📝")
            item.setText(0, f"{icon}  {ch.title or '未命名章节'}")
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            self.chapter_tree.addTopLevelItem(item)

    def update_stats(self, total_words: int, target_words: int, chapter_count: int):
        pct = (total_words / target_words * 100) if target_words > 0 else 0
        self.stats_label.setText(f"{chapter_count} 章  ·  {total_words:,} 字  ·  {pct:.1f}%")

    def set_project_title(self, title: str):
        self.project_label.setText(title or "未命名项目")

    def _on_item_changed(self, current, _previous):
        if current:
            idx = current.data(0, Qt.ItemDataRole.UserRole)
            if idx is not None:
                self.chapter_selected.emit(idx)

    def retranslate(self):
        """刷新文本。"""
        self.project_label.setText(t("sidebar_no_project"))
        self.btn_new.setText(t("sidebar_new"))
        self.btn_open.setText(t("sidebar_open"))
        self._section_label.setText(t("sidebar_chapters"))
        self.btn_add_chapter.setText(t("sidebar_add_chapter"))

    def _on_context_menu(self, pos):
        item = self.chapter_tree.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.addAction(QAction(t("ctx_rename"), self))
        menu.addAction(QAction(t("ctx_delete"), self))
        menu.exec(self.chapter_tree.viewport().mapToGlobal(pos))
