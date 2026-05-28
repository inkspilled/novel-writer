from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QScrollArea, QFrame,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QKeyEvent

from .agent_animation import AgentIndicator
from ..locales import t
from .styles import get_theme_colors

# 默认颜色池
COLOR_POOL = ["#ff6b8a", "#51cf66", "#4da6ff", "#ffd43b", "#cc5de8", "#ff922b",
              "#20c997", "#748ffc", "#f06595", "#5c7cfa", "#63e6be", "#e599f7"]

AGENT_PANEL_GLOBALS: dict = {"agent_emojis": {}, "agent_colors": {}, "config": {}}


def get_color(name: str, idx: int = 0) -> str:
    return AGENT_PANEL_GLOBALS["agent_colors"].get(name, COLOR_POOL[idx % len(COLOR_POOL)])


# ── Markdown → HTML 轻量渲染器 ──

def _is_light_color(hex_color: str) -> bool:
    """判断颜色是否为浅色（用于 Markdown 渲染时动态选择代码块背景色）。"""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = int(h[:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return False
    return (r * 299 + g * 587 + b * 114) / 1000 > 128


def _inline_md(text: str, fg: str = "#e8e8ed") -> str:
    """行内 Markdown：粗体、斜体、行内代码。"""
    code_bg = "rgba(0,0,0,0.06)" if _is_light_color(fg) else "rgba(255,255,255,0.08)"
    text = re.sub(r'`([^`]+)`',
                  r'<code style="background:' + code_bg + r';padding:1px 5px;'
                  r'border-radius:3px;font-family:Consolas,monospace;font-size:12px;">\1</code>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    return text


def _md_to_html(text: str, fg: str = "#e8e8ed") -> str:
    """轻量 Markdown → HTML，支持代码块、标题、列表、表格。"""
    is_light = _is_light_color(fg)
    code_bg = "rgba(0,0,0,0.06)" if is_light else "rgba(0,0,0,0.25)"
    code_fg = "#6e6e73" if is_light else "#a6adc8"
    border_color = "rgba(0,0,0,0.1)" if is_light else "rgba(255,255,255,0.1)"

    lines = text.split("\n")
    out: list[str] = []
    in_code_block = False
    in_list = False
    in_table = False
    table_rows: list[str] = []

    for line in lines:
        # 代码块
        if line.strip().startswith("```"):
            if in_code_block:
                out.append("</pre>")
                in_code_block = False
            else:
                if in_list:
                    out.append("</ul>")
                    in_list = False
                out.append(
                    f'<pre style="background:{code_bg};color:{code_fg};padding:10px 14px;'
                    'border-radius:8px;font-family:Consolas,monospace;font-size:12px;'
                    'overflow-x:auto;line-height:1.5;">')
                in_code_block = True
            continue
        if in_code_block:
            out.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            continue

        stripped = line.strip()

        # 表格
        if "|" in stripped and stripped.startswith("|"):
            if re.match(r'^\|[\s\-:|]+\|$', stripped):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                in_table = True
                table_rows = []
                header = "".join(
                    f'<th style="padding:6px 12px;border:1px solid {border_color};'
                    f'text-align:left;font-weight:600;">{c}</th>' for c in cells)
                table_rows.append(f"<tr>{header}</tr>")
            else:
                row = "".join(
                    f'<td style="padding:4px 12px;border:1px solid {border_color};">{c}</td>'
                    for c in cells)
                table_rows.append(f"<tr>{row}</tr>")
            continue
        elif in_table:
            out.append(
                f'<table style="border-collapse:collapse;margin:8px 0;width:100%;">'
                f'{"".join(table_rows)}</table>')
            in_table = False
            table_rows = []

        # 标题
        if stripped.startswith("### "):
            out.append(f'<b style="font-size:13px;">{stripped[4:]}</b><br>')
        elif stripped.startswith("## "):
            out.append(f'<b style="font-size:14px;">{stripped[3:]}</b><br>')
        elif stripped.startswith("# "):
            out.append(f'<b style="font-size:15px;">{stripped[2:]}</b><br>')
        # 无序列表
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                in_list = True
                out.append("<ul>")
            out.append(f"<li>{_inline_md(stripped[2:], fg)}</li>")
        # 有序列表
        elif re.match(r'^\d+\.\s', stripped):
            if not in_list:
                in_list = True
                out.append('<ul style="list-style-type:decimal;">')
            text = re.sub(r"^\d+\.\s", "", stripped)
            out.append(f'<li>{_inline_md(text, fg)}</li>')
        # 空行
        elif not stripped:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append("<br>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(_inline_md(stripped, fg))

    if in_list:
        out.append("</ul>")
    if in_table:
        out.append(
            f'<table style="border-collapse:collapse;margin:8px 0;width:100%;">'
            f'{"".join(table_rows)}</table>')
    if in_code_block:
        out.append("</pre>")

    return "<br>".join(out)


# ── 消息气泡 ──

class ChatMessage(QFrame):
    """单条消息气泡，支持 Markdown 渲染。"""

    def __init__(self, role: str, content: str, agent_name: str = "", parent=None):
        super().__init__(parent)
        self._role = role
        self._raw_content = content
        self._agent_name = agent_name
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._outer = QHBoxLayout(self)
        self._outer.setContentsMargins(8, 4, 8, 4)

        self._bubble = QFrame()
        self._bubble_layout = QVBoxLayout(self._bubble)
        self._bubble_layout.setContentsMargins(14, 10, 14, 10)
        self._bubble_layout.setSpacing(4)

        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._bubble_layout.addWidget(self._label)

        self._avatar = None
        if role == "user":
            self._outer.addStretch()
            self._outer.addWidget(self._bubble, 0)
        else:
            self._outer.addWidget(self._bubble, 1)

        self._apply_style()
        self._set_content(content)

    def _apply_style(self):
        colors = get_theme_colors(
            AGENT_PANEL_GLOBALS.get("config", {}).get("theme", "dark"),
            AGENT_PANEL_GLOBALS.get("config")
        )
        accent = colors.get("accent", "#6e8efb")
        card = colors.get("card", "#1c1c26")
        fg = colors.get("fg", "#e8e8ed")

        if self._role == "user":
            self._bubble.setStyleSheet(
                f"QFrame {{ background-color: {accent}; border-radius: 14px; }}")
            self._label.setStyleSheet(
                f"QLabel {{ color: #ffffff; background: transparent; border: none; "
                f"font-size: 13px; }}")
            self._fg = "#ffffff"
        else:
            self._bubble.setStyleSheet(
                f"QFrame {{ background-color: {card}; border-radius: 14px; "
                f"border: 1px solid {colors.get('border', 'rgba(255,255,255,0.06)')}; }}")
            self._label.setStyleSheet(
                f"QLabel {{ color: {fg}; background: transparent; border: none; "
                f"font-size: 13px; }}")
            self._fg = fg

    def _set_content(self, content: str) -> None:
        if not content:
            return
        if self._role == "user":
            safe = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            self._label.setText(safe.replace("\n", "<br>"))
        else:
            html = _md_to_html(content, fg=self._fg)
            self._label.setTextFormat(Qt.TextFormat.RichText)
            self._label.setText(html)

    def refresh_style(self):
        """刷新主题样式。"""
        self._apply_style()
        self._set_content(self._raw_content)


# ── Ctrl+Enter 发送的输入框 ──

class ChatTextEdit(QTextEdit):
    """支持 Ctrl+Enter 发送的文本输入框。"""
    submit = Signal()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.submit.emit()
                return
        super().keyPressEvent(event)


# ── Agent 面板主体 ──

class AgentPanel(QWidget):

    agent_run_requested = Signal(str, str)

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
        self._db_path: Path | None = None  # 项目级数据库路径
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 办公室场景（占 45%） ──
        from .office_scene import OfficeScene
        self.office = OfficeScene()
        self.office.agent_clicked.connect(self._on_office_agent_clicked)
        layout.addWidget(self.office, 3)

        # ── 工作流迷你进度条 ──
        from .workflow_bar import WorkflowMiniBar
        self.workflow_bar = WorkflowMiniBar()
        self.workflow_bar.setMaximumHeight(50)
        layout.addWidget(self.workflow_bar)

        # ── Agent 信息行 ──
        header = QWidget()
        header.setObjectName("agentHeader")
        header.setMaximumHeight(36)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 4, 12, 4)
        header_layout.setSpacing(8)

        self.indicator = AgentIndicator()
        header_layout.addWidget(self.indicator)
        self.agent_name_label = QLabel(t("agent_select"))
        self.agent_name_label.setWordWrap(True)
        self.agent_name_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        header_layout.addWidget(self.agent_name_label)
        header_layout.addStretch()

        self.btn_clear = QPushButton("\U0001f5d1️")
        self.btn_clear.setFixedSize(26, 26)
        self.btn_clear.setToolTip(t("agent_clear_chat"))
        self.btn_clear.setStyleSheet("""
            QPushButton {
                border-radius: 6px; font-size: 14px;
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

        # ── 快捷操作栏 ──
        self._quick_bar = QWidget()
        self._quick_bar_layout = QHBoxLayout(self._quick_bar)
        self._quick_bar_layout.setContentsMargins(12, 4, 12, 4)
        self._quick_bar_layout.setSpacing(6)
        self._quick_bar_layout.addStretch()
        self._quick_bar.setVisible(False)
        layout.addWidget(self._quick_bar)

        # ── 聊天区域（占 55%） ──
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(8, 8, 8, 8)
        self.chat_layout.setSpacing(4)
        self.chat_layout.addStretch()
        self.scroll_area.setWidget(self.chat_container)
        layout.addWidget(self.scroll_area, 4)

        # ── 加载指示器 ──
        self._loading_label = QLabel(t("agent_thinking"))
        self._loading_label.setVisible(False)
        self._loading_label.setStyleSheet("color: gray; padding: 4px 16px; font-size: 12px;")
        layout.addWidget(self._loading_label)

        # ── 输入区 ──
        input_area = QWidget()
        input_area.setObjectName("agentInput")
        input_area.setMaximumHeight(70)
        input_layout = QVBoxLayout(input_area)
        input_layout.setContentsMargins(12, 4, 12, 6)
        input_layout.setSpacing(4)

        self.input_edit = ChatTextEdit()
        self.input_edit.setPlaceholderText(t("agent_ph_input"))
        self.input_edit.setMaximumHeight(90)
        self.input_edit.submit.connect(self._on_send)
        input_layout.addWidget(self.input_edit)

        btn_row = QHBoxLayout()
        self._hint_label = QLabel(t("agent_send_hint"))
        self._hint_label.setStyleSheet("font-size: 11px; color: gray;")
        btn_row.addWidget(self._hint_label)
        btn_row.addStretch()
        self.btn_run = QPushButton(t("agent_send"))
        self.btn_run.setObjectName("primary")
        self.btn_run.setFixedHeight(34)
        self.btn_run.setFixedWidth(80)
        self.btn_run.clicked.connect(self._on_send)
        btn_row.addWidget(self.btn_run)
        input_layout.addLayout(btn_row)

        layout.addWidget(input_area)

    # ── Agent 按钮管理 ──

    def update_agent_buttons(self, agents_cfg: dict):
        self.agent_buttons.clear()
        self._agents_cfg = agents_cfg

        emojis, colors = {}, {}
        for i, (name, info) in enumerate(agents_cfg.items()):
            emoji = info.get("emoji", "🤖")
            title = info.get("title", name)
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
            AGENT_PANEL_GLOBALS.get("config", {}).get("theme", "dark"),
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
        import sys
        text = self.input_edit.toPlainText().strip()
        if not text or not self._current_agent:
            print(f"[DEBUG] _on_send blocked: text={repr(text[:50] if text else '')}, agent={self._current_agent!r}", file=sys.stderr)
            return
        self.add_user_message(text)
        self.input_edit.clear()
        print(f"[DEBUG] _on_send emitting: agent={self._current_agent!r}, text={repr(text[:80])}", file=sys.stderr)
        self.agent_run_requested.emit(self._current_agent, text)

    def _send_quick_action(self, prompt: str):
        """发送快捷操作。"""
        self.input_edit.setText(prompt)
        self._on_send()

    # ── 消息管理 ──

    def add_user_message(self, text: str):
        msg = ChatMessage("user", text)
        self._msg_widgets.append(msg)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg)
        QTimer.singleShot(50, self._scroll_to_bottom)

        if self._current_agent not in self._chat_history:
            self._chat_history[self._current_agent] = []
        self._chat_history[self._current_agent].append({"type": "user", "text": text})

    def add_agent_message(self, agent_name: str, text: str):
        msg = ChatMessage("agent", text, agent_name=agent_name)
        self._msg_widgets.append(msg)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg)
        QTimer.singleShot(50, self._scroll_to_bottom)

        if agent_name not in self._chat_history:
            self._chat_history[agent_name] = []
        self._chat_history[agent_name].append(
            {"type": "agent", "text": text, "agent_name": agent_name})

    def append_stream_chunk(self, chunk: str, agent_name: str = ""):
        """追加流式文本块。"""
        if self._stream_widget is None:
            self._stream_agent = agent_name or self._current_agent
            self._stream_widget = ChatMessage("agent", "", agent_name=self._stream_agent)
            # 只在当前显示的是同一智能体时才插入 widget
            if self._stream_agent == self._current_agent:
                self._msg_widgets.append(self._stream_widget)
                self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._stream_widget)
            self._stream_text = ""

        self._stream_text += chunk
        display_text = self._stream_text

        # 处理 think 标签
        if "<think>" in display_text and "</think>" not in display_text:
            think_content = display_text.split("<think>")[1]
            display_text = f"💭 *思考中...*\n\n{think_content}"
        elif "<think>" in display_text and "</think>" in display_text:
            parts = display_text.split("</think>")
            think_content = parts[0].split("<think>")[1]
            answer = parts[1] if len(parts) > 1 else ""
            display_text = f"💭 *思考过程:*\n{think_content}\n\n{answer}"

        # 保护：widget 可能已被 deleteLater 销毁（切换 Agent 或清空聊天时）
        try:
            self._stream_widget._set_content(display_text)
        except RuntimeError:
            self._stream_widget = None
            self._stream_text = ""
            return
        QTimer.singleShot(20, self._scroll_to_bottom)

    def finalize_stream_message(self, agent_name: str):
        """完成流式消息。"""
        target = self._stream_agent or agent_name
        # 如果流式 widget 被保存在 pending 中，取出来
        pending = self._pending_streams.pop(target, None)
        if pending and not self._stream_widget:
            self._stream_widget, self._stream_text, self._stream_agent = pending

        if self._stream_widget and self._stream_text:
            # 重新渲染完整内容（处理 think 标签）
            final_text = self._stream_text
            if "<think>" in final_text and "</think>" in final_text:
                parts = final_text.split("</think>")
                think_content = parts[0].split("<think>")[1]
                answer = parts[1].strip() if len(parts) > 1 else ""
                final_text = f"💭 *思考过程:*\n{think_content}\n\n{answer}" if think_content else answer
            self._stream_widget._raw_content = final_text
            self._stream_widget._set_content(final_text)

            if target not in self._chat_history:
                self._chat_history[target] = []
            self._chat_history[target].append(
                {"type": "agent", "text": self._stream_text, "agent_name": target})

        self._stream_widget = None
        self._stream_text = ""
        self._stream_agent = ""

    # ── 滚动 ──

    def _scroll_to_bottom(self):
        sb = self.scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── 聊天记录管理 ──

    def _save_current_chat(self):
        pass

    def _restore_chat(self, agent_name: str):
        # 保存当前流式状态（如果有正在进行的流）
        if self._stream_widget and self._stream_agent:
            self._pending_streams[self._stream_agent] = (
                self._stream_widget, self._stream_text, self._stream_agent)
            self._stream_widget = None
            self._stream_text = ""
            self._stream_agent = ""

        for msg in self._msg_widgets:
            # 如果是正在进行的流式 widget，跳过销毁（由 pending_streams 管理）
            if msg is self._stream_widget:
                continue
            msg.deleteLater()
        self._msg_widgets.clear()

        history = self._chat_history.get(agent_name, [])
        for m in history:
            if m["type"] == "user":
                msg = ChatMessage("user", m["text"])
            else:
                msg = ChatMessage("agent", m["text"],
                                  agent_name=m.get("agent_name", agent_name))
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
        self.save_history()

    def clear_chat(self):
        self._stream_widget = None
        self._stream_text = ""
        for msg in self._msg_widgets:
            msg.deleteLater()
        self._msg_widgets.clear()
        self._chat_history.clear()
        self.save_history()

    # ── 项目级数据库管理 ──

    def set_project_db(self, project_dir: Path | None):
        """切换项目数据库路径。切换前自动保存当前项目的聊天记录。"""
        if self._db_path and self._chat_history:
            self.save_history()
        if project_dir:
            self._db_path = project_dir / "chat.db"
        else:
            self._db_path = None
        self._chat_history.clear()
        self._stream_widget = None
        self._stream_text = ""
        self._stream_agent = ""
        self._pending_streams.clear()
        for msg in self._msg_widgets:
            msg.deleteLater()
        self._msg_widgets.clear()
        if self._db_path:
            self.load_history()

    # ── 聊天记录持久化 (SQLite) ──

    def _get_db(self) -> sqlite3.Connection:
        if not self._db_path:
            raise RuntimeError("未设置项目数据库路径")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self._db_path))
        db.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        db.commit()
        return db

    def save_history(self):
        """将当前所有聊天记录写入 SQLite（全量覆盖当前项目）。"""
        if not self._db_path:
            return
        db = self._get_db()
        try:
            db.execute("DELETE FROM messages")
            rows = []
            for agent_name, messages in self._chat_history.items():
                for m in messages:
                    rows.append((agent_name, m["type"], m["text"]))
            db.executemany("INSERT INTO messages (agent, role, content) VALUES (?, ?, ?)", rows)
            db.commit()
        finally:
            db.close()

    def load_history(self):
        """从 SQLite 加载当前项目的聊天记录。"""
        if not self._db_path or not self._db_path.exists():
            return
        db = self._get_db()
        try:
            self._chat_history = {}
            for agent, role, content in db.execute(
                    "SELECT agent, role, content FROM messages ORDER BY id"):
                if agent not in self._chat_history:
                    self._chat_history[agent] = []
                self._chat_history[agent].append(
                    {"type": role, "text": content, "agent_name": agent})
        finally:
            db.close()

    # ── 工作状态 ──

    def set_working(self, active: bool, agent_name: str = ""):
        if active:
            color = get_color(agent_name)
            self.indicator.start(color)
            self.btn_run.setEnabled(False)
            self.btn_run.setText(t("agent_generating"))
            self._loading_label.setVisible(True)
            self.input_edit.setEnabled(False)
        else:
            self.indicator.stop()
            self.btn_run.setEnabled(True)
            self.btn_run.setText(t("agent_send"))
            self._loading_label.setVisible(False)
            self.input_edit.setEnabled(True)

    # ── 主题刷新 ──

    def refresh_theme(self):
        """刷新所有组件的主题样式。"""
        colors = get_theme_colors(
            AGENT_PANEL_GLOBALS.get("config", {}).get("theme", "dark"),
            AGENT_PANEL_GLOBALS.get("config")
        )
        fg3 = colors.get("fg3", "#5a5a66")
        border = colors.get("border", "rgba(255,255,255,0.06)")

        self._loading_label.setStyleSheet(
            f"color: {fg3}; padding: 4px 16px; font-size: 12px;")
        self._hint_label.setStyleSheet(
            f"font-size: 11px; color: {fg3};")
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
        for slot in self.office._slots:
            if slot.state == AgentState.WORKING:
                self.office.set_agent_state(slot.name, AgentState.ERROR)
                self.workflow_bar.append_log(f"✗ <b>{slot.title}</b> 出错: {error[:80]}")
                break

    def on_workflow_progress(self, percent: int, step_text: str = ""):
        self.workflow_bar.set_progress(percent, step_text)

    def on_workflow_started(self):
        from .office_scene import AgentState
        for slot in self.office._slots:
            if slot.state != AgentState.WORKING:
                self.office.set_agent_state(slot.name, AgentState.WAITING)
        self.workflow_bar.set_running(True)
        self.workflow_bar.append_log("── 工作流开始 ──")

    def on_workflow_finished(self):
        from .office_scene import AgentState
        for slot in self.office._slots:
            self.office.set_agent_state(slot.name, AgentState.DONE)
        self.workflow_bar.set_running(False)
        self.workflow_bar.append_log("── <b>工作流完成！</b> ──")

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
