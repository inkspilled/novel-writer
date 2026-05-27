from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QScrollArea, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from .agent_animation import AgentIndicator, AgentBubble
from ..locales import t

# 默认颜色池
COLOR_POOL = ["#ff6b8a", "#51cf66", "#4da6ff", "#ffd43b", "#cc5de8", "#ff922b",
              "#20c997", "#748ffc", "#f06595", "#5c7cfa", "#63e6be", "#e599f7"]

AGENT_PANEL_GLOBALS: dict = {"agent_emojis": {}, "agent_colors": {}}


def get_color(name: str, idx: int = 0) -> str:
    return AGENT_PANEL_GLOBALS["agent_colors"].get(name, COLOR_POOL[idx % len(COLOR_POOL)])


class ChatBubble(QFrame):

    def __init__(self, text: str, is_agent: bool = True, agent_name: str = "", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)

        emoji = AGENT_PANEL_GLOBALS.get("agent_emojis", {}).get(agent_name, "🤖")

        if is_agent:
            avatar = AgentBubble(emoji)
            layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)
            self.content_label = QLabel(text)
            self.content_label.setWordWrap(True)
            self.content_label.setTextFormat(Qt.TextFormat.MarkdownText)
            self.content_label.setObjectName("chatBubble")
            self.content_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            layout.addWidget(self.content_label)
        else:
            self.content_label = QLabel(text)
            self.content_label.setWordWrap(True)
            self.content_label.setTextFormat(Qt.TextFormat.MarkdownText)
            self.content_label.setObjectName("chatBubbleUser")
            self.content_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            layout.addWidget(self.content_label)
            avatar = AgentBubble("👤")
            layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)


class AgentPanel(QWidget):

    agent_run_requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setMaximumWidth(560)
        self.agent_buttons: dict[str, QPushButton] = {}
        self._current_agent = ""
        self._chat_history: dict[str, list] = {}  # 每个Agent独立的聊天记录
        self._stream_label: QLabel = None  # 当前流式显示的标签
        self._stream_text: str = ""  # 当前流式文本
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("agentHeader")
        self.header_layout = QVBoxLayout(header)
        self.header_layout.setContentsMargins(16, 16, 16, 12)
        self.header_layout.setSpacing(10)

        self._title_label = QLabel(t("agent_workbench"))
        self._title_label.setStyleSheet("font-size: 15px; font-weight: 700; letter-spacing: -0.3px;")
        self.header_layout.addWidget(self._title_label)

        # 按钮容器
        self.agent_btn_container = QWidget()
        self.agent_btn_layout = QHBoxLayout(self.agent_btn_container)
        self.agent_btn_layout.setSpacing(8)
        self.agent_btn_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.addWidget(self.agent_btn_container)

        # Agent 信息
        info_row = QHBoxLayout()
        info_row.setSpacing(10)
        self.indicator = AgentIndicator()
        info_row.addWidget(self.indicator)
        self.agent_name_label = QLabel(t("agent_select"))
        self.agent_name_label.setWordWrap(True)
        self.agent_name_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        info_row.addWidget(self.agent_name_label)
        info_row.addStretch()

        # 清空聊天按钮
        self.btn_clear = QPushButton("🗑️")
        self.btn_clear.setFixedSize(32, 32)
        self.btn_clear.setToolTip(t("agent_clear_chat"))
        self.btn_clear.setStyleSheet("""
            QPushButton {
                border-radius: 16px;
                font-size: 16px;
                border: 1px solid rgba(255,255,255,0.1);
            }
            QPushButton:hover {
                background-color: rgba(255,100,100,0.2);
                border-color: rgba(255,100,100,0.3);
            }
        """)
        self.btn_clear.clicked.connect(self._clear_current_chat)
        info_row.addWidget(self.btn_clear)

        self.header_layout.addLayout(info_row)

        layout.addWidget(header)

        # 对话区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; }")
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chat_layout.setSpacing(6)
        self.chat_layout.setContentsMargins(8, 8, 8, 8)
        self.scroll_area.setWidget(self.chat_container)
        layout.addWidget(self.scroll_area)

        # 输入区
        input_area = QWidget()
        input_area.setObjectName("agentInput")
        input_layout = QVBoxLayout(input_area)
        input_layout.setContentsMargins(16, 12, 16, 16)
        input_layout.setSpacing(10)

        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText(t("agent_ph_input"))
        self.input_edit.setMaximumHeight(90)
        input_layout.addWidget(self.input_edit)

        btn_row = QHBoxLayout()
        self.btn_run = QPushButton(t("agent_send"))
        self.btn_run.setObjectName("primary")
        self.btn_run.setFixedHeight(36)
        self.btn_run.setFixedWidth(80)
        self.btn_run.clicked.connect(self._on_send)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_run)
        input_layout.addLayout(btn_row)

        layout.addWidget(input_area)

    def update_agent_buttons(self, agents_cfg: dict):
        while self.agent_btn_layout.count():
            item = self.agent_btn_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.agent_buttons.clear()

        emojis, colors = {}, {}
        for i, (name, info) in enumerate(agents_cfg.items()):
            emoji = info.get("emoji", "🤖")
            title = info.get("title", name)
            color = COLOR_POOL[i % len(COLOR_POOL)]
            emojis[name] = emoji
            colors[name] = color

            btn = QPushButton(emoji)
            btn.setFixedSize(48, 48)
            btn.setToolTip(title)
            btn.setStyleSheet(f"""
                QPushButton {{
                    border-radius: 24px;
                    font-size: 24px;
                    border: 2px solid transparent;
                    padding: 0;
                }}
                QPushButton:hover {{ border-color: {color}; }}
                QPushButton:checked {{
                    border-color: {color};
                    background-color: rgba(255,255,255,0.06);
                }}
            """)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, n=name: self._on_agent_selected(n))
            self.agent_btn_layout.addWidget(btn)
            self.agent_buttons[name] = btn

        self.agent_btn_layout.addStretch()
        AGENT_PANEL_GLOBALS["agent_emojis"] = emojis
        AGENT_PANEL_GLOBALS["agent_colors"] = colors

    def _on_agent_selected(self, name: str):
        # 保存当前Agent的聊天记录
        if self._current_agent and self._current_agent != name:
            self._save_current_chat()

        self._current_agent = name
        for n, btn in self.agent_buttons.items():
            btn.setChecked(n == name)
        emoji = AGENT_PANEL_GLOBALS["agent_emojis"].get(name, "🤖")
        self.agent_name_label.setText(f"{emoji}  {name}")

        # 恢复选中Agent的聊天记录
        self._restore_chat(name)

    def _on_send(self):
        text = self.input_edit.toPlainText().strip()
        if not text or not self._current_agent:
            return
        self.add_user_message(text)
        self.input_edit.clear()
        self.agent_run_requested.emit(self._current_agent, text)

    def add_user_message(self, text: str):
        bubble = ChatBubble(text, is_agent=False)
        self.chat_layout.addWidget(bubble)
        # 记录到当前Agent的历史
        if self._current_agent not in self._chat_history:
            self._chat_history[self._current_agent] = []
        self._chat_history[self._current_agent].append({"type": "user", "text": text})

    def add_agent_message(self, agent_name: str, text: str):
        bubble = ChatBubble(text, is_agent=True, agent_name=agent_name)
        self.chat_layout.addWidget(bubble)
        # 记录到当前Agent的历史
        if agent_name not in self._chat_history:
            self._chat_history[agent_name] = []
        self._chat_history[agent_name].append({"type": "agent", "text": text, "agent_name": agent_name})

    def append_stream_chunk(self, chunk: str):
        """追加流式文本块"""
        if self._stream_label is None:
            # 创建新的流式消息气泡
            bubble = ChatBubble("", is_agent=True, agent_name=self._current_agent)
            self.chat_layout.addWidget(bubble)
            self._stream_label = bubble.content_label
            self._stream_text = ""

        self._stream_text += chunk
        # 处理think标签
        display_text = self._stream_text
        if "<think>" in display_text and "</think>" not in display_text:
            # 正在思考中，显示思考状态
            think_content = display_text.split("<think>")[1]
            display_text = f"💭 *思考中...*\n\n{think_content}"
        elif "<think>" in display_text and "</think>" in display_text:
            # 思考完成，提取思考内容和回答
            parts = display_text.split("</think>")
            think_content = parts[0].split("<think>")[1]
            answer = parts[1] if len(parts) > 1 else ""
            display_text = f"💭 *思考过程:*\n{think_content}\n\n{answer}"

        if self._stream_label:
            self._stream_label.setText(display_text)
            # 滚动到底部
            self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum())

    def finalize_stream_message(self, agent_name: str):
        """完成流式消息"""
        if self._stream_label and self._stream_text:
            # 记录到历史
            if agent_name not in self._chat_history:
                self._chat_history[agent_name] = []
            self._chat_history[agent_name].append({
                "type": "agent",
                "text": self._stream_text,
                "agent_name": agent_name
            })
        self._stream_label = None
        self._stream_text = ""

    def _save_current_chat(self):
        """保存当前聊天区域的内容到当前Agent的历史"""
        # 聊天已经在 add_user_message/add_agent_message 中保存了
        pass

    def _restore_chat(self, agent_name: str):
        """恢复指定Agent的聊天记录"""
        # 清空当前聊天区域
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 恢复该Agent的聊天记录
        history = self._chat_history.get(agent_name, [])
        for msg in history:
            if msg["type"] == "user":
                bubble = ChatBubble(msg["text"], is_agent=False)
            else:
                bubble = ChatBubble(msg["text"], is_agent=True, agent_name=msg.get("agent_name", agent_name))
            self.chat_layout.addWidget(bubble)

    def set_working(self, active: bool, agent_name: str = ""):
        if active:
            color = get_color(agent_name)
            self.indicator.start(color)
            self.btn_run.setEnabled(False)
            self.btn_run.setText(t("agent_generating"))
        else:
            self.indicator.stop()
            self.btn_run.setEnabled(True)
            self.btn_run.setText(t("agent_send"))

    def retranslate(self):
        """刷新文本。"""
        self._title_label.setText(t("agent_workbench"))
        self.agent_name_label.setText(t("agent_select"))
        self.input_edit.setPlaceholderText(t("agent_ph_input"))
        self.btn_run.setText(t("agent_send"))

    def clear_chat(self):
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._chat_history.clear()

    def _clear_current_chat(self):
        """清空当前Agent的聊天记录"""
        if not self._current_agent:
            return
        # 清空当前Agent的历史
        self._chat_history.pop(self._current_agent, None)
        # 清空聊天区域
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
