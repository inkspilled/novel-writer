"""应用主窗口 — 组合 app_shell 各 Mixin，自身只留骨架（状态组装/信号接线/生命周期）。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon

from .editor.sidebar import Sidebar
from .editor.editor_panel import EditorPanel
from .drama.drama_workspace import DramaWorkspace
from .drama.drama_panel import DramaAgentPanel
from .drama.drama_worker import DramaAIWorker
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
        self._drama_loaded_dir: str | None = None
        self._drama_worker: DramaAIWorker | None = None
        self._old_drama_workers: list[DramaAIWorker] = []
        self.agents: dict[str, BaseAgent] = {}
        self.llm: LLMClient | None = None
        self._worker: AgentWorker | None = None
        self._old_workers: list[AgentWorker] = []
        # 正文输入防抖：避免每个击键都整章原子写盘
        self._pending_chapter = None
        self._content_save_timer = QTimer(self)
        self._content_save_timer.setSingleShot(True)
        self._content_save_timer.setInterval(350)
        self._content_save_timer.timeout.connect(self._flush_content_save)

        self._setup_ui()
        self._apply_theme()
        self._setup_menu()
        self._setup_menubar_icon()
        self._init_llm()
        self._init_agents()
        self._setup_workflow()

    # ── UI 构建 ──

    def _setup_ui(self):
        # 顶层大页签：小说 / 短剧。短剧依附当前小说项目，不单独建项目。
        self.mode_tabs = QTabWidget()
        self.mode_tabs.setObjectName("modeTabs")
        self.mode_tabs.setDocumentMode(True)

        # ── 小说页签：侧栏 + 正文/规划/世界 + 智能体 ──
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
        novel_page = QWidget()
        novel_layout = QVBoxLayout(novel_page)
        novel_layout.setContentsMargins(0, 0, 0, 0)
        novel_layout.setSpacing(0)
        novel_layout.addWidget(splitter)
        self.mode_tabs.addTab(novel_page, "小说")

        # ── 短剧页签：独立工作区 + 独立工作流入口 ──
        self.drama_workspace = DramaWorkspace()
        self.drama_panel = DramaAgentPanel()
        drama_splitter = QSplitter(Qt.Orientation.Horizontal)
        drama_splitter.setHandleWidth(1)
        drama_splitter.setChildrenCollapsible(False)
        drama_splitter.addWidget(self.drama_workspace)
        drama_splitter.addWidget(self.drama_panel)
        drama_splitter.setStretchFactor(0, 4)
        drama_splitter.setStretchFactor(1, 1)
        drama_splitter.setSizes([900, 320])
        self.drama_splitter = drama_splitter
        drama_page = QWidget()
        drama_layout = QVBoxLayout(drama_page)
        drama_layout.setContentsMargins(0, 0, 0, 0)
        drama_layout.setSpacing(0)
        drama_layout.addWidget(drama_splitter)
        self.drama_page = drama_page
        self.mode_tabs.addTab(drama_page, "短剧")
        self.mode_tabs.setTabToolTip(1, "短剧依附当前小说项目，数据写在 drama/；页面与工作流独立")

        self.setCentralWidget(self.mode_tabs)
        self.statusBar().showMessage(t("status_ready"))

        self.sidebar.chapter_selected.connect(self._on_chapter_selected)
        self.sidebar.new_project_requested.connect(self._new_project)
        self.sidebar.open_project_requested.connect(self._open_project)
        self.sidebar.chapter_add_requested.connect(self._add_chapter)
        self.agent_panel.agent_run_requested.connect(self._on_agent_run)
        self.agent_panel.chapter_apply_requested.connect(self._on_chapter_apply)
        self.editor.content_changed.connect(self._on_content_changed)
        self.editor.planning_save_requested.connect(self._on_planning_save)
        self.editor.chapter_activate_requested.connect(self._open_chapter_number)
        self.drama_workspace.status_message.connect(
            lambda msg: self.statusBar().showMessage(msg, 3000)
        )
        self.drama_panel.action_requested.connect(self._on_drama_action)
        self.drama_panel.stop_requested.connect(self._drama_stop)
        self.mode_tabs.currentChanged.connect(self._on_mode_tab_changed)

    def closeEvent(self, event):
        self._stop_worker()
        if self._drama_worker is not None and self._drama_worker.isRunning():
            self._drama_worker.request_stop()
            if not self._drama_worker.wait(8000):
                logger.warning("短剧生成线程 8s 内未退出，继续关闭")
        if self._drama_worker is not None:
            self._old_drama_workers.append(self._drama_worker)
            self._drama_worker = None
        for old in list(self._old_drama_workers):
            if old.isRunning():
                old.wait(3000)
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
