"""主窗口外壳 Mixin — 按关注点拆分，MainWindow 组合。"""
from .theme_mixin import ThemeMixin
from .llm_mixin import LlmMixin
from .project_mixin import ProjectMixin
from .chat_mixin import ChatMixin
from .settings_mixin import SettingsMixin
from .workflow_mixin import WorkflowMixin

__all__ = [
    "ThemeMixin", "LlmMixin", "ProjectMixin",
    "ChatMixin", "SettingsMixin", "WorkflowMixin",
]
