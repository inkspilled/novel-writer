from __future__ import annotations

import asyncio
import json
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QSplitter, QInputDialog,
    QMessageBox, QDialog, QVBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QHBoxLayout, QLabel,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QKeySequence, QIcon, QPixmap

from .sidebar import Sidebar
from .editor_panel import EditorPanel
from .agent_panel import AgentPanel
from .settings_dialog import AppearanceDialog, ModelDialog, AgentDialog
from .styles import build_style
from ..locales import t, set_language
from ..models.project import Project
from ..models.chapter import Chapter, ChapterStatus
from ..models.character import Character
from ..core.llm import OpenAICompatLLM, ClaudeLLM, OllamaLLM
from ..core.agents import load_default_agents
from ..core.agents.base import BaseAgent, AgentConfig


CONFIG_PATH = Path.home() / ".novel-writer" / "config.json"
PROJECTS_DIR = Path.home() / ".novel-writer" / "projects"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
PROJECT_ROOT = ASSETS_DIR.parent.parent


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

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            full_response = ""
            async def collect_stream():
                nonlocal full_response
                async for chunk in self.agent.stream_run(self.user_input, self.context):
                    full_response += chunk
                    self.chunk_received.emit(chunk)
                return full_response

            loop.run_until_complete(collect_stream())
            self.finished.emit(full_response)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            loop.close()


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
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

        self._setup_ui()
        self._apply_theme()
        self._setup_menu()
        self._setup_menubar_icon()
        self._init_llm()
        self._init_agents()
        self.agent_panel.load_history()

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
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([240, 700, 380])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage(t("status_ready"))

        self.sidebar.chapter_selected.connect(self._on_chapter_selected)
        self.sidebar.new_project_requested.connect(self._new_project)
        self.sidebar.open_project_requested.connect(self._open_project)
        self.sidebar.chapter_add_requested.connect(self._add_chapter)
        self.agent_panel.agent_run_requested.connect(self._on_agent_run)
        self.editor.content_changed.connect(self._on_content_changed)

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
        self._model_agent_menu.addSeparator()
        self._rebuild_agent_menu(self._model_agent_menu)

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

    def _rebuild_agent_menu(self, menu: QMenu = None):
        if menu is None:
            for action in self.menuBar().actions():
                if action.menu() == self._model_agent_menu:
                    menu = action.menu()
                    break
        if not menu:
            return
        # 清除旧的 agent 快捷项（保留 "Agent 管理" 和分隔线）
        to_remove = []
        found_sep = False
        for action in menu.actions():
            if action.isSeparator():
                found_sep = True
                continue
            if found_sep and not action.isSeparator():
                to_remove.append(action)
        for action in to_remove:
            menu.removeAction(action)
        # 重新添加
        agents_cfg = self.config.get("agents") or load_default_agents()
        for name, info in agents_cfg.items():
            emoji = info.get("emoji", "🤖")
            title = info.get("title", name)
            action = QAction(f"{emoji} {title}", self)
            action.triggered.connect(lambda checked, n=name: self._quick_run_agent(n))
            menu.addAction(action)

    def _load_config(self) -> dict:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {}

    def _save_config(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")

    def _create_llm(self, provider: dict):
        """根据供应商配置创建 LLM 实例。"""
        ptype = provider.get("type", "")
        if ptype == "openai_compat":
            return OpenAICompatLLM(
                model=provider["model"],
                api_key=provider.get("api_key", ""),
                base_url=provider["base_url"],
            )
        elif ptype == "claude":
            return ClaudeLLM(
                model=provider["model"],
                api_key=provider.get("api_key", ""),
            )
        elif ptype == "ollama":
            return OllamaLLM(
                model=provider["model"],
                base_url=provider.get("base_url", "http://localhost:11434"),
            )
        return None

    def _init_llm(self):
        provider = self.config.get("current_provider")
        if not provider:
            return
        self.llm = self._create_llm(provider)

    def _init_agents(self):
        self.agents.clear()
        if not self.llm:
            return

        # 从 config 读取 agents 配置，没有则用内置默认
        agents_cfg = self.config.get("agents", {})
        if not agents_cfg:
            # 从 JSON 加载默认 agents 配置
            default_agents = load_default_agents()
            for name, defaults in default_agents.items():
                agents_cfg[name] = {
                    "title": defaults.get("title", name),
                    "emoji": defaults.get("emoji", "🤖"),
                    "skills": defaults.get("skills", []),
                    "temperature": defaults.get("temperature", 0.7),
                    "max_tokens": defaults.get("max_tokens", 4096),
                    "system_prompt": defaults.get("system_prompt", ""),
                    "model": "",
                }
            self.config["agents"] = agents_cfg

        saved_models = self.config.get("saved_models", {})

        for name, info in agents_cfg.items():
            # 每个 Agent 可以有自己的模型（引用 saved_models 的 key）
            agent_model_key = info.get("model", "").strip()
            if agent_model_key:
                saved = saved_models.get(agent_model_key)
                if saved:
                    # 从已保存模型配置创建独立 LLM
                    llm = self._create_llm(saved) or self.llm
                else:
                    # 兼容旧逻辑：当作 model 名称覆盖全局配置
                    provider = self.config.get("current_provider", {}).copy()
                    provider["model"] = agent_model_key
                    llm = self._create_llm(provider) or self.llm
            else:
                llm = self.llm

            prompt = info.get("system_prompt", "")
            agent_config = AgentConfig(
                name=name,
                role=info.get("title", name),
                title=info.get("title", name),
                system_prompt=prompt,
                skills=info.get("skills", []),
                model=agent_model_key,
                temperature=info.get("temperature", 0.7),
                max_tokens=info.get("max_tokens", 4096),
            )
            self.agents[name] = BaseAgent(agent_config, llm)

        # 更新 Agent 面板的按钮
        self.agent_panel.update_agent_buttons(agents_cfg)

    def _new_project(self):
        # 先保存当前项目
        if self.project.title:
            self._save_project()
        title, ok = QInputDialog.getText(self, t("dialog_new_project"), t("dialog_novel_title"))
        if ok and title:
            self.project = Project(title=title)
            self.project.add_chapter(t("chapter_first"))
            self._save_project()
            self.sidebar.set_project_title(title)
            self.sidebar.load_chapters(self.project.chapters)
            self.sidebar.update_stats(0, self.project.target_words, len(self.project.chapters))
            self.editor.clear()
            self.statusBar().showMessage(t("status_created", title))

    def _open_project(self):
        """打开已有项目。"""
        # 先保存当前项目
        if self.project.title:
            self._save_project()
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        project_files = sorted(PROJECTS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not project_files:
            QMessageBox.information(self, t("dialog_prompt"), t("msg_no_projects_saved"))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(t("dialog_open_project"))
        dialog.setMinimumSize(400, 360)
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel(t("dialog_select_project")))
        project_list = QListWidget()
        for f in project_files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                title = data.get("title", f.stem)
                ch_count = len(data.get("chapters", []))
                words = sum(len(ch.get("content", "")) for ch in data.get("chapters", []))
                item = QListWidgetItem(f"{title}  ({ch_count} {t('sidebar_chapters')} · {words:,} {t('editor_words')})")
                item.setData(Qt.ItemDataRole.UserRole, str(f))
                project_list.addItem(item)
            except Exception:
                continue
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
                self._load_project_from_file(path)
                dialog.accept()

        def do_delete():
            item = project_list.currentItem()
            if not item:
                return
            path = Path(item.data(Qt.ItemDataRole.UserRole))
            title = item.text().split("  (")[0]
            if QMessageBox.question(dialog, t("dialog_confirm_delete"), t("msg_delete_project", title)) == QMessageBox.StandardButton.Yes:
                path.unlink(missing_ok=True)
                row = project_list.row(item)
                project_list.takeItem(row)

        btn_open.clicked.connect(do_open)
        btn_del.clicked.connect(do_delete)
        btn_cancel.clicked.connect(dialog.reject)
        project_list.itemDoubleClicked.connect(lambda: do_open())

        dialog.exec()

    def _load_project_from_file(self, path: Path):
        """从 JSON 文件加载项目。"""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.project = Project(
                id=data.get("id", ""),
                title=data.get("title", ""),
                genre=data.get("genre", ""),
                style=data.get("style", ""),
                theme=data.get("theme", ""),
                target_words=data.get("target_words", 200000),
                synopsis=data.get("synopsis", ""),
                world_setting=data.get("world_setting", ""),
            )
            for ch_data in data.get("chapters", []):
                status_str = ch_data.get("status", "outlined")
                try:
                    status = ChapterStatus(status_str)
                except ValueError:
                    status = ChapterStatus.OUTLINED
                ch = Chapter(
                    id=ch_data.get("id", ""),
                    number=ch_data.get("number", 0),
                    title=ch_data.get("title", ""),
                    outline=ch_data.get("outline", ""),
                    content=ch_data.get("content", ""),
                    notes=ch_data.get("notes", ""),
                    status=status,
                )
                self.project.chapters.append(ch)
            for char_data in data.get("characters", []):
                char = Character(
                    id=char_data.get("id", ""),
                    name=char_data.get("name", ""),
                    personality=char_data.get("personality", ""),
                    background=char_data.get("background", ""),
                )
                self.project.characters.append(char)
            self.sidebar.set_project_title(self.project.title)
            self.sidebar.load_chapters(self.project.chapters)
            self.sidebar.update_stats(
                self.project.total_words(),
                self.project.target_words,
                len(self.project.chapters),
            )
            self.editor.clear()
            self.statusBar().showMessage(t("status_opened", self.project.title))
        except Exception as e:
            QMessageBox.critical(self, t("dialog_error"), t("msg_open_fail", e))

    def _add_chapter(self):
        if not self.project.title:
            QMessageBox.information(self, t("dialog_prompt"), t("msg_no_project"))
            return
        title, ok = QInputDialog.getText(self, t("dialog_add_chapter"), t("dialog_chapter_title"))
        if ok:
            self.project.add_chapter(title)
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
            self.project.chapters[idx].outline = self.editor.get_outline()
            self.project.chapters[idx].notes = self.editor.get_notes()
            self.sidebar.update_stats(
                self.project.total_words(),
                self.project.target_words,
                len(self.project.chapters),
            )

    def _on_agent_run(self, agent_name: str, user_input: str):
        agent = self.agents.get(agent_name)
        if not agent:
            QMessageBox.warning(self, t("dialog_error"), t("msg_agent_not_init", agent_name))
            return
        self._stop_worker()
        context = self._build_context(agent_name)
        self.agent_panel.set_working(True, agent_name)
        self._worker = AgentWorker(agent, user_input, context)
        self._worker.chunk_received.connect(self._on_agent_stream)
        self._worker.finished.connect(lambda resp: self._on_agent_finished(agent_name, resp))
        self._worker.error.connect(self._on_agent_error)
        self._worker.start()

    def _stop_worker(self):
        """安全停止后台工作线程，避免 QThread 销毁时线程仍在运行。"""
        if self._worker is not None and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(3000)  # 最多等待 3 秒
            self._worker = None

    def _build_context(self, agent_name: str) -> str:
        parts = []
        if self.project.title:
            parts.append(f"项目: {self.project.title}")
            parts.append(f"题材: {self.project.genre}")
            parts.append(f"风格: {self.project.style}")
            parts.append(f"主题: {self.project.theme}")
            parts.append(f"简介: {self.project.synopsis}")
        if self.project.characters:
            chars = "\n".join(f"- {c.name}: {c.personality}" for c in self.project.characters)
            parts.append(f"人物设定:\n{chars}")
        if self.project.world_setting:
            parts.append(f"世界观:\n{self.project.world_setting}")
        idx = self.editor._current_chapter_idx
        if 0 <= idx < len(self.project.chapters):
            ch = self.project.chapters[idx]
            parts.append(f"当前章节: 第{ch.number}章 {ch.title}")
            if ch.outline:
                parts.append(f"章节大纲:\n{ch.outline}")
            if ch.content:
                parts.append(f"已有正文:\n{ch.content[:2000]}")
        return "\n\n".join(parts)

    def _on_agent_finished(self, agent_name: str, response: str):
        self.agent_panel.set_working(False, agent_name)
        self.agent_panel.finalize_stream_message(agent_name)
        self.agent_panel.save_history()
        agents_cfg = self.config.get("agents") or load_default_agents()
        title = agents_cfg.get(agent_name, {}).get("title", agent_name)
        self.statusBar().showMessage(t("status_agent_done", title))

    def _on_agent_error(self, error: str):
        self.agent_panel.set_working(False)
        QMessageBox.critical(self, t("dialog_error"), error)
        self.statusBar().showMessage(f"{t('dialog_error')}: {error}")

    def _on_agent_stream(self, chunk: str):
        """接收流式文本块，更新UI"""
        self.agent_panel.append_stream_chunk(chunk)

    def _quick_run_agent(self, agent_name: str):
        self.agent_panel._on_agent_selected(agent_name)

    def _open_agent_manage(self):
        """打开 Agent 管理对话框。"""
        dialog = AgentDialog(self.config, self)
        if dialog.exec() == AgentDialog.DialogCode.Accepted:
            self.config = dialog.get_config()
            self._save_config()
            self._init_agents()
            self._rebuild_agent_menu()
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
        self._rebuild_agent_menu()
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
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        path = PROJECTS_DIR / f"{self.project.id}.json"
        data = {
            "id": self.project.id,
            "title": self.project.title,
            "genre": self.project.genre,
            "style": self.project.style,
            "theme": self.project.theme,
            "target_words": self.project.target_words,
            "synopsis": self.project.synopsis,
            "world_setting": self.project.world_setting,
            "chapters": [
                {
                    "id": ch.id, "number": ch.number, "title": ch.title,
                    "outline": ch.outline, "content": ch.content, "notes": ch.notes,
                    "status": ch.status.value if hasattr(ch.status, 'value') else ch.status,
                    "review_comments": ch.review_comments, "proofread_notes": ch.proofread_notes,
                }
                for ch in self.project.chapters
            ],
            "characters": [
                {
                    "id": c.id, "name": c.name, "aliases": c.aliases,
                    "gender": c.gender, "age": c.age, "personality": c.personality,
                    "background": c.background, "appearance": c.appearance,
                    "relationships": c.relationships, "arc": c.arc, "notes": c.notes,
                }
                for c in self.project.characters
            ],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.statusBar().showMessage(t("status_saved", str(path)))

    def closeEvent(self, event):
        self._stop_worker()
        if self.project.title:
            self._save_project()
        event.accept()
