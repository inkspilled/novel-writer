"""应用主窗口 — 组合 app_shell 各 Mixin，自身只留骨架（状态组装/信号接线/生命周期）。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow, QSplitter,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

from .editor.sidebar import Sidebar
from .editor.editor_panel import EditorPanel
from .agent.agent_panel import AgentPanel
from .common.workers import AgentWorker
from .app_shell import (
    ThemeMixin, LlmMixin, ProjectMixin, ChatMixin, SettingsMixin, WorkflowMixin,
)
from ..locales import t, set_language
from ..models.project import Project
from ..core.llm import LLMClient
from ..core.agents.base import BaseAgent
from ..core.app_config import (
    LOGO_PATH,
    load_config as _load_app_config,
)
from ..core.logger import get_logger

logger = get_logger(__name__)


class MainWindow(
    ThemeMixin, LlmMixin, ProjectMixin, ChatMixin, SettingsMixin, WorkflowMixin,
    QMainWindow,
):
    """应用主窗口。

    行为按 Mixin 分包（ui/app_shell/）：主题外壳 / LLM / 项目 / 对话 / 设置 / 工作流。
    本类只做：状态字段、控件组装、信号接线、生命周期。
    """

    def __init__(self):
        super().__init__()
        logger.info("MainWindow 初始化")
        self.resize(1400, 900)

        # 窗口居中
        screen = self.screen()
        if screen:
            geo = self.frameGeometry()
            geo.moveCenter(screen.availableGeometry().center())
            self.move(geo.topLeft())

        self.config = _load_app_config()
        set_language(self.config.get("language", "zh"))
        self.setWindowTitle(t("window_title"))

        # 窗口图标（标题栏 + macOS dock）
        if LOGO_PATH.exists():
            self.setWindowIcon(QIcon(str(LOGO_PATH)))
        self.project = Project()
        self.agents: dict[str, BaseAgent] = {}
        self.llm: LLMClient | None = None
        self._worker: AgentWorker | None = None
        self._old_workers: list[AgentWorker] = []

        self._setup_ui()
        self._apply_theme()
        self._setup_menu()
        self._setup_menubar_icon()
        self._init_llm()
        self._init_agents()
        self._setup_workflow()

    # ── UI 构建 ──

    def _setup_ui(self):
        self.splitter = splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)
        self.sidebar = Sidebar()
        self.editor = EditorPanel()
        self.agent_panel = AgentPanel()
        self.agent_panel.set_config(self.config)
        splitter.addWidget(self.sidebar)
        splitter.addWidget(self.editor)
        splitter.addWidget(self.agent_panel)
        # 写作工具：正文是主角。侧边栏固定窄条，右栏协作区略窄
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([240, 680, 480])
        self.sidebar.setMinimumWidth(200)
        self.sidebar.setMaximumWidth(320)
        self.agent_panel.setMinimumWidth(340)
        self.setCentralWidget(splitter)
        self.statusBar().showMessage(t("status_ready"))

        self.sidebar.chapter_selected.connect(self._on_chapter_selected)
        self.sidebar.new_project_requested.connect(self._new_project)
        self.sidebar.open_project_requested.connect(self._open_project)
        self.sidebar.chapter_add_requested.connect(self._add_chapter)
        self.agent_panel.agent_run_requested.connect(self._on_agent_run)
        self.agent_panel.chapter_apply_requested.connect(self._on_chapter_apply)
        self.editor.content_changed.connect(self._on_content_changed)
        self.editor.save_requested.connect(self._save_project)
        self.editor.planning_save_requested.connect(self._on_planning_save)

    def closeEvent(self, event):
        self._stop_worker()
        # 工作流线程必须先停并等它退出，否则 QThread: Destroyed while running
        if self._wf_thread is not None and self._wf_thread.isRunning():
            self._wf_thread.request_stop()
            if not self._wf_thread.wait(8000):
                logger.warning("工作流线程 8s 内未退出，继续关闭")
        if self._wf_thread is not None:
            self._old_workers.append(self._wf_thread)
            self._wf_thread = None

        # 去重后关闭所有 LLM 连接（同一 llm 实例可能被多个 agent 共享）
        all_llms: list = []
        seen: set[int] = set()
        for llm in [agent.llm for agent in self.agents.values()] + ([self.llm] if self.llm else []):
            if id(llm) not in seen:
                seen.add(id(llm))
                all_llms.append(llm)

        async def close_all_llms():
            for llm in all_llms:
                try:
                    if hasattr(llm, "close"):
                        await llm.close()
                    elif hasattr(llm, "client") and hasattr(llm.client, "close"):
                        await llm.client.close()
                except Exception as e:
                    logger.debug("关闭 LLM 连接失败（忽略）: %s", e)

        import asyncio
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(close_all_llms())
            loop.close()
        except Exception as e:
            logger.debug("关闭 LLM 事件循环异常（忽略）: %s", e)

        # 保存聊天记录和项目
        self.agent_panel.save_history()
        if self.project.title:
            self._save_project()
        event.accept()
