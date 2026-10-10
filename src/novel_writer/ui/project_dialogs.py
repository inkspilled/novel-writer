"""项目相关对话框 — 打开项目、工作流模式选择。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QComboBox, QSpinBox, QGroupBox, QMessageBox,
)
from PySide6.QtCore import Qt

from ..core import project_io
from ..core.workflow import WorkflowMode
from ..core.logger import get_logger
from ..locales import t

logger = get_logger(__name__)


class OpenProjectDialog(QDialog):
    """项目选择对话框：列出已有项目，支持打开与删除。

    打开（双击或确定）后通过 selected_dir 属性获取所选项目目录。
    """

    def __init__(self, projects: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("dialog_open_project"))
        self.setMinimumSize(400, 360)
        self.selected_dir: Path | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("dialog_select_project")))

        self._list = QListWidget()
        for info in projects:
            item = QListWidgetItem(
                f"{info['title']}  ({info['chapter_count']} {t('sidebar_chapters')} · {info['total_words']:,} {t('editor_words')})")
            item.setData(Qt.ItemDataRole.UserRole, str(info["dir"]))
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        layout.addWidget(self._list)

        btn_row = QHBoxLayout()
        btn_del = QPushButton(t("settings_btn_delete"))
        btn_del.setObjectName("danger")
        btn_row.addWidget(btn_del)
        btn_row.addStretch()
        btn_open = QPushButton(t("settings_btn_open"))
        btn_open.setObjectName("primary")
        btn_row.addWidget(btn_open)
        btn_cancel = QPushButton(t("settings_cancel"))
        btn_row.addWidget(btn_open)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        btn_open.clicked.connect(self._on_open)
        btn_del.clicked.connect(self._on_delete)
        btn_cancel.clicked.connect(self.reject)
        self._list.itemDoubleClicked.connect(lambda _: self._on_open())

    def _on_open(self):
        item = self._list.currentItem()
        if item:
            self.selected_dir = Path(item.data(Qt.ItemDataRole.UserRole))
            self.accept()

    def _on_delete(self):
        item = self._list.currentItem()
        if not item:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        title = item.text().split("  (")[0]
        if QMessageBox.question(self, t("dialog_confirm_delete"), t("msg_delete_project", title)) == QMessageBox.StandardButton.Yes:
            project_io.delete_project(path)
            self._list.takeItem(self._list.row(item))


class WorkflowModeDialog(QDialog):
    """工作流模式选择对话框，返回 (mode, start_ch, end_ch) 或 None。"""

    def __init__(self, project_dir: Path | None, default_end_chapter: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("workflow_title"))
        self.setMinimumWidth(380)
        self._default_end_chapter = default_end_chapter
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # 模式选择
        mode_group = QGroupBox("工作流模式")
        mode_layout = QVBoxLayout(mode_group)
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("📝 新书立意 — 只生成规划文档", WorkflowMode.NEW_BOOK_PLANNING)
        self._mode_combo.addItem("📖 新书全流程 — 从立意到审校", WorkflowMode.NEW_BOOK)
        self._mode_combo.addItem("✍️ 续写 — 从已有章节继续", WorkflowMode.CONTINUE)
        self._mode_combo.addItem("🔍 查漏补缺 — 检查并补写缺失章节", WorkflowMode.FILL_GAPS)
        self._mode_combo.addItem("✅ 校验 — 审核+校对已有章节", WorkflowMode.VALIDATE)
        mode_layout.addWidget(self._mode_combo)
        layout.addWidget(mode_group)

        # 章节范围
        self._chapter_group = QGroupBox("章节范围")
        chapter_layout = QHBoxLayout(self._chapter_group)

        chapter_layout.addWidget(QLabel("从第"))
        self._start_spin = QSpinBox()
        self._start_spin.setRange(1, 99999)
        self._start_spin.setValue(1)
        chapter_layout.addWidget(self._start_spin)
        chapter_layout.addWidget(QLabel("章"))

        chapter_layout.addWidget(QLabel("到第"))
        self._end_spin = QSpinBox()
        self._end_spin.setRange(1, 99999)
        self._end_spin.setValue(default_end_chapter)
        chapter_layout.addWidget(self._end_spin)
        chapter_layout.addWidget(QLabel("章"))

        layout.addWidget(self._chapter_group)

        # 续写模式：自动检测起始章节
        self._project_dir = project_dir
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._on_mode_changed(0)  # 初始化

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton(t("settings_cancel"))
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_ok = QPushButton(t("workflow_start"))
        btn_ok.setObjectName("primary")
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def _on_mode_changed(self, idx):
        mode = self._mode_combo.currentData()
        start_spin, end_spin = self._start_spin, self._end_spin
        if mode == WorkflowMode.CONTINUE:
            # 扫描已有章节数，自动设置起始章节（锁死不可改）
            chapters = project_io.scan_chapters(self._project_dir) if self._project_dir else []
            if chapters:
                last_num = max(c["number"] for c in chapters)
                start_spin.setValue(last_num + 1)
                # 结束章：优先用项目目标章节数（立意/分卷），而非 last+20
                target = self._default_end_chapter
                if target <= last_num:
                    target = last_num + 20  # 目标不比已写的多时才兜底
                end_spin.setValue(target)
            start_spin.setEnabled(False)  # 锁死起始章节
            end_spin.setEnabled(True)
            self._chapter_group.setTitle("续写范围")
        elif mode == WorkflowMode.NEW_BOOK:
            start_spin.setValue(1)
            start_spin.setEnabled(False)
            end_spin.setEnabled(True)
            self._chapter_group.setTitle("目标章节数")
        elif mode == WorkflowMode.NEW_BOOK_PLANNING:
            self._chapter_group.setTitle("无需设置章节范围")
            start_spin.setEnabled(False)
            end_spin.setEnabled(False)
        elif mode == WorkflowMode.FILL_GAPS:
            start_spin.setEnabled(False)
            end_spin.setEnabled(False)
            self._chapter_group.setTitle("自动检测缺失章节")
        elif mode == WorkflowMode.VALIDATE:
            start_spin.setEnabled(False)
            end_spin.setEnabled(False)
            self._chapter_group.setTitle("校验全部章节")

    def get_result(self) -> tuple[WorkflowMode, int, int]:
        """返回 (mode, start_ch, end_ch)。"""
        return (self._mode_combo.currentData(), self._start_spin.value(), self._end_spin.value())
