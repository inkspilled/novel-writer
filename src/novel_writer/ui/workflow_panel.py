"""工作流进度面板 — 显示步骤状态、进度条、执行日志。"""
from __future__ import annotations

import asyncio

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QProgressBar, QTextEdit,
    QMessageBox,
)
from PySide6.QtCore import Qt, Signal, QThread

from ..locales import t
from ..core.logger import get_logger

logger = get_logger(__name__)


# ── 步骤状态枚举 ──

STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_ERROR = "error"
STEP_SKIPPED = "skipped"


# ── 单个步骤卡片 ──

class StepCard(QFrame):
    """工作流步骤的状态卡片。"""

    def __init__(self, step_id: str, title: str, needs: str, parent=None):
        super().__init__(parent)
        self.step_id = step_id
        self._status = STEP_PENDING
        self._setup_ui(title, needs)

    def _setup_ui(self, title: str, needs: str):
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(12, 8, 12, 8)
        self._layout.setSpacing(10)

        # 状态图标
        self._icon_label = QLabel("○")
        self._icon_label.setFixedWidth(20)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setStyleSheet("font-size: 16px;")
        self._layout.addWidget(self._icon_label)

        # 步骤信息
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        self._title_label = QLabel(title)
        self._title_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        info_layout.addWidget(self._title_label)
        self._needs_label = QLabel(needs)
        self._needs_label.setStyleSheet("font-size: 11px; color: gray;")
        info_layout.addWidget(self._needs_label)
        self._layout.addLayout(info_layout, 1)

        # 状态文本
        self._status_label = QLabel(t("workflow_step_pending"))
        self._status_label.setStyleSheet("font-size: 11px;")
        self._layout.addWidget(self._status_label)

        # 章节进度（循环步骤用）
        self._chapter_label = QLabel("")
        self._chapter_label.setStyleSheet("font-size: 11px; color: gray;")
        self._chapter_label.setVisible(False)
        self._layout.addWidget(self._chapter_label)

    def set_status(self, status: str):
        self._status = status
        icon_map = {
            STEP_PENDING: ("○", "gray"),
            STEP_RUNNING: ("◉", "#4da6ff"),
            STEP_DONE: ("●", "#50c878"),
            STEP_ERROR: ("✗", "#ff6b6b"),
            STEP_SKIPPED: ("○", "#8e8e9a"),
        }
        icon, color = icon_map.get(status, ("○", "gray"))
        self._icon_label.setText(icon)
        self._icon_label.setStyleSheet(f"font-size: 16px; color: {color};")

        status_text_map = {
            STEP_PENDING: t("workflow_step_pending"),
            STEP_RUNNING: t("workflow_step_running"),
            STEP_DONE: t("workflow_step_done"),
            STEP_ERROR: t("workflow_step_error"),
            STEP_SKIPPED: t("workflow_step_skipped"),
        }
        self._status_label.setText(status_text_map.get(status, ""))
        self._status_label.setStyleSheet(f"font-size: 11px; color: {color};")

        # 高亮运行中的步骤
        if status == STEP_RUNNING:
            self.setStyleSheet("StepCard { background-color: rgba(77,166,255,0.08); border-radius: 8px; }")
        else:
            self.setStyleSheet("")

    def set_chapter_progress(self, current: int, total: int):
        self._chapter_label.setText(t("workflow_chapter_progress", current, total))
        self._chapter_label.setVisible(True)


# ── 工作流执行线程 ──

class WorkflowThread(QThread):
    """后台线程执行工作流。"""
    step_started = Signal(str, int, str)        # step_id, n, agent_title
    step_finished = Signal(str, int, str, str)  # step_id, n, agent_title, response
    step_error = Signal(str, str)               # step_id, error_msg
    progress_updated = Signal(dict)             # progress dict
    log_message = Signal(str)                   # 日志消息
    workflow_done = Signal()                    # 全部完成
    workflow_stopped = Signal()                 # 被停止

    def __init__(self, runner, workflow, progress, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.workflow = workflow
        self.progress = progress
        self._loop = None

    def run(self):
        logger.info("WorkflowThread 开始执行")
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self.runner.on_step_start = self._on_step_start
        self.runner.on_step_end = self._on_step_end
        self.runner.on_error = self._on_error

        try:
            self.log_message.emit("工作流开始执行...")
            result = self._loop.run_until_complete(
                self.runner.run(self.workflow, self.progress)
            )
            self.progress = result
            if self.runner._stop:
                logger.info("工作流已被用户停止")
                self.workflow_stopped.emit()
            else:
                logger.info("工作流执行完成")
                self.workflow_done.emit()
        except Exception as e:
            import traceback
            logger.error("工作流执行异常: %s", e, exc_info=True)
            self.log_message.emit(f"错误: {e}\n{traceback.format_exc()}")
            self.workflow_stopped.emit()
        finally:
            self._loop.close()

    def _on_step_start(self, step_id, n, agent_title):
        self.step_started.emit(step_id, n, agent_title)
        self.log_message.emit(f"▸ {agent_title} 开始 {step_id}" + (f" (第{n}章)" if n > 1 else ""))

    def _on_step_end(self, step_id, n, agent_title, output):
        self.step_finished.emit(step_id, n, agent_title, output)
        self.log_message.emit(f"✓ {agent_title} 完成 {step_id}" + (f" (第{n}章)" if n > 1 else ""))
        self.progress_updated.emit(self.progress)

    def _on_error(self, step_id, error):
        self.step_error.emit(step_id, error)
        self.log_message.emit(f"✗ {step_id}: {error}")

    def request_stop(self):
        self.runner.stop()


# ── 工作流面板主体 ──

class WorkflowPanel(QWidget):
    """工作流进度面板。"""

    workflow_started = Signal()
    workflow_finished = Signal()
    generate_requested = Signal()  # 请求 AI 生成工作流

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workflow = None
        self._runner = None
        self._thread = None
        self._progress = {}
        self._step_cards: dict[str, StepCard] = {}
        self._config = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ──
        header = QWidget()
        header.setObjectName("workflowHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 14, 16, 10)
        header_layout.setSpacing(10)

        self._title_label = QLabel(t("workflow_title"))
        self._title_label.setStyleSheet("font-size: 15px; font-weight: 700;")
        header_layout.addWidget(self._title_label)

        # 总体进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        header_layout.addWidget(self._progress_bar)

        self._progress_label = QLabel(f"0%")
        self._progress_label.setStyleSheet("font-size: 11px; color: gray;")
        header_layout.addWidget(self._progress_label)

        layout.addWidget(header)

        # ── 步骤列表（可滚动） ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._steps_container = QWidget()
        self._steps_layout = QVBoxLayout(self._steps_container)
        self._steps_layout.setContentsMargins(8, 8, 8, 8)
        self._steps_layout.setSpacing(4)
        self._steps_layout.addStretch()
        self._scroll.setWidget(self._steps_container)
        layout.addWidget(self._scroll, 1)

        # ── 执行日志 ──
        self._log_area = QTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setMaximumHeight(120)
        self._log_area.setStyleSheet("font-size: 11px; font-family: Consolas, monospace;")
        self._log_area.setPlaceholderText(t("workflow_log_title"))
        layout.addWidget(self._log_area)

        # ── 底部按钮栏 ──
        btn_bar = QWidget()
        btn_bar.setObjectName("workflowBtnBar")
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(12, 10, 12, 12)
        btn_layout.setSpacing(8)

        self._btn_generate = QPushButton(t("workflow_generate"))
        self._btn_generate.setFixedHeight(34)
        self._btn_generate.clicked.connect(self.generate_requested.emit)
        btn_layout.addWidget(self._btn_generate)

        self._btn_reset = QPushButton(t("workflow_reset"))
        self._btn_reset.setFixedHeight(34)
        self._btn_reset.clicked.connect(self._on_reset)
        btn_layout.addWidget(self._btn_reset)

        btn_layout.addStretch()

        self._btn_stop = QPushButton(t("workflow_stop"))
        self._btn_stop.setFixedHeight(34)
        self._btn_stop.setObjectName("danger")
        self._btn_stop.setVisible(False)
        self._btn_stop.clicked.connect(self._on_stop)
        btn_layout.addWidget(self._btn_stop)

        self._btn_start = QPushButton(t("workflow_start"))
        self._btn_start.setFixedHeight(34)
        self._btn_start.setObjectName("primary")
        self._btn_start.clicked.connect(self._on_start)
        btn_layout.addWidget(self._btn_start)

        layout.addWidget(btn_bar)

    def set_config(self, config: dict):
        self._config = config

    # ── 工作流加载 ──

    def load_workflow(self, workflow_def, progress: dict | None = None):
        """加载工作流定义，显示步骤卡片。"""
        self._workflow = workflow_def
        self._progress = progress or {}

        # 清除旧卡片
        for card in self._step_cards.values():
            card.deleteLater()
        self._step_cards.clear()

        # 创建新卡片
        for step in workflow_def.steps:
            # 确定步骤显示标题
            title = step.id
            needs = step.needs
            if step.repeat > 0:
                needs += f" (×{step.repeat})"
            if step.every > 0:
                needs += f" (每{step.every}章)"
            if step.optional:
                needs += " [可选]"

            card = StepCard(step.id, title, needs)
            self._step_cards[step.id] = card
            self._steps_layout.insertWidget(self._steps_layout.count() - 1, card)

        # 恢复进度状态显示
        self._update_progress_display()
        self._update_buttons()

    # ── 进度更新 ──

    def _update_progress_display(self):
        """根据进度字典更新所有步骤卡片状态。"""
        if not self._workflow:
            return

        total = len(self._workflow.steps)
        done_count = 0

        for step in self._workflow.steps:
            card = self._step_cards.get(step.id)
            if not card:
                continue

            step_prog = self._progress.get(step.id, "pending")
            if step_prog == "done":
                card.set_status(STEP_DONE)
                done_count += 1
            elif isinstance(step_prog, dict):
                # 循环步骤
                current = step_prog.get("current", 0)
                total_ch = step_prog.get("total", step.repeat)
                card.set_chapter_progress(current, total_ch)
                if current >= total_ch:
                    card.set_status(STEP_DONE)
                    done_count += 1
                else:
                    card.set_status(STEP_PENDING)
            elif step_prog == "pending":
                card.set_status(STEP_PENDING)
            else:
                card.set_status(STEP_PENDING)

        # 更新进度条
        pct = int(done_count / total * 100) if total > 0 else 0
        self._progress_bar.setValue(pct)
        self._progress_label.setText(f"{pct}%")

    def _update_buttons(self):
        running = self._thread is not None and self._thread.isRunning()
        has_workflow = self._workflow is not None
        has_progress = bool(self._progress)

        self._btn_start.setVisible(not running)
        self._btn_stop.setVisible(running)
        self._btn_generate.setEnabled(not running)
        self._btn_reset.setEnabled(not running and has_progress)

        if running:
            self._btn_start.setText(t("workflow_resume"))
        elif has_progress:
            self._btn_start.setText(t("workflow_resume"))
        else:
            self._btn_start.setText(t("workflow_start"))

    # ── 按钮事件 ──

    def _on_start(self):
        if not self._workflow:
            return
        self.workflow_started.emit()

    def _on_stop(self):
        if self._thread and self._thread.isRunning():
            self._thread.request_stop()

    def _on_reset(self):
        if QMessageBox.question(
            self, t("dialog_confirm"), t("workflow_confirm_reset")
        ) != QMessageBox.StandardButton.Yes:
            return
        self._progress = {}
        for card in self._step_cards.values():
            card.set_status(STEP_PENDING)
            card._chapter_label.setVisible(False)
        self._progress_bar.setValue(0)
        self._progress_label.setText("0%")
        self._log_area.clear()
        self._update_buttons()

    # ── 工作流执行控制 ──

    def start_execution(self, runner, workflow=None, progress=None):
        """开始执行工作流（由外部调用）。"""
        if workflow:
            self._workflow = workflow
        if progress is not None:
            self._progress = progress

        self._runner = runner
        self._thread = WorkflowThread(runner, self._workflow, self._progress, self)
        self._thread.step_started.connect(self._on_step_started)
        self._thread.step_finished.connect(self._on_step_finished)
        self._thread.step_error.connect(self._on_step_error)
        self._thread.log_message.connect(self._append_log)
        self._thread.progress_updated.connect(self._on_progress_updated)
        self._thread.workflow_done.connect(self._on_workflow_done)
        self._thread.workflow_stopped.connect(self._on_workflow_stopped)
        self._thread.start()
        self._update_buttons()
        self._append_log("── 工作流开始 ──")

    def _on_step_started(self, step_id: str, n: int, agent_title: str):
        card = self._step_cards.get(step_id)
        if card:
            card.set_status(STEP_RUNNING)

    def _on_step_finished(self, step_id: str, n: int, agent_title: str):
        card = self._step_cards.get(step_id)
        if card:
            # 检查是否是循环步骤
            step_prog = self._progress.get(step_id)
            if isinstance(step_prog, dict):
                current = step_prog.get("current", 0)
                total = step_prog.get("total", 0)
                card.set_chapter_progress(current, total)
                if current >= total:
                    card.set_status(STEP_DONE)
            else:
                card.set_status(STEP_DONE)
        self._update_progress_display()

    def _on_step_error(self, step_id: str, error: str):
        card = self._step_cards.get(step_id)
        if card:
            card.set_status(STEP_ERROR)

    def _on_progress_updated(self, progress: dict):
        self._progress = progress
        self._update_progress_display()

    def _on_workflow_done(self):
        self._thread = None
        self._update_buttons()
        self._append_log(f"── {t('workflow_finished')} ──")
        self._progress_bar.setValue(100)
        self._progress_label.setText("100%")
        self.workflow_finished.emit()

    def _on_workflow_stopped(self):
        self._thread = None
        self._update_buttons()
        self._append_log(f"── {t('workflow_stopped')} ──")

    def _append_log(self, msg: str):
        self._log_area.append(msg)

    # ── 主题 ──

    def apply_theme(self, colors: dict):
        border = colors.get("border", "rgba(255,255,255,0.06)")
        accent = colors.get("accent", "#6e8efb")
        fg3 = colors.get("fg3", "#5a5a66")
        self._progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {colors.get('elevated', '#242430')};
                border-radius: 3px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {accent};
                border-radius: 3px;
            }}
        """)
        self._log_area.setStyleSheet(
            f"font-size: 11px; font-family: Consolas, monospace; "
            f"background-color: {colors.get('surface', '#16161d')}; "
            f"color: {colors.get('fg2', '#8e8e9a')}; "
            f"border: 1px solid {border}; border-radius: 8px;"
        )
        self._progress_label.setStyleSheet(f"font-size: 11px; color: {fg3};")

    # ── 国际化 ──

    def retranslate(self):
        self._title_label.setText(t("workflow_title"))
        self._btn_start.setText(t("workflow_start"))
        self._btn_stop.setText(t("workflow_stop"))
        self._btn_generate.setText(t("workflow_generate"))
        self._btn_reset.setText(t("workflow_reset"))
