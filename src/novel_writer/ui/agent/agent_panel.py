from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea,
)
from PySide6.QtCore import Qt, Signal, QTimer

from .agent_animation import AgentIndicator
from ..common.chat_rendering import COLOR_POOL, AGENT_PANEL_GLOBALS, get_color
from ..common.chat_widgets import ChatMessage, ChatTextEdit
from ...core.chat_history import ChatHistory
from ...locales import t
from ...core.logger import get_logger
from ..common.styles import get_theme_colors

logger = get_logger(__name__)


def _render_stream_parts(parts: list[tuple[str, str]]) -> str:
    """按 (kind, text) 渲染聊天内容：同 kind 合并，思考整段一个 💭。"""
    merged: list[list[str]] = []
    for kind, text in parts:
        if not text:
            continue
        if merged and merged[-1][0] == kind:
            merged[-1][1] += text
        else:
            merged.append([kind, text])
    out: list[str] = []
    for kind, text in merged:
        if kind == "thinking":
            out.append(f"💭 {text.strip()}")
        else:
            out.append(text)
    return "\n\n".join(out)


class AgentPanel(QWidget):

    agent_run_requested = Signal(str, str)
    # 请求把 AI 输出写进章节：(章节号, 正文)
    chapter_apply_requested = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.agent_buttons: dict[str, QPushButton] = {}
        self._current_agent = ""
        self._agents_cfg: dict = {}
        self._chat_history: dict[str, list] = {}
        self._msg_widgets: list[ChatMessage] = []
        self._stream_widget: ChatMessage | None = None
        self._stream_text: str = ""
        self._stream_agent: str = ""
        self._pending_streams: dict[str, tuple] = {}
        self._quick_buttons: list[QPushButton] = []
        self._chat_db: ChatHistory | None = None  # 项目级聊天记录存储
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Agent 状态条（紧凑卡片，不抢聊天区） ──
        from .office_scene import OfficeScene
        self.office = OfficeScene()
        self.office.setMinimumHeight(OfficeScene.HEIGHT)
        self.office.agent_clicked.connect(self._on_office_agent_clicked)
        layout.addWidget(self.office)

        # ── 工作流迷你进度条（含日志） ──
        from ..workflow.workflow_bar import WorkflowMiniBar
        self.workflow_bar = WorkflowMiniBar()
        self.workflow_bar.setMaximumHeight(160)
        layout.addWidget(self.workflow_bar, 0)

        # ── Agent 信息行 ──
        header = QWidget()
        header.setObjectName("agentHeader")
        header.setMaximumHeight(32)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 2, 12, 2)
        header_layout.setSpacing(6)

        self.indicator = AgentIndicator()
        header_layout.addWidget(self.indicator)
        self.agent_name_label = QLabel(t("agent_select"))
        self.agent_name_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        header_layout.addWidget(self.agent_name_label)
        header_layout.addStretch()

        self.btn_clear = QPushButton("\U0001f5d1️")
        self.btn_clear.setFixedSize(24, 24)
        self.btn_clear.setToolTip(t("agent_clear_chat"))
        self.btn_clear.setStyleSheet("""
            QPushButton {
                border-radius: 6px; font-size: 13px;
                border: 1px solid rgba(255,255,255,0.1);
                padding: 0;
            }
            QPushButton:hover {
                background-color: rgba(255,100,100,0.2);
                border-color: rgba(255,100,100,0.3);
            }
        """)
        self.btn_clear.clicked.connect(self._clear_current_chat)
        header_layout.addWidget(self.btn_clear)
        layout.addWidget(header)

        # ── 未打开项目提示 ──
        self._no_project_hint = QLabel("💡 请先打开或新建项目")
        self._no_project_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_project_hint.setStyleSheet("color: gray; font-size: 12px; padding: 8px;")
        self._no_project_hint.setVisible(True)
        layout.addWidget(self._no_project_hint)

        # ── 快捷操作栏 ──
        self._quick_bar = QWidget()
        self._quick_bar_layout = QHBoxLayout(self._quick_bar)
        self._quick_bar_layout.setContentsMargins(12, 4, 12, 4)
        self._quick_bar_layout.setSpacing(6)
        self._quick_bar_layout.addStretch()
        self._quick_bar.setVisible(False)
        layout.addWidget(self._quick_bar)

        # ── 聊天区域（主区域，弹性占满） ──
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(12, 10, 12, 10)
        self.chat_layout.setSpacing(8)
        self._empty_state = QWidget()
        empty_layout = QVBoxLayout(self._empty_state)
        empty_layout.setContentsMargins(28, 40, 28, 24)
        empty_layout.setSpacing(8)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._empty_title = QLabel(t("agent_empty_title"))
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        empty_layout.addWidget(self._empty_title)

        self._empty_hint_label = QLabel(t("agent_empty_hint"))
        self._empty_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint_label.setWordWrap(True)
        self._empty_hint_label.setStyleSheet("color: #8e8e9a; font-size: 12px;")
        empty_layout.addWidget(self._empty_hint_label)

        empty_layout.addSpacing(6)
        self._empty_actions = []
        for agent_key, text_key, prompt in (
            ("planner", "agent_quick_outline",
             "请根据现有立意补全故事大纲，保持分卷篇幅，不要压缩剧情。"),
            ("writer", "agent_quick_continue",
             "请续写下一章正文，先给出章节标题，再写正文。"),
            ("reviewer", "agent_quick_review",
             "请检查最近 3 章的剧情逻辑、人物一致性和节奏，列出问题与修改建议。"),
        ):
            btn = QPushButton(t(text_key))
            btn.setMinimumHeight(34)
            btn.clicked.connect(
                lambda checked=False, a=agent_key, p=prompt: self._on_empty_action(a, p)
            )
            empty_layout.addWidget(btn)
            self._empty_actions.append(btn)

        self.chat_layout.addWidget(self._empty_state)
        self.chat_layout.addStretch()
        self.scroll_area.setWidget(self.chat_container)
        layout.addWidget(self.scroll_area, 1)

        # ── 加载指示器 ──
        self._loading_label = QLabel(t("agent_thinking"))
        self._loading_label.setVisible(False)
        self._loading_label.setStyleSheet("color: gray; padding: 4px 16px; font-size: 12px;")
        layout.addWidget(self._loading_label)

        # ── 输入区（固定底栏，禁止被撑大） ──
        input_area = QWidget()
        input_area.setObjectName("agentInput")
        input_area.setFixedHeight(132)  # 输入框+按钮行，写死不参与布局膨胀
        input_layout = QVBoxLayout(input_area)
        input_layout.setContentsMargins(12, 10, 12, 12)
        input_layout.setSpacing(8)

        self.input_edit = ChatTextEdit()
        self.input_edit.setPlaceholderText(t("agent_ph_input"))
        self.input_edit.setFixedHeight(68)  # QTextEdit 默认会纵向膨胀，必须锁死
        self.input_edit.setEnabled(False)
        self.input_edit.submit.connect(self._on_send)
        input_layout.addWidget(self.input_edit)

        btn_row = QHBoxLayout()
        self._hint_label = QLabel(t("agent_send_hint"))
        self._hint_label.setStyleSheet("font-size: 11px; color: gray;")
        btn_row.addWidget(self._hint_label)
        btn_row.addStretch()
        self.btn_run = QPushButton(t("agent_send"))
        self.btn_run.setObjectName("primary")
        self.btn_run.setFixedHeight(36)
        self.btn_run.setFixedWidth(88)
        self.btn_run.setEnabled(False)
        self.btn_run.clicked.connect(self._on_send)
        btn_row.addWidget(self.btn_run)
        input_layout.addLayout(btn_row)

        layout.addWidget(input_area)

        # 初始状态：禁用交互
        self._project_loaded = False
        self._chat_locked = False   # 工作流执行中禁止自由对话
        self._working_active = False  # 单次对话生成中

    # ── Agent 按钮管理 ──

    def update_agent_buttons(self, agents_cfg: dict):
        self.agent_buttons.clear()
        self._agents_cfg = agents_cfg

        emojis, colors = {}, {}
        for i, (name, info) in enumerate(agents_cfg.items()):
            emoji = info.get("emoji", "🤖")
            color = COLOR_POOL[i % len(COLOR_POOL)]
            emojis[name] = emoji
            colors[name] = color

        AGENT_PANEL_GLOBALS["agent_emojis"] = emojis
        AGENT_PANEL_GLOBALS["agent_colors"] = colors

        # 初始化办公室场景
        self.office.setup_agents(agents_cfg, colors)

    def set_config(self, config: dict):
        """传入应用配置，用于主题感知。"""
        AGENT_PANEL_GLOBALS["config"] = config

    # ── Agent 切换 ──

    def _on_office_agent_clicked(self, name: str):
        """办公室场景中点击了某个 Agent。"""
        self._on_agent_selected(name)

    def _on_agent_selected(self, name: str):
        if self._current_agent and self._current_agent != name:
            self._save_current_chat()

        self._current_agent = name

        # 更新办公室场景选中态
        self.office.set_selected(name)

        emoji = AGENT_PANEL_GLOBALS["agent_emojis"].get(name, "🤖")
        info = self._agents_cfg.get(name, {})
        title = info.get("title", name)

        # 智能体名称 + 模型信息（同一行）
        agent_model = info.get("model", "").strip()
        if agent_model:
            model_html = f'  <span style="font-size:11px;font-weight:400;color:#4da6ff;">🤖 {agent_model}</span>'
        else:
            model_html = f'  <span style="font-size:11px;font-weight:400;color:gray;">🤖 {t("agent_model_default")}</span>'
        self.agent_name_label.setTextFormat(Qt.TextFormat.RichText)
        self.agent_name_label.setText(f"{emoji}  {title}{model_html}")

        self._restore_chat(name)
        self._update_quick_actions(name)

    def _update_quick_actions(self, agent_name: str):
        """根据 Agent 的 skills 生成快捷按钮。"""
        for btn in self._quick_buttons:
            btn.deleteLater()
        self._quick_buttons.clear()

        info = self._agents_cfg.get(agent_name, {})
        skills = info.get("skills", [])
        if not skills:
            self._quick_bar.setVisible(False)
            return

        self._quick_bar.setVisible(True)
        prompt_map = {
            "选题分析": "帮我分析一下当前热门的小说题材趋势",
            "立意规划": "帮我确定这部小说的核心立意",
            "风格定位": "帮我确定写作风格",
            "故事结构": "帮我设计故事结构",
            "章节规划": "帮我规划章节结构",
            "人物设定": "帮我设计主要人物",
            "世界观构建": "帮我构建世界观设定",
            "伏笔设计": "帮我设计伏笔和悬念",
            "正文写作": "请根据大纲写正文",
            "对话创作": "帮我写一段人物对话",
            "场景描写": "帮我描写这个场景",
            "错别字检查": "帮我检查错别字",
            "剧情审查": "帮我审查剧情逻辑",
            "文笔润色": "帮我润色这段文字",
            "情感渲染": "帮我加强情感表达",
        }
        for skill in skills[:4]:
            prompt = prompt_map.get(skill, f"帮我{skill}")
            btn = QPushButton(skill)
            btn.setProperty("quick_action", True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, p=prompt: self._send_quick_action(p))
            self._quick_bar_layout.insertWidget(self._quick_bar_layout.count() - 1, btn)
            self._quick_buttons.append(btn)

        self._apply_quick_style()

    def _apply_quick_style(self):
        """应用快捷按钮主题样式。"""
        colors = get_theme_colors(
            AGENT_PANEL_GLOBALS.get("config", {}).get("theme", "light"),
            AGENT_PANEL_GLOBALS.get("config")
        )
        card = colors.get("card", "#1c1c26")
        border = colors.get("border", "rgba(255,255,255,0.06)")
        text = colors.get("fg", "#e8e8ed")
        accent = colors.get("accent", "#6e8efb")
        for btn in self._quick_buttons:
            btn.setStyleSheet(
                f"QPushButton {{ background: {card}; border: 1px solid {border}; "
                f"border-radius: 12px; padding: 4px 12px; color: {text}; font-size: 12px; }}"
                f"QPushButton:hover {{ background: {accent}; color: #ffffff; border-color: {accent}; }}"
            )

    # ── 发送 ──

    def _on_send(self):
        if not self._project_loaded:
            return
        text = self.input_edit.toPlainText().strip()
        if not text or not self._current_agent:
            logger.debug("_on_send blocked: text=%r, agent=%r", text[:50] if text else '', self._current_agent)
            return
        self.add_user_message(text)
        self.input_edit.clear()
        logger.debug("_on_send emitting: agent=%r, text=%r", self._current_agent, text[:80])
        self.agent_run_requested.emit(self._current_agent, text)

    def _send_quick_action(self, prompt: str):
        """发送快捷操作。"""
        self.input_edit.setText(prompt)
        self._on_send()

    # ── 消息管理 ──

    def _update_empty_hint(self):
        has_messages = bool(self._msg_widgets)
        visible = self._project_loaded and not has_messages and not self._chat_locked
        self._empty_state.setVisible(visible)
        for btn in self._empty_actions:
            btn.setEnabled(self._project_loaded and not self._chat_locked)

    def _on_empty_action(self, agent_key: str, prompt: str):
        """空状态快捷动作：切到对应智能体并发送预置指令。"""
        if not self._project_loaded or self._chat_locked:
            return
        if agent_key not in self._agents_cfg:
            agent_key = next(iter(self._agents_cfg), "")
        if not agent_key:
            return
        self._on_agent_selected(agent_key)
        self._send_quick_action(prompt)

    def add_user_message(self, text: str):
        msg = ChatMessage("user", text)
        self._msg_widgets.append(msg)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg)
        self._update_empty_hint()
        QTimer.singleShot(50, self._scroll_to_bottom)

        if self._current_agent not in self._chat_history:
            self._chat_history[self._current_agent] = []
        self._chat_history[self._current_agent].append({"type": "user", "text": text})

    def add_agent_message(self, agent_name: str, text: str):
        msg = ChatMessage("agent", text)
        self._msg_widgets.append(msg)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg)
        self._update_empty_hint()
        QTimer.singleShot(50, self._scroll_to_bottom)

        if agent_name not in self._chat_history:
            self._chat_history[agent_name] = []
        self._chat_history[agent_name].append(
            {"type": "agent", "text": text, "agent_name": agent_name})

    def append_stream_chunk(self, chunk: str, agent_name: str = "", kind: str = "text"):
        """追加流式文本块。kind=thinking 时灰字显示，text 常规显示。"""
        if self._stream_widget is None:
            self._stream_agent = agent_name or self._current_agent
            self._stream_widget = ChatMessage("agent", "")
            # 只在当前显示的是同一智能体时才插入 widget
            if self._stream_agent == self._current_agent:
                self._msg_widgets.append(self._stream_widget)
                self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._stream_widget)
            self._stream_text = ""
            self._stream_parts = []  # (kind, text)

        if not hasattr(self, "_stream_parts"):
            self._stream_parts = []
        self._stream_parts.append((kind, chunk))
        # 历史只存正文
        if kind == "text":
            self._stream_text += chunk
        display_text = _render_stream_parts(self._stream_parts)

        # widget 可能已被 deleteLater 销毁（切换 Agent 或清空聊天时）
        try:
            self._stream_widget._set_content(display_text)
        except RuntimeError as e:
            logger.debug("Stream widget destroyed during update: %s", e)
            self._stream_widget = None
            self._stream_text = ""
            self._stream_parts = []
            return
        QTimer.singleShot(20, self._scroll_to_bottom)

    def finalize_stream_message(self, agent_name: str):
        """完成流式消息。"""
        target = self._stream_agent or agent_name
        # 如果流式 widget 被保存在 pending 中，取出来
        pending = self._pending_streams.pop(target, None)
        if pending and not self._stream_widget:
            self._stream_widget, self._stream_text, self._stream_agent = pending

        parts = getattr(self, "_stream_parts", None) or []
        if self._stream_widget and (self._stream_text or parts):
            # 按 kind 渲染：思考灰字、正文常规（无标签）
            final_text = _render_stream_parts(parts) if parts else self._stream_text
            try:
                self._stream_widget._raw_content = final_text
                self._stream_widget._set_content(final_text)
            except RuntimeError as e:
                logger.debug("Stream widget destroyed at finalize: %s", e)

            if target not in self._chat_history:
                self._chat_history[target] = []
            # 历史只存正文
            self._chat_history[target].append(
                {"type": "agent", "text": self._stream_text, "agent_name": target})

            # 动作条：外部（chat_mixin）判定章节号后调用 show_chapter_actions
            self._last_agent_message = self._stream_widget

        self._stream_widget = None
        self._stream_text = ""
        self._stream_parts = []
        self._stream_agent = ""

    def show_apply_actions(self, chapter_n: int, content: str):
        """在最近一条 AI 消息下显示「替换第 N 章 / 复制」。"""
        msg = getattr(self, "_last_agent_message", None)
        if msg is None:
            return
        msg.show_chapter_actions(chapter_n, content)
        try:
            msg.apply_to_chapter.connect(
                lambda text, n=chapter_n: self.chapter_apply_requested.emit(n, text)
            )
            msg.copy_requested.connect(
                lambda text: self._copy_to_clipboard(text)
            )
        except Exception:
            pass

    @staticmethod
    def _copy_to_clipboard(text: str):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)

    def _scroll_to_bottom(self):
        sb = self.scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── 聊天记录管理 ──

    def _save_current_chat(self):
        pass

    def _restore_chat(self, agent_name: str):
        # 保存当前流式状态（如果有正在进行的流）
        preserved_ids: set[int] = set()
        if self._stream_widget and self._stream_agent:
            self._pending_streams[self._stream_agent] = (
                self._stream_widget, self._stream_text, self._stream_agent)
            preserved_ids.add(id(self._stream_widget))
            self._stream_widget = None
            self._stream_text = ""
            self._stream_agent = ""

        # 其他 Agent 的 pending 流式控件也绝不能销毁
        for w, _text, _agent in self._pending_streams.values():
            preserved_ids.add(id(w))

        for msg in self._msg_widgets:
            if id(msg) in preserved_ids:
                continue
            msg.deleteLater()
        self._msg_widgets.clear()

        history = self._chat_history.get(agent_name, [])
        for m in history:
            if m["type"] == "user":
                msg = ChatMessage("user", m["text"])
            else:
                msg = ChatMessage("agent", m["text"])
            self._msg_widgets.append(msg)
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg)

        # 恢复该智能体的进行中流式输出
        pending = self._pending_streams.pop(agent_name, None)
        if pending:
            widget, text, s_agent = pending
            self._stream_widget = widget
            self._stream_text = text
            self._stream_agent = s_agent
            self._msg_widgets.append(widget)
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, widget)

        self._update_empty_hint()
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _clear_current_chat(self):
        if not self._current_agent:
            return
        self._stream_widget = None
        self._stream_text = ""
        self._stream_agent = ""
        self._pending_streams.pop(self._current_agent, None)
        self._chat_history.pop(self._current_agent, None)
        for msg in self._msg_widgets:
            msg.deleteLater()
        self._msg_widgets.clear()
        self._update_empty_hint()
        self.save_history()

    # ── 项目级聊天记录 ──

    def set_project_db(self, project_dir: Path | None):
        """切换项目数据库路径。切换前自动保存当前项目的聊天记录。"""
        if self._chat_db and self._chat_history:
            self.save_history()
        if project_dir:
            self._chat_db = ChatHistory(project_dir / "chat.db")
            self._project_loaded = True
            self._update_empty_hint()
            self._no_project_hint.setVisible(False)
            self.input_edit.setEnabled(True)
            self.btn_run.setEnabled(True)
        else:
            self._chat_db = None
            self._project_loaded = False
            self._update_empty_hint()
            self._no_project_hint.setVisible(True)
            self.input_edit.setEnabled(False)
            self.btn_run.setEnabled(False)
        self._chat_history.clear()
        self._stream_widget = None
        self._stream_text = ""
        self._stream_agent = ""
        self._pending_streams.clear()
        for msg in self._msg_widgets:
            msg.deleteLater()
        self._msg_widgets.clear()
        if self._chat_db:
            self.load_history()

    def save_history(self):
        """将当前所有聊天记录写入 SQLite（全量覆盖当前项目）。"""
        if not self._chat_db:
            return
        self._chat_db.save(self._chat_history)

    def load_history(self):
        """从 SQLite 加载当前项目的聊天记录。"""
        if not self._chat_db:
            return
        self._chat_history = self._chat_db.load()

    # ── 工作状态 ──

    def set_working(self, active: bool, agent_name: str = ""):
        self._working_active = active
        if active:
            color = get_color(agent_name)
            self.indicator.start(color)
            self.btn_run.setEnabled(False)
            self.btn_run.setText(t("agent_generating"))
            self._loading_label.setVisible(True)
            self.input_edit.setEnabled(False)
        else:
            self.indicator.stop()
            self._loading_label.setVisible(False)
            # 工作流锁定期间不解锁聊天
            if not self._chat_locked:
                self.btn_run.setEnabled(self._project_loaded)
                self.btn_run.setText(t("agent_send"))
                self.input_edit.setEnabled(self._project_loaded)

    # ── 主题刷新 ──

    def refresh_theme(self):
        """刷新所有组件的主题样式。"""
        colors = get_theme_colors(
            AGENT_PANEL_GLOBALS.get("config", {}).get("theme", "light"),
            AGENT_PANEL_GLOBALS.get("config")
        )
        fg3 = colors.get("fg3", "#5a5a66")
        border = colors.get("border", "rgba(255,255,255,0.06)")

        self._loading_label.setStyleSheet(
            f"color: {fg3}; padding: 4px 16px; font-size: 12px;")
        self._hint_label.setStyleSheet(
            f"font-size: 11px; color: {fg3};")
        self._empty_hint_label.setStyleSheet(
            f"color: {colors.get('fg2', fg3)}; font-size: 12px;")
        self.btn_clear.setStyleSheet(f"""
            QPushButton {{
                border-radius: 6px; font-size: 14px;
                border: 1px solid {border};
                padding: 0;
            }}
            QPushButton:hover {{
                background-color: rgba(255,100,100,0.2);
                border-color: rgba(255,100,100,0.3);
            }}
        """)

        self.indicator.update_theme(colors)
        self.office.apply_theme(colors)
        self.workflow_bar.apply_theme(colors)

        self._apply_quick_style()
        for msg in self._msg_widgets:
            msg.refresh_style()

    # ── 工作流联动 ──

    def on_workflow_step_started(self, step_id: str, n: int, agent_title: str):
        """工作流步骤开始。"""
        agent_name = self._find_agent_by_title(agent_title)
        if agent_name:
            from .office_scene import AgentState
            desc = f"{step_id} #{n}" if n > 1 else step_id
            self.office.set_agent_state(agent_name, AgentState.WORKING, task_desc=desc)
            self._on_agent_selected(agent_name)
            self.workflow_bar.set_current_step(step_id, agent_title, n, 0)
            self.workflow_bar.append_log(f"▸ <b>{agent_title}</b> 开始 {step_id}" + (f" (第{n}章)" if n > 1 else ""))

    def on_workflow_step_finished(self, step_id: str, n: int, agent_title: str):
        """工作流步骤完成。"""
        agent_name = self._find_agent_by_title(agent_title)
        if agent_name:
            from .office_scene import AgentState
            self.office.set_agent_state(agent_name, AgentState.DONE)
            self.workflow_bar.append_log(f"✓ <b>{agent_title}</b> 完成 {step_id}" + (f" (第{n}章)" if n > 1 else ""))

    def on_workflow_step_error(self, step_id: str, error: str):
        """工作流步骤出错。"""
        from .office_scene import AgentState
        for slot in self.office.iter_slots():
            if slot.state == AgentState.WORKING:
                self.office.set_agent_state(slot.name, AgentState.ERROR)
                self.workflow_bar.append_log(f"✗ <b>{slot.title}</b> 出错: {error[:80]}")
                break

    def on_workflow_progress(self, percent: int, step_text: str = ""):
        self.workflow_bar.set_progress(percent, step_text)

    def on_workflow_started(self):
        from .office_scene import AgentState
        for slot in self.office.iter_slots():
            if slot.state != AgentState.WORKING:
                self.office.set_agent_state(slot.name, AgentState.WAITING)
        self.workflow_bar.set_running(True)
        self.workflow_bar.append_log("── 工作流开始 ──")
        # 工作流占用 Agent/LLM：禁止自由对话，避免和流程步骤抢模型、打乱历史
        self._set_chat_locked(True)

    def on_workflow_finished(self):
        from .office_scene import AgentState
        self.office.reset_states(AgentState.DONE)
        self.workflow_bar.set_running(False)
        self.workflow_bar.append_log("── <b>工作流完成！</b> ──")
        self._set_chat_locked(False)

    def _set_chat_locked(self, locked: bool):
        """工作流执行期间锁定/解锁自由对话。"""
        self._chat_locked = locked
        if locked:
            self.input_edit.setEnabled(False)
            self.btn_run.setEnabled(False)
            self.input_edit.setPlaceholderText("工作流执行中，暂不可自由对话（可点停止后继续）")
            self.btn_run.setText(t("agent_generating"))
        else:
            enabled = self._project_loaded and not self._working_active
            self.input_edit.setEnabled(enabled)
            self.btn_run.setEnabled(enabled)
            self.input_edit.setPlaceholderText(t("agent_ph_input"))
            self.btn_run.setText(t("agent_send"))
        self._update_empty_hint()

    def _find_agent_by_title(self, title: str) -> str:
        """根据 agent 中文标题找到配置名。"""
        for name, info in self._agents_cfg.items():
            if info.get("title", name) == title:
                return name
        return ""

    # ── 国际化 ──

    def retranslate(self):
        self.agent_name_label.setText(t("agent_select"))
        self.input_edit.setPlaceholderText(t("agent_ph_input"))
        self.btn_run.setText(t("agent_send"))
        self._loading_label.setText(t("agent_thinking"))
        self._hint_label.setText(t("agent_send_hint"))
        self._empty_title.setText(t("agent_empty_title"))
        self._empty_hint_label.setText(t("agent_empty_hint"))
        for btn, key in zip(self._empty_actions, (
            "agent_quick_outline", "agent_quick_continue", "agent_quick_review",
        )):
            btn.setText(t(key))
