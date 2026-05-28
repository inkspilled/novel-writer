"""工作流迷你进度条 + 执行日志。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from ..locales import t


class WorkflowMiniBar(QWidget):

    start_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._log_expanded = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(4)

        # 第一行：标题 + 按钮
        row = QHBoxLayout()
        row.setSpacing(6)
        self._title_label = QLabel(t("workflow_title"))
        self._title_label.setStyleSheet("font-size: 11px; font-weight: 600;")
        row.addWidget(self._title_label)
        row.addStretch()

        self._btn_log = QPushButton("📋")
        self._btn_log.setFixedSize(24, 24)
        self._btn_log.setToolTip("展开/收起执行日志")
        self._btn_log.setStyleSheet("font-size: 12px; padding: 0; border-radius: 4px;")
        self._btn_log.clicked.connect(self._toggle_log)
        row.addWidget(self._btn_log)

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

        # 进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(16)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%p%")
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)

        # 状态文本
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("font-size: 10px; color: gray;")
        layout.addWidget(self._status_label)

        # 执行日志（默认隐藏）
        self._log_area = QTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setMaximumHeight(120)
        self._log_area.setStyleSheet(
            "font-size: 10px; font-family: Consolas, monospace; "
            "background: rgba(0,0,0,0.2); border: 1px solid rgba(255,255,255,0.06); "
            "border-radius: 6px; padding: 4px;"
        )
        self._log_area.setVisible(False)
        layout.addWidget(self._log_area)

    def _toggle_log(self):
        self._log_expanded = not self._log_expanded
        self._log_area.setVisible(self._log_expanded)

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

    def append_log(self, msg: str):
        self._log_area.append(msg)
        # 自动滚动到底部
        sb = self._log_area.verticalScrollBar()
        sb.setValue(sb.maximum())
        # 有新日志时自动展开
        if not self._log_expanded:
            self._log_expanded = True
            self._log_area.setVisible(True)

    def apply_theme(self, colors: dict):
        accent = colors.get("accent", "#6e8efb")
        elevated = colors.get("elevated", "#242430")
        fg3 = colors.get("fg3", "#5a5a66")
        self._progress_bar.setStyleSheet(
            f"QProgressBar {{ background: {elevated}; border-radius: 8px; border: none; "
            f"color: #ffffff; font-size: 10px; font-weight: bold; text-align: center; }}"
            f"QProgressBar::chunk {{ background: {accent}; border-radius: 8px; }}"
        )
        self._status_label.setStyleSheet(f"font-size: 10px; color: {fg3};")
