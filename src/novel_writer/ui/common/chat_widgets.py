"""聊天消息控件 — 消息气泡与输入框。"""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QFrame
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent

from .chat_rendering import AGENT_PANEL_GLOBALS, md_to_html
from .styles import get_theme_colors


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
            html = md_to_html(content, fg=self._fg)
            self._label.setTextFormat(Qt.TextFormat.RichText)
            self._label.setText(html)

    def refresh_style(self):
        """刷新主题样式。"""
        self._apply_style()
        self._set_content(self._raw_content)


class ChatTextEdit(QTextEdit):
    """支持 Ctrl+Enter 发送的文本输入框。"""
    submit = Signal()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.submit.emit()
                return
        super().keyPressEvent(event)
