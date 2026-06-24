from __future__ import annotations

import asyncio
import json
from pathlib import Path
from functools import partial

from PySide6.QtWidgets import (
    QMainWindow, QSplitter, QInputDialog,
    QMessageBox, QDialog, QVBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QHBoxLayout, QLabel, QComboBox, QSpinBox, QGroupBox,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QAction, QKeySequence, QIcon, QPixmap

from .sidebar import Sidebar
from .editor_panel import EditorPanel
from .agent_panel import AgentPanel
from .workflow_panel import WorkflowThread
from .settings_dialog import AppearanceDialog, ModelDialog, AgentDialog
from .styles import build_style
from ..locales import t, set_language
from ..models.project import Project
from ..core.llm import LLMClient
from ..core.agents import load_agents
from ..core.agents.base import BaseAgent, AgentConfig
from ..core import project_io
from ..core.workflow import WorkflowRunner, WorkflowDef, DEFAULT_WORKFLOW, WorkflowMode, build_workflow
from ..core.logger import get_logger

logger = get_logger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
CONFIG_PATH = DATA_DIR / "config.json"
PROJECTS_DIR = DATA_DIR / "projects"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class AgentWorker(QThread):
    """后台线程执行 Agent 调用，支持流式输出。"""
    chunk_received = Signal(str)  # 流式文本块
    finished = Signal(str)  # 完成时的完整响应
    error = Signal(str)

    def __init__(self, agent: BaseAgent, user_input: str, context: str = ""):
        super().__init__()
        self.agent = agent
        self.user_input = user_input
        self.context = context
        self._cancelled = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    def cancel(self):
        """请求取消进行中的任务。"""
        self._cancelled = True
        if self._loop and self._task and not self._task.done():
            self._loop.call_soon_threadsafe(self._task.cancel)

    def run(self):
        logger.debug("AgentWorker.run: agent=%r, input=%r", self.agent.name, self.user_input[:80])
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            full_response = ""
            async def collect_stream():
                nonlocal full_response
                async for chunk in self.agent.stream_run(self.user_input, self.context):
                    if self._cancelled:
                        break
                    full_response += chunk
                    # C-09 修复：信号发射异常记录日志，不静默吞掉
                    try:
                        self.chunk_received.emit(chunk)
                    except RuntimeError as e:
                        # Widget 可能已销毁，记录并退出
                        logger.debug("Signal emit failed (widget destroyed): %s", e)
                        break
                return full_response

            self._task = self._loop.create_task(collect_stream())
            self._loop.run_until_complete(self._task)
            if not self._cancelled:
                self.finished.emit(full_response)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if not self._cancelled:
                logger.error("AgentWorker error: %s", e)
                try:
                    self.error.emit(str(e)[:500])
                except RuntimeError:
                    pass
        finally:
            # 不关闭 LLM 客户端 — 它是共享的，由 MainWindow 管理生命周期
            self._loop.close()
            self._loop = None
            self._task = None


class MainWindow(QMainWindow):

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

        self.config = self._load_config()
        set_language(self.config.get("language", "zh"))
        self.setWindowTitle(t("window_title"))

        # 窗口图标（标题栏 + macOS dock）
        logo = PROJECT_ROOT / "logo.png"
        if logo.exists():
            self.setWindowIcon(QIcon(str(logo)))
        self.project = Project()
        self.agents: dict[str, BaseAgent] = {}
        self.llm = None
        self._worker = None
        self._old_workers: list[AgentWorker] = []

        self._setup_ui()
        self._apply_theme()
        self._setup_menu()
        self._setup_menubar_icon()
        self._init_llm()
        self._init_agents()
        self._setup_workflow()

    def _setup_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        self.sidebar = Sidebar()
        self.editor = EditorPanel()
        self.agent_panel = AgentPanel()
        self.agent_panel.set_config(self.config)
        splitter.addWidget(self.sidebar)
        splitter.addWidget(self.editor)
        splitter.addWidget(self.agent_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([220, 560, 620])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage(t("status_ready"))

        self.sidebar.chapter_selected.connect(self._on_chapter_selected)
        self.sidebar.new_project_requested.connect(self._new_project)
        self.sidebar.open_project_requested.connect(self._open_project)
        self.sidebar.chapter_add_requested.connect(self._add_chapter)
        self.agent_panel.agent_run_requested.connect(self._on_agent_run)
        self.editor.content_changed.connect(self._on_content_changed)
        self.editor.save_requested.connect(self._save_project)
        self.editor.planning_save_requested.connect(self._on_planning_save)

    def _setup_menu(self):
        menubar = self.menuBar()

        # 文件
        self._file_menu = menubar.addMenu(t("menu_file"))
        self._new_action = QAction(t("menu_new_project"), self)
        self._new_action.setShortcut(QKeySequence.StandardKey.New)
        self._new_action.triggered.connect(self._new_project)
        self._file_menu.addAction(self._new_action)
        self._open_action = QAction(t("menu_open_project"), self)
        self._open_action.setShortcut(QKeySequence("Ctrl+O"))
        self._open_action.triggered.connect(self._open_project)
        self._file_menu.addAction(self._open_action)
        self._save_action = QAction(t("menu_save_project"), self)
        self._save_action.setShortcut(QKeySequence.StandardKey.Save)
        self._save_action.triggered.connect(self._save_project)
        self._file_menu.addAction(self._save_action)
        self._export_menu = self._file_menu.addMenu(t("menu_export"))
        self._export_txt_action = QAction(t("menu_export_txt"), self)
        self._export_txt_action.triggered.connect(self._export_txt)
        self._export_menu.addAction(self._export_txt_action)
        self._export_epub_action = QAction(t("menu_export_epub"), self)
        self._export_epub_action.triggered.connect(self._export_epub)
        self._export_menu.addAction(self._export_epub_action)
        self._export_pdf_action = QAction(t("menu_export_pdf"), self)
        self._export_pdf_action.triggered.connect(self._export_pdf)
        self._export_menu.addAction(self._export_pdf_action)
        self._settings_action = QAction("项目设置", self)
        self._settings_action.setShortcut(QKeySequence("Ctrl+Shift+,"))
        self._settings_action.triggered.connect(self._open_project_settings)
        self._file_menu.addAction(self._settings_action)
        self._file_menu.addSeparator()
        self._exit_action = QAction(t("menu_exit"), self)
        self._exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self._exit_action.triggered.connect(self.close)
        self._file_menu.addAction(self._exit_action)

        # 模型 & 智能体
        self._model_agent_menu = menubar.addMenu(t("menu_model_agent"))
        self._model_action = QAction(t("menu_model_open"), self)
        self._model_action.triggered.connect(self._open_model_settings)
        self._model_agent_menu.addAction(self._model_action)
        self._manage_action = QAction(t("menu_agent_manage"), self)
        self._manage_action.triggered.connect(self._open_agent_manage)
        self._model_agent_menu.addAction(self._manage_action)

        # 工作流
        self._workflow_menu = menubar.addMenu(t("workflow_menu"))
        self._workflow_open_action = QAction(t("workflow_menu_open"), self)
        self._workflow_open_action.setShortcut(QKeySequence("Ctrl+Shift+W"))
        self._workflow_open_action.triggered.connect(self._open_workflow_panel)
        self._workflow_menu.addAction(self._workflow_open_action)
        self._workflow_default_action = QAction(t("workflow_menu_default"), self)
        self._workflow_default_action.triggered.connect(self._load_default_workflow)
        self._workflow_menu.addAction(self._workflow_default_action)

        # 关于
        self._about_menu = menubar.addMenu(t("menu_about"))
        self._appearance_action = QAction(t("menu_appearance_open"), self)
        self._appearance_action.setShortcut(QKeySequence("Ctrl+,"))
        self._appearance_action.triggered.connect(self._open_appearance)
        self._about_menu.addAction(self._appearance_action)
        self._about_action = QAction(t("menu_about_app"), self)
        self._about_action.triggered.connect(self._show_about)
        self._about_menu.addAction(self._about_action)

    def _setup_menubar_icon(self):
        """在状态栏左侧显示应用小图标。"""
        status_icon = ASSETS_DIR / "status_icon.png"
        if status_icon.exists():
            label = QLabel()
            pixmap = QPixmap(str(status_icon)).scaled(
                18, 18, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            label.setPixmap(pixmap)
            label.setContentsMargins(8, 0, 0, 0)
            self.statusBar().addPermanentWidget(label)

    def _load_config(self) -> dict:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        return {}

    def _save_config(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8-sig")

    def _create_llm(self, provider: dict):
        """根据供应商配置创建 LLM 实例。统一走 OpenAI 兼容协议。"""
        model = provider.get("model", "")
        if not model:
            return None
        base_url = provider.get("base_url", "")
        api_key = provider.get("api_key", "")
        # Ollama 使用 /v1 端点
        if provider.get("type") == "ollama":
            base_url = base_url.rstrip("/") + "/v1"
        if not base_url:
            base_url = "https://api.openai.com/v1"
        return LLMClient(model=model, api_key=api_key, base_url=base_url)

    def _init_llm(self):
        provider = self.config.get("current_provider")
        if not provider:
            return
        self.llm = self._create_llm(provider)

    def _init_agents(self):
        self.agents.clear()

        # 从 agents.json 读取配置
        agents_cfg = load_agents()

        # 先更新按钮（不依赖 LLM）
        self.agent_panel.update_agent_buttons(agents_cfg)

        if not self.llm:
            return

        saved_models = self.config.get("saved_models", {})

        for name, info in agents_cfg.items():
            agent_model_key = info.get("model", "").strip()
            if agent_model_key:
                saved = saved_models.get(agent_model_key)
                if saved:
                    llm = self._create_llm(saved) or self.llm
                else:
                    provider = self.config.get("current_provider", {}).copy()
                    provider["model"] = agent_model_key
                    llm = self._create_llm(provider) or self.llm
            else:
                llm = self.llm

            agent_config = AgentConfig(
                name=name,
                role=info.get("title", name),
                title=info.get("title", name),
                system_prompt=info.get("system_prompt", ""),
                skills=info.get("skills", []),
                model=agent_model_key,
                temperature=info.get("temperature", 0.7),
                max_tokens=info.get("max_tokens", 4096),
            )
            self.agents[name] = BaseAgent(agent_config, llm)

    def _new_project(self):
        # 先保存当前项目
        if self.project.title:
            self._save_project()

        from .new_project_dialog import NewProjectDialog
        dialog = NewProjectDialog(llm=self.llm, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        data = dialog.get_data()
        title = data["title"]

        # 创建项目目录
        safe_name = title.replace(" ", "_").replace("/", "_")
        project_dir = PROJECTS_DIR / safe_name
        project_dir.mkdir(parents=True, exist_ok=True)

        # 复制封面到项目目录
        cover_rel = ""
        if data["cover"]:
            import shutil
            src = Path(data["cover"])
            ext = src.suffix or ".png"
            dst = project_dir / f"cover{ext}"
            shutil.copy2(src, dst)
            cover_rel = f"cover{ext}"

        self.project = Project(
            title=title,
            genre=data["genre"],
            style=data["style"],
            theme=data["theme"],
            direction=data["direction"],
            target_words=data["target_words"],
            synopsis=data["synopsis"],
            cover=cover_rel,
        )
        self.project.set_project_dir(project_dir)
        project_io.init_project_dir(project_dir)

        # 保存立意文档
        ideation_content = f"# {title}\n\n"
        ideation_content += f"## 核心立意\n\n{data['theme']}\n\n"
        ideation_content += f"## 规划方向\n\n{data['direction']}\n\n"
        if data["synopsis"]:
            ideation_content += f"## 简介\n\n{data['synopsis']}\n\n"
        ideation_content += f"## 基本信息\n\n"
        ideation_content += f"- 题材：{data['genre']}\n"
        ideation_content += f"- 风格：{data['style']}\n"
        ideation_content += f"- 目标字数：{data['target_words']:,}\n"
        project_io.write_md(project_dir / "planning" / "立意.md", ideation_content)

        self.project.save()
        self.agent_panel.set_project_db(project_dir)
        self._load_workflow_for_project()
        self.sidebar.set_project_title(title)
        self.sidebar.load_chapters(self.project.chapters)
        self.sidebar.update_stats(0, self.project.target_words, len(self.project.chapters))
        self.editor.clear()
        self.editor.set_project_dir(project_dir)
        self.statusBar().showMessage(t("status_created", title))
        logger.info("新项目已创建: %s (%s)", title, project_dir)

    def _open_project(self):
        """打开已有项目。"""
        # 先保存当前项目
        if self.project.title:
            self._save_project()
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        projects_info = project_io.list_projects(PROJECTS_DIR)
        if not projects_info:
            QMessageBox.information(self, t("dialog_prompt"), t("msg_no_projects_saved"))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(t("dialog_open_project"))
        dialog.setMinimumSize(400, 360)
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel(t("dialog_select_project")))
        project_list = QListWidget()
        for info in projects_info:
            item = QListWidgetItem(
                f"{info['title']}  ({info['chapter_count']} {t('sidebar_chapters')} · {info['total_words']:,} {t('editor_words')})")
            item.setData(Qt.ItemDataRole.UserRole, str(info["dir"]))
            project_list.addItem(item)
        if project_list.count() > 0:
            project_list.setCurrentRow(0)
        layout.addWidget(project_list)

        btn_row = QHBoxLayout()
        btn_del = QPushButton(t("settings_btn_delete"))
        btn_del.setObjectName("danger")
        btn_row.addWidget(btn_del)
        btn_row.addStretch()
        btn_open = QPushButton(t("settings_btn_open"))
        btn_open.setObjectName("primary")
        btn_cancel = QPushButton(t("settings_cancel"))
        btn_row.addWidget(btn_open)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        def do_open():
            item = project_list.currentItem()
            if item:
                path = Path(item.data(Qt.ItemDataRole.UserRole))
                self._load_project_from_dir(path)
                dialog.accept()

        def do_delete():
            item = project_list.currentItem()
            if not item:
                return
            path = Path(item.data(Qt.ItemDataRole.UserRole))
            title = item.text().split("  (")[0]
            if QMessageBox.question(dialog, t("dialog_confirm_delete"), t("msg_delete_project", title)) == QMessageBox.StandardButton.Yes:
                project_io.delete_project(path)
                row = project_list.row(item)
                project_list.takeItem(row)

        btn_open.clicked.connect(do_open)
        btn_del.clicked.connect(do_delete)
        btn_cancel.clicked.connect(dialog.reject)
        project_list.itemDoubleClicked.connect(lambda: do_open())

        dialog.exec()

    def _load_project_from_dir(self, path: Path):
        """从项目目录加载项目。"""
        try:
            self.project = Project.load(path)
            self.agent_panel.set_project_db(path)
            self._load_workflow_for_project()
            self.sidebar.set_project_title(self.project.title)
            self.sidebar.load_chapters(self.project.chapters)
            self.sidebar.update_stats(
                self.project.total_words(),
                self.project.target_words,
                len(self.project.chapters),
            )
            self.editor.clear()
            self.editor.set_project_dir(self.project.project_dir)
            self.statusBar().showMessage(t("status_opened", self.project.title))
            logger.info("项目已加载: %s (%s)", self.project.title, path)
        except Exception as e:
            logger.error("加载项目失败: %s - %s", path, e, exc_info=True)
            QMessageBox.critical(self, t("dialog_error"), t("msg_open_fail", e))

    def _add_chapter(self):
        if not self.project.title:
            QMessageBox.information(self, t("dialog_prompt"), t("msg_no_project"))
            return
        title, ok = QInputDialog.getText(self, t("dialog_add_chapter"), t("dialog_chapter_title"))
        if ok:
            self.project.add_chapter(title)
            self.project.save()
            self.sidebar.load_chapters(self.project.chapters)
            self.sidebar.update_stats(
                self.project.total_words(),
                self.project.target_words,
                len(self.project.chapters),
            )

    def _on_chapter_selected(self, index: int):
        if 0 <= index < len(self.project.chapters):
            self.editor.load_chapter(self.project.chapters[index])

    def _on_content_changed(self):
        idx = self.editor._current_chapter_idx
        if 0 <= idx < len(self.project.chapters):
            self.project.chapters[idx].content = self.editor.get_content()
            self.sidebar.update_stats(
                self.project.total_words(),
                self.project.target_words,
                len(self.project.chapters),
            )

    def _on_agent_run(self, agent_name: str, user_input: str):
        agent = self._agents.get(agent_name)
        logger.debug("_on_agent_run: agent=%r, has_agent=%s, input=%r", agent_name, agent is not None, user_input[:80])
        if not agent:
            QMessageBox.warning(self, t("dialog_error"), t("msg_agent_not_init", agent_name))
            return
        self._stop_worker()
        context = self._build_context(agent_name)
        logger.debug("context_len=%d, context_preview=%r", len(context), context[:200])
        self.agent_panel.set_working(True, agent_name)
        self._worker = AgentWorker(agent, user_input, context)
        # C-07 修复：使用 partial 替代 lambda，避免内存泄漏
        self._worker.chunk_received.connect(partial(self._on_agent_stream, agent_name))
        self._worker.finished.connect(partial(self._on_agent_finished, agent_name))
        self._worker.error.connect(self._on_agent_error)
        self._worker.start()

    def _stop_worker(self):
        """安全停止后台工作线程，取消进行中的请求。"""
        if self._worker is not None:
            if self._worker.isRunning():
                self._worker.cancel()
                # C-08 修复：移除 terminate()，只用 wait() 等待安全退出
                self._worker.wait(5000)
            self._old_workers.append(self._worker)
            self._worker = None
        self._cleanup_workers()

    def _cleanup_workers(self):
        """清理已结束的旧 worker 引用。"""
        self._old_workers = [w for w in self._old_workers if w.isRunning()]

    def _build_context(self, agent_name: str) -> str:
        parts = []
        if self.project.title:
            meta = f"项目: {self.project.title}"
            if self.project.genre:
                meta += f" | 题材: {self.project.genre}"
            if self.project.style:
                meta += f" | 风格: {self.project.style}"
            if self.project.theme:
                meta += f" | 主题: {self.project.theme}"
            parts.append(meta)

        # 读取核心规划文档（只读大纲/人物/立意，不读全部）
        if self.project.project_dir:
            planning_dir = self.project.project_dir / "planning"
            if planning_dir.exists():
                core_files = ["大纲.md", "人物设定.md", "立意.md"]
                char_content = ""
                for fname in core_files:
                    f = planning_dir / fname
                    if f.exists():
                        content = project_io.read_md(f)
                        if content:
                            parts.append(f"=== {f.stem} ===\n{content}")
                            if fname == "人物设定.md":
                                char_content = content
                # 角色约束
                if char_content:
                    from ..core.workflow import _build_character_constraint
                    constraint = _build_character_constraint(char_content)
                    if constraint:
                        parts.append(constraint)

        # 只读最近3章内容（不读全部章节）
        if self.project.project_dir:
            chapters_dir = self.project.project_dir / "chapters"
            if chapters_dir.exists():
                recent_chapters = self.project.chapters[-3:] if len(self.project.chapters) > 3 else self.project.chapters
                for ch in recent_chapters:
                    if ch.content:
                        parts.append(f"=== 第{ch.number}章 {ch.title} ===\n{ch.content}")

        return "\n\n".join(parts)

    def _on_agent_finished(self, agent_name: str, response: str):
        try:
            self.agent_panel.set_working(False, agent_name)
            self.agent_panel.finalize_stream_message(agent_name)
            self.agent_panel.save_history()
            agents_cfg = load_agents()
            title = agents_cfg.get(agent_name, {}).get("title", agent_name)
            self.statusBar().showMessage(t("status_agent_done", title))
        except Exception as e:
            self.agent_panel.set_working(False)
            self.statusBar().showMessage(f"{t('dialog_error')}: {e}")

    def _on_agent_error(self, error: str):
        logger.error("Agent 调用出错: %s", error)
        self.agent_panel.set_working(False)
        self.statusBar().showMessage(f"{t('dialog_error')}: {error}")
        try:
            QMessageBox.critical(self, t("dialog_error"), error)
        except Exception:
            pass

    def _on_agent_stream(self, agent_name: str, chunk: str):
        """接收流式文本块，更新UI"""
        try:
            self.agent_panel.append_stream_chunk(chunk, agent_name)
        except Exception:
            pass

    def _open_agent_manage(self):
        """打开 Agent 管理对话框。"""
        dialog = AgentDialog(self.config, self)
        if dialog.exec() == AgentDialog.DialogCode.Accepted:
            self.config = dialog.get_config()
            self._save_config()
            self._init_agents()
            self.statusBar().showMessage(t("status_settings_saved"))

    def _open_model_settings(self):
        """打开模型设置对话框。"""
        dialog = ModelDialog(self.config, self)
        if dialog.exec() == ModelDialog.DialogCode.Accepted:
            self.config = dialog.get_config()
            self._save_config()
            self._init_llm()
            self._init_agents()
            self.statusBar().showMessage(t("status_settings_saved"))

    def _open_project_settings(self):
        """打开项目设置对话框。"""
        if not self.project.title:
            QMessageBox.information(self, t("dialog_prompt"), "请先打开或新建项目")
            return
        from .new_project_dialog import NewProjectDialog
        project_data = {
            "title": self.project.title,
            "genre": self.project.genre,
            "style": self.project.style,
            "target_words": self.project.target_words,
            "theme": self.project.theme,
            "direction": self.project.direction,
            "synopsis": self.project.synopsis,
            "cover": self.project.cover,
            "_project_dir": str(self.project.project_dir) if self.project.project_dir else "",
        }
        dialog = NewProjectDialog(llm=self.llm, parent=self, project_data=project_data)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            # 更新项目信息
            self.project.title = data["title"]
            self.project.genre = data["genre"]
            self.project.style = data["style"]
            self.project.target_words = data["target_words"]
            self.project.theme = data["theme"]
            self.project.direction = data["direction"]
            self.project.synopsis = data["synopsis"]
            # 更新封面
            if data["cover"] and self.project.project_dir:
                import shutil
                src = Path(data["cover"])
                ext = src.suffix or ".png"
                dst = self.project.project_dir / f"cover{ext}"
                shutil.copy2(src, dst)
                self.project.cover = f"cover{ext}"
            self._save_project()
            self.sidebar.set_project_title(self.project.title)
            self.statusBar().showMessage("项目设置已保存")

    def _open_appearance(self):
        """打开外观设置对话框。"""
        dialog = AppearanceDialog(self.config, self)
        dialog.theme_preview.connect(self._apply_theme)
        if dialog.exec() == AppearanceDialog.DialogCode.Accepted:
            self.config = dialog.get_config()
            self._save_config()
            self._apply_theme()
            self._refresh_ui_texts()
            self.statusBar().showMessage(t("status_settings_saved"))

    def _show_about(self):
        """显示关于对话框。"""
        QMessageBox.about(
            self,
            t("menu_about_app"),
            f"<h2 style='margin-bottom:8px;'>Novel Writer</h2>"
            f"<p>{t('about_version')}</p>"
            f"<p>{t('about_desc')}</p>"
            f"<p style='color:gray;font-size:12px;'>PySide6 · Python {'.'.join(map(str, __import__('sys').version_info[:3]))}</p>",
        )

    def _refresh_ui_texts(self):
        """切换语言后刷新所有 UI 文本。"""
        lang = self.config.get("language", "zh")
        set_language(lang)
        self.setWindowTitle(t("window_title"))
        # 菜单
        self._file_menu.setTitle(t("menu_file"))
        self._new_action.setText(t("menu_new_project"))
        self._open_action.setText(t("menu_open_project"))
        self._save_action.setText(t("menu_save_project"))
        self._exit_action.setText(t("menu_exit"))
        self._model_agent_menu.setTitle(t("menu_model_agent"))
        self._model_action.setText(t("menu_model_open"))
        self._manage_action.setText(t("menu_agent_manage"))
        self._about_menu.setTitle(t("menu_about"))
        self._appearance_action.setText(t("menu_appearance_open"))
        self._about_action.setText(t("menu_about_app"))
        self._workflow_menu.setTitle(t("workflow_menu"))
        self._workflow_open_action.setText(t("workflow_menu_open"))
        self._workflow_default_action.setText(t("workflow_menu_default"))
        # 侧边栏
        self.sidebar.retranslate()
        # 编辑区
        self.editor.retranslate()
        # Agent 面板
        self.agent_panel.retranslate()

    def _apply_theme(self, theme_name: str = None, preview_config: dict = None):
        theme_name = theme_name or self.config.get("theme", "dark")
        self.config["theme"] = theme_name
        cfg = preview_config or self.config
        self.setStyleSheet(build_style(theme_name, cfg))
        self.agent_panel.set_config(cfg)
        self.agent_panel.refresh_theme()

    def _save_project(self):
        if not self.project.title:
            return
        # 如果还没有项目目录（兼容旧流程），创建一个
        if not self.project.project_dir:
            safe_name = self.project.title.replace(" ", "_").replace("/", "_")
            project_dir = PROJECTS_DIR / safe_name
            project_dir.mkdir(parents=True, exist_ok=True)
            self.project.set_project_dir(project_dir)
        try:
            self.project.save()
            self._show_toast(t("status_saved", self.project.title))
        except Exception as e:
            self._show_toast(f"保存失败: {e}", error=True)
            logger.error("项目保存失败: %s", e)

    def _on_planning_save(self, doc_name: str):
        """保存规划文档。"""
        if not self.project.project_dir:
            return
        from ..ui.editor_panel import PLANNING_DOCS
        for label, rel_path in PLANNING_DOCS:
            if label == doc_name:
                content = self.editor.get_planning_content(doc_name)
                fpath = self.project.project_dir / rel_path
                try:
                    project_io.write_md(fpath, content)
                    self._show_toast(f"已保存: {doc_name}")
                    logger.info("规划文档已保存: %s", fpath)
                except Exception as e:
                    self._show_toast(f"保存失败: {e}", error=True)
                    logger.error("规划文档保存失败: %s - %s", fpath, e)
                return

    def _show_toast(self, text: str, duration: int = 2000, error: bool = False):
        """显示一个自动消失的提示框。"""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QLabel as _QLabel
        if error:
            style = "QLabel { background: #c0392b; color: white; padding: 8px 18px; border-radius: 6px; font-size: 13px; font-weight: 600; }"
        else:
            style = "QLabel { background: palette(highlight); color: palette(highlighted-text); padding: 8px 18px; border-radius: 6px; font-size: 13px; font-weight: 600; }"
        toast = _QLabel(text, self)
        toast.setStyleSheet(style)
        toast.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.ToolTip)
        toast.adjustSize()
        x = self.x() + (self.width() - toast.width()) // 2
        y = self.y() + self.height() - 100
        toast.move(x, y)
        toast.show()
        QTimer.singleShot(duration, toast.deleteLater)

    def _export_txt(self):
        """导出为 TXT 格式。"""
        if not self.project.project_dir:
            QMessageBox.warning(self, t("menu_export"), t("export_no_project"))
            return
        try:
            from ..core.exporter import Exporter
            exporter = Exporter(self.project.project_dir)
            output_path = exporter.export_txt()
            QMessageBox.information(self, t("export_success"), f"{output_path}")
        except Exception as e:
            QMessageBox.critical(self, t("export_failed"), str(e))
            logger.error("TXT 导出失败: %s", e)

    def _export_epub(self):
        """导出为 EPUB 格式。"""
        if not self.project.project_dir:
            QMessageBox.warning(self, t("menu_export"), t("export_no_project"))
            return
        try:
            from ..core.exporter import Exporter
            exporter = Exporter(self.project.project_dir)
            output_path = exporter.export_epub()
            QMessageBox.information(self, t("export_success"), f"{output_path}")
        except ImportError as e:
            QMessageBox.warning(self, t("export_missing_dep"), "pip install ebooklib")
        except Exception as e:
            QMessageBox.critical(self, t("export_failed"), str(e))
            logger.error("EPUB 导出失败: %s", e)

    def _export_pdf(self):
        """导出为 PDF 格式。"""
        if not self.project.project_dir:
            QMessageBox.warning(self, t("menu_export"), t("export_no_project"))
            return
        try:
            from ..core.exporter import Exporter
            exporter = Exporter(self.project.project_dir)
            output_path = exporter.export_pdf()
            QMessageBox.information(self, t("export_success"), f"{output_path}")
        except ImportError as e:
            QMessageBox.warning(self, t("export_missing_dep"), "pip install reportlab")
        except Exception as e:
            QMessageBox.critical(self, t("export_failed"), str(e))
            logger.error("PDF 导出失败: %s", e)

    # ── 工作流 ──

    def _setup_workflow(self):
        """初始化工作流：加载定义，连接信号。"""
        bar = self.agent_panel.workflow_bar
        bar.start_requested.connect(self._workflow_start)
        bar.stop_requested.connect(self._workflow_stop)
        self._wf_runner = None
        self._wf_thread = None
        self._wf_def = None
        self._wf_progress = {}

    def _load_workflow_for_project(self):
        """为当前项目加载工作流定义。"""
        if not self.project.project_dir:
            return
        saved_wf = project_io.load_workflow(self.project.project_dir)
        self._wf_progress = saved_wf.get("progress", {})
        wf_data = saved_wf.get("workflow")
        if wf_data:
            self._wf_def = WorkflowDef.from_dict(wf_data)
        else:
            wf_data = DEFAULT_WORKFLOW.copy()
            wf_data["project"] = {
                "title": self.project.title,
                "genre": self.project.genre,
                "style": self.project.style,
                "target_chapters": 20,
            }
            self._wf_def = WorkflowDef.from_dict(wf_data)
        self.agent_panel.workflow_bar.set_has_workflow(True)
        # 计算已完成百分比
        done = sum(1 for v in self._wf_progress.values() if v == "done")
        total = len(self._wf_def.steps) if self._wf_def else 1
        self.agent_panel.workflow_bar.set_progress(int(done / total * 100))

    def _show_mode_dialog(self) -> tuple[WorkflowMode, int, int] | None:
        """显示工作流模式选择对话框，返回 (mode, start_ch, end_ch) 或 None。"""
        dialog = QDialog(self)
        dialog.setWindowTitle(t("workflow_title"))
        dialog.setMinimumWidth(380)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # 模式选择
        mode_group = QGroupBox("工作流模式")
        mode_layout = QVBoxLayout(mode_group)
        mode_combo = QComboBox()
        mode_combo.addItem("📝 新书立意 — 只生成规划文档", WorkflowMode.NEW_BOOK_PLANNING)
        mode_combo.addItem("📖 新书全流程 — 从立意到审校", WorkflowMode.NEW_BOOK)
        mode_combo.addItem("✍️ 续写 — 从已有章节继续", WorkflowMode.CONTINUE)
        mode_combo.addItem("🔍 查漏补缺 — 检查并补写缺失章节", WorkflowMode.FILL_GAPS)
        mode_combo.addItem("✅ 校验 — 审核+校对已有章节", WorkflowMode.VALIDATE)
        mode_layout.addWidget(mode_combo)
        layout.addWidget(mode_group)

        # 章节范围
        chapter_group = QGroupBox("章节范围")
        chapter_layout = QHBoxLayout(chapter_group)

        chapter_layout.addWidget(QLabel("从第"))
        start_spin = QSpinBox()
        start_spin.setRange(1, 99999)
        start_spin.setValue(1)
        chapter_layout.addWidget(start_spin)
        chapter_layout.addWidget(QLabel("章"))

        chapter_layout.addWidget(QLabel("到第"))
        end_spin = QSpinBox()
        end_spin.setRange(1, 99999)
        end_spin.setValue(20)
        chapter_layout.addWidget(end_spin)
        chapter_layout.addWidget(QLabel("章"))

        layout.addWidget(chapter_group)

        # 续写模式：自动检测起始章节
        def on_mode_changed(idx):
            mode = mode_combo.currentData()
            if mode == WorkflowMode.CONTINUE:
                # 扫描已有章节数，自动设置起始章节（锁死不可改）
                chapters = project_io.scan_chapters(self.project.project_dir)
                if chapters:
                    last_num = max(c["number"] for c in chapters)
                    start_spin.setValue(last_num + 1)
                    end_spin.setValue(last_num + 20)
                start_spin.setEnabled(False)  # 锁死起始章节
                end_spin.setEnabled(True)
                chapter_group.setTitle("续写范围")
            elif mode == WorkflowMode.NEW_BOOK:
                start_spin.setValue(1)
                start_spin.setEnabled(False)
                end_spin.setEnabled(True)
                chapter_group.setTitle("目标章节数")
            elif mode == WorkflowMode.NEW_BOOK_PLANNING:
                chapter_group.setTitle("无需设置章节范围")
                start_spin.setEnabled(False)
                end_spin.setEnabled(False)
            elif mode == WorkflowMode.FILL_GAPS:
                start_spin.setEnabled(False)
                end_spin.setEnabled(False)
                chapter_group.setTitle("自动检测缺失章节")
            elif mode == WorkflowMode.VALIDATE:
                start_spin.setEnabled(False)
                end_spin.setEnabled(False)
                chapter_group.setTitle("校验全部章节")

        mode_combo.currentIndexChanged.connect(on_mode_changed)
        on_mode_changed(0)  # 初始化

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton(t("settings_cancel"))
        btn_cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_cancel)
        btn_ok = QPushButton(t("workflow_start"))
        btn_ok.setObjectName("primary")
        btn_ok.clicked.connect(dialog.accept)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        mode = mode_combo.currentData()
        return (mode, start_spin.value(), end_spin.value())

    def _workflow_start(self):
        """开始执行工作流 — 先弹出模式选择对话框。"""
        if not self.project.title:
            QMessageBox.information(self, t("dialog_prompt"), t("workflow_no_project"))
            return
        if not self.agents:
            QMessageBox.information(self, t("dialog_prompt"), t("workflow_no_agents"))
            return

        result = self._show_mode_dialog()
        if not result:
            return

        mode, start_ch, end_ch = result

        # 新书模式：检查是否已有章节内容
        if mode in (WorkflowMode.NEW_BOOK, WorkflowMode.NEW_BOOK_PLANNING) and self.project.project_dir:
            chapters = project_io.scan_chapters(self.project.project_dir)
            has_content = any(c["content_path"].stat().st_size > 0 for c in chapters)
            if has_content:
                QMessageBox.warning(
                    self, t("dialog_prompt"),
                    "该项目已有章节内容，不能执行新书工作流。\n请使用「续写」或「查漏补缺」模式。"
                )
                return

        # 构建工作流
        project_info = {
            "title": self.project.title,
            "genre": self.project.genre,
            "style": self.project.style,
            "theme": self.project.theme,
            "_project_dir": str(self.project.project_dir),
        }
        self._wf_def = build_workflow(mode, project_info, start_ch, end_ch)
        self._wf_progress = {}

        # 保存工作流定义
        wf_dict = self._wf_def.to_dict()
        project_io.save_workflow(self.project.project_dir, {
            "workflow": wf_dict,
            "progress": {},
        })

        self._wf_runner = WorkflowRunner(
            agents=self.agents,
            project_dir=self.project.project_dir,
            project_info=self._wf_def.project,
        )

        # 创建后台线程执行
        from .workflow_panel import WorkflowThread
        self._wf_thread = WorkflowThread(
            self._wf_runner, self._wf_def, self._wf_progress
        )

        # 连接线程信号（WorkflowThread.run() 会覆盖 runner 回调，所以连信号）
        self._wf_thread.step_started.connect(self._on_wf_step_started)
        self._wf_thread.step_finished.connect(self._on_wf_step_finished)
        self._wf_thread.step_error.connect(self._on_wf_step_error)
        self._wf_thread.progress_updated.connect(self._on_wf_progress)
        self._wf_thread.log_message.connect(self._on_wf_log)
        self._wf_thread.workflow_done.connect(self._on_wf_done)
        self._wf_thread.workflow_stopped.connect(self._on_wf_stopped)

        self.agent_panel.on_workflow_started()
        self._wf_thread.start()

    def _on_wf_step_started(self, step_id: str, n: int, agent_title: str):
        self.agent_panel.on_workflow_step_started(step_id, n, agent_title)
        self.agent_panel.workflow_bar.set_current_step(step_id, agent_title, n, 0)
        # 在聊天区显示工作流调用
        self.agent_panel.add_user_message(f"[工作流] {step_id}" + (f" 第{n}章" if n > 1 else ""))

    def _on_wf_step_finished(self, step_id: str, n: int, agent_title: str, response: str):
        self.agent_panel.on_workflow_step_finished(step_id, n, agent_title)
        # 将 Agent 响应添加到聊天记录
        agent_name = self.agent_panel._find_agent_by_title(agent_title)
        if agent_name and response:
            self.agent_panel.add_agent_message(agent_name, response[:2000])
        # 章节步骤完成后刷新侧边栏
        if step_id == "chapter":
            self._refresh_chapters()
        # 规划步骤完成后刷新规划文档编辑器
        PLANNING_STEPS = {"ideation", "outline", "characters", "world", "timeline", "main_plot", "sub_plot", "foreshadow"}
        if step_id in PLANNING_STEPS:
            self.editor.refresh_planning()

    def _on_wf_step_error(self, step_id: str, error: str):
        self.agent_panel.on_workflow_step_error(step_id, error)

    def _on_wf_log(self, msg: str):
        self.agent_panel.workflow_bar.append_log(msg)
        self.statusBar().showMessage(msg, 5000)

    def _workflow_stop(self):
        if self._wf_thread and self._wf_thread.isRunning():
            self._wf_thread.request_stop()

    def _on_wf_progress(self, progress: dict):
        self._wf_progress = progress
        done = sum(1 for v in progress.values() if v == "done")
        total = len(self._wf_def.steps) if self._wf_def else 1
        self.agent_panel.on_workflow_progress(int(done / total * 100))

    def _on_wf_done(self):
        self._wf_thread = None
        self.agent_panel.on_workflow_finished()
        self.agent_panel.workflow_bar.set_progress(100)
        self._refresh_chapters()
        self.editor.refresh_planning()
        self.statusBar().showMessage(t("workflow_finished"))

    def _on_wf_stopped(self):
        self._wf_thread = None
        self.agent_panel.workflow_bar.set_running(False)
        self._refresh_chapters()
        self.statusBar().showMessage(t("workflow_stopped"))

    def _refresh_chapters(self):
        """从磁盘重新加载章节列表到侧边栏。"""
        if not self.project.project_dir:
            return
        # 重新扫描章节文件
        scanned = project_io.scan_chapters(self.project.project_dir)
        # 更新 Project 对象的 chapters 列表
        existing = {ch.number: ch for ch in self.project.chapters}
        for item in scanned:
            if item["number"] not in existing:
                from ..models.chapter import Chapter
                ch = Chapter(
                    number=item["number"],
                    title=item["title"],
                    _content_path=str(item["content_path"]),
                    _outline_path=str(item["outline_path"]) if item["outline_path"] else "",
                )
                self.project.chapters.append(ch)
        self.project.chapters.sort(key=lambda c: c.number)
        # 刷新侧边栏
        self.sidebar.load_chapters(self.project.chapters)
        self.sidebar.update_stats(
            self.project.total_words(),
            self.project.target_words,
            len(self.project.chapters),
        )

    def _open_workflow_panel(self):
        """打开工作流面板（保留兼容，实际通过 workflow_bar 操作）。"""
        if not self.project.title:
            QMessageBox.information(self, t("dialog_prompt"), t("workflow_no_project"))
            return
        self._workflow_start()

    def _load_default_workflow(self):
        """加载默认工作流到当前项目。"""
        if not self.project.title:
            QMessageBox.information(self, t("dialog_prompt"), t("workflow_no_project"))
            return
        wf_data = DEFAULT_WORKFLOW.copy()
        wf_data["project"] = {
            "title": self.project.title,
            "genre": self.project.genre,
            "style": self.project.style,
            "target_chapters": 20,
        }
        project_io.save_workflow(self.project.project_dir, {
            "workflow": wf_data,
            "progress": {},
        })
        self._load_workflow_for_project()
        self.statusBar().showMessage(t("workflow_generated"))

    def closeEvent(self, event):
        self._stop_worker()
        # M-10 修复：使用单个事件循环关闭所有连接，避免阻塞
        all_llms = [agent.llm for agent in self.agents.values()]
        if self.llm:
            all_llms.append(self.llm)

        async def close_all_llms():
            for llm in all_llms:
                try:
                    if hasattr(llm, 'client') and hasattr(llm.client, 'close'):
                        await llm.client.close()
                except Exception:
                    pass

        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(close_all_llms())
            loop.close()
        except Exception:
            pass

        # 保存聊天记录和项目
        self.agent_panel.save_history()
        if self.project.title:
            self._save_project()
        event.accept()
