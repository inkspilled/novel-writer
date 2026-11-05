"""短剧右侧面板 — 与小说侧同一套工作流条 / 智能体卡片组件，只是没有聊天区。

布局、留白、日志行为全部复用小说侧的 `WorkflowMiniBar` + `OfficeScene`，
避免两边各写一份布局、一边修好另一边又跑偏。短剧没有自由对话，
所以工作流条始终按「聊天折叠」模式吃满剩余高度。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ...core.app_config import load_config
from ...designsystem import get_theme_colors
from ...models.project import Project
from ..agent.office_scene import AgentState, OfficeScene
from ..common.chat_rendering import COLOR_POOL
from ..workflow.workflow_bar import WorkflowMiniBar

DRAMA_AGENTS = {
    "showrunner": {"title": "短剧策划", "emoji": "🎬"},
    "writer": {"title": "分集编剧", "emoji": "🧩"},
    "storyboard": {"title": "分镜师", "emoji": "🎥"},
    "prompt": {"title": "视觉提示词", "emoji": "🎨"},
}

_FLOW_HINT = (
    "流程：改编圣经 → 智能分集（写剧本）→ 分镜 → 资产。\n"
    "点「执行模式」选跑哪一步；进度和日志就显示在这里。"
)


class DramaAgentPanel(QWidget):
    """短剧右侧：OfficeScene 智能体卡片 + 工作流迷你条（与小说同款）。"""

    start_requested = Signal()
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

        self.office = OfficeScene()
        self.office.setup_agents(DRAMA_AGENTS, self._colors)
        self.office.agent_clicked.connect(self._on_agent_clicked)
        root.addWidget(self.office)

        self.workflow = WorkflowMiniBar()
        self.workflow.set_summary("短剧工作流")
        self.workflow.set_empty_hint(_FLOW_HINT)
        self.workflow.start_requested.connect(self.start_requested.emit)
        self.workflow.stop_requested.connect(self.stop_requested.emit)
        # 与小说侧「聊天折叠」时完全同款：日志/空提示吃满剩余高度，
        # 条子本身贴顶，不会因为拉伸而把内容挤到面板中间。
        self.workflow.set_fill_available(True)
        root.addWidget(self.workflow, 1)
        self.refresh_theme()

    def _on_agent_clicked(self, name: str) -> None:
        info = DRAMA_AGENTS.get(name, {})
        self.workflow.append_log(
            f"{info.get('emoji', '🤖')} {info.get('title', name)}：该智能体的自动执行下一阶段接入。"
        )

    def set_project(self, project: Project | None) -> None:
        self.project = project
        title = project.title if project and project.title else ""
        self.workflow.set_empty_hint(
            (f"依附：{title}\n" if title else "") + _FLOW_HINT
        )
        self.reset_agents()
        self.workflow.set_has_workflow(bool(title))
        self.workflow.clear_log()
        self.workflow.set_progress(0, "")
        self.workflow.set_summary("短剧工作流")
        self.workflow.set_running(False)

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
        self.office.apply_theme(colors)
        self.workflow.apply_theme(colors)


# 兼容旧引用
DramaWorkflowPanel = DramaAgentPanel
