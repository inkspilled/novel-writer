"""短剧右侧面板 — 复用小说侧同一套 AgentDock（卡片 + 工作流条）。

不再自己拼布局：卡片、工作流条、空态提示、日志行为都由
`ui/agent/agent_dock.py` 提供，短剧只补「依附当前小说」与项目切换逻辑。
"""
from __future__ import annotations

from ...models.project import Project
from ..agent.agent_dock import AgentDock
from ..common.chat_rendering import COLOR_POOL

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


class DramaAgentPanel(AgentDock):
    """短剧右侧：与小说侧同款协作栏，没有聊天区。"""

    def __init__(self, parent=None):
        super().__init__(parent, workflow_title="短剧工作流", empty_hint=_FLOW_HINT)
        self.setObjectName("dramaPanel")
        self.setMinimumWidth(300)
        self.setMaximumWidth(420)
        self.project: Project | None = None
        self._colors = {name: COLOR_POOL[i % len(COLOR_POOL)]
                        for i, name in enumerate(DRAMA_AGENTS)}
        self.set_agents(DRAMA_AGENTS, self._colors)
        self.refresh_theme()

    def on_agent_clicked(self, name: str) -> None:
        info = DRAMA_AGENTS.get(name, {})
        self.append_log(
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


# 兼容旧引用
DramaWorkflowPanel = DramaAgentPanel
