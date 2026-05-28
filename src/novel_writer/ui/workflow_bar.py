"""工作流迷你进度条 — 嵌入智能体面板的紧凑工作流显示。"""
from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar
from PySide6.QtCore import Qt, Signal

from ..locales import t


class WorkflowMiniBar(QWidget):

    start_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 6)
        layout.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(6)

        self._title_label = QLabel(t("workflow_title"))
        self._title_label.setStyleSheet("font-size: 11px; font-weight: 600;")
        row.addWidget(self._title_label)
        row.addStretch()

        self._btn_start = QPushButton(t("workflow_start"))
        self._btn_start.setFixedHeight(24)
        self._btn_start.setObjectName("primary")
        self._btn_start.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        self._btn_start.clicked.connect(self.start_requested.emit)
        row.addWidget(self._btn_start)

        self._btn_stop = QPushButton(t("workflow_stop"))
        self._btn_stop.setFixedHeight(24)
        self._btn_stop.setObjectName("danger")
        self._btn_stop.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        self._btn_stop.setVisible(False)
        self._btn_stop.clicked.connect(self.stop_requested.emit)
        row.addWidget(self._btn_stop)
        layout.addLayout(row)

        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("font-size: 10px; color: gray;")
        layout.addWidget(self._status_label)

    def set_progress(self, percent: int, step_text: str = ""):
        self._progress_bar.setValue(percent)
        if step_text:
            self._status_label.setText(step_text)

    def set_running(self, running: bool):
        self._btn_start.setVisible(not running)
        self._btn_stop.setVisible(running)

    def set_has_workflow(self, has: bool):
        self._btn_start.setEnabled(has)

    def set_current_step(self, step_id: str, agent_title: str, n: int = 0, total: int = 0):
        if total > 0:
            self._status_label.setText(f"▸ {agent_title} — {t('workflow_chapter_progress', n, total)}")
        else:
            self._status_label.setText(f"▸ {agent_title}")

    def apply_theme(self, colors: dict):
        accent = colors.get("accent", "#6e8efb")
        elevated = colors.get("elevated", "#242430")
        fg3 = colors.get("fg3", "#5a5a66")
        self._progress_bar.setStyleSheet(
            f"QProgressBar {{ background: {elevated}; border-radius: 2px; border: none; }}"
            f"QProgressBar::chunk {{ background: {accent}; border-radius: 2px; }}"
        )
        self._status_label.setStyleSheet(f"font-size: 10px; color: {fg3};")
