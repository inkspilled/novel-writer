"""右侧协作栏的公共骨架 — 小说侧和短剧侧共用，不再各拼一份布局。

包含两块所有协作栏都必须有的东西：

- ``OfficeScene`` 智能体卡片（可先建空场景，之后再 ``set_agents``）
- ``WorkflowMiniBar`` 工作流进度/日志条（``self.workflow``，兼容旧名 ``workflow_bar``）

子类只负责在 ``self._root_layout`` 里追加自己的东西（聊天区、对话框入口等），
以及覆盖 ``on_agent_clicked`` 决定点卡片时做什么。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ...core.app_config import load_config
from ...designsystem import get_theme_colors
from ..workflow.workflow_bar import WorkflowMiniBar
from .office_scene import AgentState, OfficeScene


class AgentDock(QWidget):
    """智能体卡片 + 工作流条；两侧外观、留白、日志行为保持一致。"""

    start_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent=None, *, workflow_title: str = "",
                 empty_hint: str = ""):
        super().__init__(parent)
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

        self.office = OfficeScene()
        self.office.agent_clicked.connect(self._dispatch_agent_clicked)
        self._root_layout.addWidget(self.office)

        self.workflow = WorkflowMiniBar()
        if workflow_title:
            self.workflow.set_summary(workflow_title)
        if empty_hint:
            self.workflow.set_empty_hint(empty_hint)
        self.workflow.start_requested.connect(self.start_requested.emit)
        self.workflow.stop_requested.connect(self.stop_requested.emit)
        # 与小说侧「聊天折叠」同款：日志/空态提示吃满剩余高度，条子贴顶
        self.workflow.set_fill_available(True)
        self._root_layout.addWidget(self.workflow, 1)
        self.workflow_bar = self.workflow  # 兼容小说侧旧引用

    # ── 子类钩子 ──

    def on_agent_clicked(self, name: str) -> None:
        """点击某张智能体卡片时调用；默认什么都不做。"""

    def _dispatch_agent_clicked(self, name: str) -> None:
        self.on_agent_clicked(name)

    # ── 公共 API ──

    def set_agents(self, agents_cfg: dict, colors: dict) -> None:
        self.office.setup_agents(agents_cfg, colors)

    def set_agent_state(self, name: str, state: AgentState, task: str = "") -> None:
        self.office.set_agent_state(name, state, task)

    def reset_agents(self) -> None:
        self.office.reset_states(AgentState.IDLE)

    def set_status(self, text: str, percent: int) -> None:
        self.workflow.set_progress(max(0, min(100, int(percent))), text)

    def set_running(self, running: bool) -> None:
        self.workflow.set_running(running)

    def set_usage(self, usage: dict | None) -> None:
        self.workflow.set_usage(usage)

    def append_log(self, text: str) -> None:
        self.workflow.append_log(text)

    def refresh_theme(self) -> None:
        cfg = load_config()
        name = cfg.get("theme", "dark")
        colors = get_theme_colors(name, cfg if name == "custom" else None)
        self.apply_theme(colors)

    def apply_theme(self, colors: dict) -> None:
        self.office.apply_theme(colors)
        self.workflow.apply_theme(colors)
