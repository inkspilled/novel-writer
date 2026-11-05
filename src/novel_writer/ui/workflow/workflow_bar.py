"""工作流迷你进度条 + 执行日志。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from ...locales import t
from datetime import datetime


class WorkflowMiniBar(QWidget):

    start_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._log_expanded = False
        self._fill = False
        self._has_log = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(2)
        # 没有日志时不要把所有内容垂直居中（曾被拉伸后「跑中间去」）；
        # 有日志时日志区是 Expanding，仍会吃掉剩余高度。
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 单行：标题 + 进度条 + 状态 + 按钮
        row = QHBoxLayout()
        row.setSpacing(6)

        self._title_label = QLabel(t("workflow_title"))
        self._title_label.setStyleSheet("font-size: 11px; font-weight: 600;")
        row.addWidget(self._title_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(12)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%p%")
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        row.addWidget(self._progress_bar, 1)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("font-size: 10px; color: gray;")
        row.addWidget(self._status_label)

        self._usage_label = QLabel("")
        self._usage_label.setStyleSheet("font-size: 10px; color: gray;")
        self._usage_label.setToolTip("本次运行累计 token（含工具/会审/感知）")
        self._usage_label.setVisible(False)
        row.addWidget(self._usage_label)

        self._btn_log = QPushButton("📋")
        self._btn_log.setFixedSize(20, 20)
        self._btn_log.setToolTip("展开/收起执行日志")
        self._btn_log.setStyleSheet("font-size: 11px; padding: 0; border-radius: 4px;")
        self._btn_log.clicked.connect(self._toggle_log)
        row.addWidget(self._btn_log)

        self._btn_start = QPushButton("执行模式")
        self._btn_start.setFixedHeight(22)
        self._btn_start.setObjectName("primary")
        self._btn_start.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        self._btn_start.clicked.connect(self.start_requested.emit)
        row.addWidget(self._btn_start)

        self._btn_stop = QPushButton(t("workflow_stop"))
        self._btn_stop.setFixedHeight(22)
        self._btn_stop.setObjectName("danger")
        self._btn_stop.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        self._btn_stop.setVisible(False)
        self._btn_stop.clicked.connect(self.stop_requested.emit)
        row.addWidget(self._btn_stop)

        layout.addLayout(row)

        # 执行日志（默认隐藏）
        self._log_area = QTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setPlaceholderText("工作流日志")
        self._log_area.setMinimumHeight(80)
        self._log_area.setMaximumHeight(180)
        self._log_area.setStyleSheet(
            "font-size: 10px; font-family: Menlo, Consolas, monospace; "
            "background: rgba(0,0,0,0.2); border: 1px solid rgba(255,255,255,0.06); "
            "border-radius: 6px; padding: 4px;"
        )
        self._log_area.setVisible(False)
        layout.addWidget(self._log_area)

        # 空日志占位：不要用一个空的深灰 QTextEdit 撑满右栏，换成一句说明。
        self._log_hint = QLabel(t("workflow_log_hint"))
        self._log_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._log_hint.setWordWrap(True)
        self._log_hint.setStyleSheet(
            "font-size: 11px; color: palette(placeholderText); padding: 12px;"
        )
        self._log_hint.setVisible(False)
        layout.addWidget(self._log_hint, 1)

    def set_empty_hint(self, text: str):
        """空日志时的占位文案（短剧侧用来写流程说明，小说侧保持默认）。"""
        self._log_hint.setText(text or t("workflow_log_hint"))

    def _refresh_log_visibility(self):
        """日志区只在「有内容」或「用户主动展开」时出现；空的时候显示提示。"""
        wanted = self._fill or self._log_expanded
        self._log_area.setVisible(wanted and self._has_log)
        self._log_hint.setVisible(wanted and not self._has_log)

    def _toggle_log(self):
        self._log_expanded = not self._log_expanded
        self._refresh_log_visibility()

    def set_summary(self, text: str, percent: int | None = None):
        """标题位显示两段式进度（规划/正文）；percent 给正文进度时覆盖进度条。"""
        self._title_label.setText(text or t("workflow_title"))
        if percent is not None:
            self._progress_bar.setValue(max(0, min(100, int(percent))))

    def set_progress(self, percent: int, step_text: str = ""):
        self._progress_bar.setValue(percent)
        if step_text:
            self._status_label.setText(step_text)

    def set_running(self, running: bool):
        self._btn_start.setVisible(not running)
        self._btn_stop.setVisible(running)

    def set_has_workflow(self, has: bool):
        self._btn_start.setEnabled(has)

    def set_log_expands(self, expands: bool):
        """日志区是否吃满剩余高度（只改尺寸策略，保留 📋 的展开/收起语义）。"""
        height = 16777215 if expands else 180
        policy = (
            QSizePolicy.Policy.Expanding if expands else QSizePolicy.Policy.Preferred
        )
        self._log_area.setMaximumHeight(height)
        self._log_area.setSizePolicy(policy, policy)

    def set_fill_available(self, fill: bool):
        """聊天折叠时让日志区吃掉剩余高度；展开时恢复 160px 上限。"""
        self._fill = fill
        self._log_area.setMaximumHeight(16777215 if fill else 180)
        self.setMaximumHeight(16777215 if fill else 160)
        self._log_area.setSizePolicy(
            QSizePolicy.Policy.Expanding if fill else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding if fill else QSizePolicy.Policy.Preferred,
        )
        self._log_hint.setSizePolicy(
            QSizePolicy.Policy.Expanding if fill else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding if fill else QSizePolicy.Policy.Preferred,
        )
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding if fill else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding if fill else QSizePolicy.Policy.Preferred,
        )
        self._refresh_log_visibility()

    def set_usage(self, usage: dict | None):
        usage = usage or {}
        total = usage.get("total_tokens")
        if not total:
            total = sum(int(v) for k, v in usage.items() if k.endswith("tokens") and isinstance(v, (int, float)))
        if total:
            text = f"{total/1000:.1f}k tok" if total >= 1000 else f"{int(total)} tok"
            self._usage_label.setText(text)
            self._usage_label.setVisible(True)
        else:
            self._usage_label.setText("")
            self._usage_label.setVisible(False)

    def set_current_step(self, step_id: str, agent_title: str, n: int = 0, total: int = 0):
        if total > 0:
            self._status_label.setText(f"▸ {agent_title} — {t('workflow_chapter_progress', n, total)}")
        else:
            self._status_label.setText(f"▸ {agent_title}")

    def append_log(self, msg: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        self._log_area.append(f"<span style='color:#8e8e9a'>[{stamp}]</span> {msg}")
        # 自动滚动到底部
        sb = self._log_area.verticalScrollBar()
        sb.setValue(sb.maximum())
        # 有新日志时才把空占位换成日志区
        self._has_log = True
        if not self._log_expanded and not self._fill:
            self._log_expanded = True
        self._refresh_log_visibility()

    def clear_log(self):
        """清空日志并回到空占位（短剧切换项目时用）。"""
        self._log_area.clear()
        self._has_log = False
        self._refresh_log_visibility()

    def apply_theme(self, colors: dict):
        accent = colors.get("accent", "#6e8efb")
        elevated = colors.get("elevated", "#242430")
        fg3 = colors.get("fg3", "#5a5a66")
        self._progress_bar.setStyleSheet(
            f"QProgressBar {{ background: {elevated}; border-radius: 6px; border: none; "
            f"color: {colors.get('fg', '#ffffff')}; font-size: 9px; font-weight: bold; text-align: center; }}"
            f"QProgressBar::chunk {{ background: {accent}; border-radius: 6px; }}"
        )
        self._status_label.setStyleSheet(f"font-size: 10px; color: {fg3};")
