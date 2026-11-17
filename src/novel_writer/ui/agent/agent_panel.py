"""小说工作流面板：进度、运行控制和日志。"""
from .agent_dock import AgentDock
from ..common.chat_rendering import COLOR_POOL
from ..common.styles import get_theme_colors
from ...locales import t


class AgentPanel(AgentDock):
    def __init__(self, parent=None):
        super().__init__(parent, workflow_title=t("workflow_title"))
        self._agents_cfg = {}
        self._config = {}

    def set_config(self, config):
        self._config = config

    def update_agent_buttons(self, agents_cfg):
        self._agents_cfg = agents_cfg
        self.set_agents(agents_cfg, {name: COLOR_POOL[i % len(COLOR_POOL)]
                                   for i, name in enumerate(agents_cfg)})

    def refresh_theme(self):
        self.apply_theme(get_theme_colors(self._config.get("theme", "light"), self._config))

    def retranslate(self):
        pass

    def on_workflow_step_started(self, step_id, n, agent_title):
        self.workflow_bar.set_current_step(step_id, agent_title, n, 0)

    def on_workflow_step_finished(self, step_id, n, agent_title):
        pass

    def on_workflow_step_error(self, step_id, error):
        pass

    def on_workflow_progress(self, percent, step_text=""):
        self.workflow_bar.set_progress(percent, step_text)

    def on_workflow_started(self):
        self.workflow_bar.set_running(True)
        self.workflow_bar.append_log("── 工作流开始 ──")

    def on_workflow_finished(self):
        self.workflow_bar.set_running(False)
        self.workflow_bar.append_log("── <b>工作流完成！</b> ──")
