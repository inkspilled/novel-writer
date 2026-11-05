"""短剧右侧面板 — 和小说一样：上面智能体，下面工作流执行区。"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
    QPushButton, QVBoxLayout, QWidget,
)

from ...core.app_config import load_config
from ...designsystem import get_theme_colors
from ...models.project import Project
from ..agent.office_scene import AgentState, OfficeScene
from ..common.chat_rendering import COLOR_POOL

DRAMA_AGENTS = {
    "showrunner": {"title": "短剧策划", "emoji": "🎬"},
    "writer": {"title": "分集编剧", "emoji": "🧩"},
    "storyboard": {"title": "分镜师", "emoji": "🎥"},
    "prompt": {"title": "视觉提示词", "emoji": "🎨"},
}


class DramaWorkflowBar(QWidget):
    """短剧工作流执行区：一键动作 + 进度 + 日志。"""

    action_requested = Signal(str)
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 6, 12, 8)
        root.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel("短剧工作流")
        title.setStyleSheet("font-size: 11px; font-weight: 700;")
        head.addWidget(title)
        head.addStretch()
        self.status = QLabel("待命")
        self.status.setStyleSheet("font-size: 10px; color: palette(placeholderText);")
        head.addWidget(self.status)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setFixedHeight(22)
        self.btn_stop.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        self.btn_stop.setVisible(False)
        self.btn_stop.clicked.connect(self.stop_requested.emit)
        head.addWidget(self.btn_stop)
        root.addLayout(head)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        root.addWidget(self.progress)

        actions = QGridLayout()
        actions.setHorizontalSpacing(6)
        actions.setVerticalSpacing(6)
        self.buttons: dict[str, QPushButton] = {}
        for index, (key, text) in enumerate((
            ("adapt", "一键改编"),
            ("segment", "智能分集"),
            ("script", "一键转剧本"),
            ("storyboard", "一键分镜"),
            ("assets", "一键资产"),
        )):
            btn = QPushButton(text)
            btn.setMinimumHeight(30)
            btn.setStyleSheet("font-size: 11px; padding: 4px 6px;")
            btn.clicked.connect(lambda checked=False, k=key: self.action_requested.emit(k))
            actions.addWidget(btn, index // 2, index % 2)
            self.buttons[key] = btn
        root.addLayout(actions)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("工作流日志：一键改编 → 一键转剧本 → 一键分镜 → 一键资产")
        self.log.setMinimumHeight(90)
        self.log.setStyleSheet(
            "font-size: 10px; font-family: Menlo, Consolas, monospace;"
            " background: rgba(0,0,0,0.08); border-radius: 6px; padding: 4px;"
        )
        root.addWidget(self.log, 1)

    def set_status(self, text: str, percent: int | None = None) -> None:
        self.status.setText(text)
        if percent is not None:
            self.progress.setValue(max(0, min(100, int(percent))))

    def set_running(self, running: bool, active: str = "") -> None:
        for key, btn in self.buttons.items():
            btn.setEnabled(not running or key == active)
        self.btn_stop.setVisible(running)
        if running:
            self.set_status("执行中…", 15)

    def append_log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{stamp}] {text}")
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def reset(self) -> None:
        self.log.clear()
        self.set_status("待命", 0)


class DramaAgentPanel(QWidget):
    """短剧右侧：OfficeScene 智能体卡片 + 短剧工作流执行区。"""

    action_requested = Signal(str)
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dramaPanel")
        self.setMinimumWidth(300)
        self.setMaximumWidth(420)
        self.project: Project | None = None
        self._colors = {name: COLOR_POOL[i % len(COLOR_POOL)]
                        for i, name in enumerate(DRAMA_AGENTS)}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setObjectName("agentHeader")
        header.setMaximumHeight(36)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 4, 12, 4)
        title = QLabel("短剧智能体")
        title.setStyleSheet("font-size: 12px; font-weight: 700;")
        hl.addWidget(title)
        self.project_label = QLabel("依附当前小说")
        self.project_label.setStyleSheet("font-size: 10px; color: palette(placeholderText);")
        hl.addWidget(self.project_label)
        self.count_label = QLabel("4 个智能体")
        self.count_label.setStyleSheet("font-size: 10px; color: palette(placeholderText);")
        hl.addWidget(self.count_label)
        hl.addStretch()
        root.addWidget(header)

        self.office = OfficeScene()
        self.office.setup_agents(DRAMA_AGENTS, self._colors)
        self.office.agent_clicked.connect(self._on_agent_clicked)
        root.addWidget(self.office)

        self.workflow = DramaWorkflowBar()
        self.workflow.action_requested.connect(self.action_requested)
        self.workflow.stop_requested.connect(self.stop_requested)
        root.addWidget(self.workflow, 1)
        self.refresh_theme()

    def _on_agent_clicked(self, name: str) -> None:
        info = DRAMA_AGENTS.get(name, {})
        self.workflow.append_log(
            f"{info.get('emoji', '🤖')} {info.get('title', name)}：该智能体的自动执行下一阶段接入。"
        )

    def set_project(self, project: Project | None) -> None:
        self.project = project
        if project and project.title:
            self.project_label.setText(f"依附：{project.title}")
        else:
            self.project_label.setText("依附当前小说")
        self.reset_agents()
        self.workflow.reset()

    def set_agent_state(self, name: str, state: AgentState, task: str = "") -> None:
        self.office.set_agent_state(name, state, task)

    def reset_agents(self) -> None:
        self.office.reset_states(AgentState.IDLE)

    def set_status(self, text: str, percent: int | None = None) -> None:
        self.workflow.set_status(text, percent)

    def set_running(self, running: bool, active: str = "") -> None:
        self.workflow.set_running(running, active)

    def append_log(self, text: str) -> None:
        self.workflow.append_log(text)

    def refresh_theme(self) -> None:
        cfg = load_config()
        name = cfg.get("theme", "dark")
        colors = get_theme_colors(name, cfg if name == "custom" else None)
        self.office.apply_theme(colors)


# 兼容旧引用
DramaWorkflowPanel = DramaAgentPanel
