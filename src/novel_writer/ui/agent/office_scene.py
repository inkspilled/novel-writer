"""Agent 状态条 — 紧凑、主题感知的智能体状态总览。

原先是 QPainter 绘制的假 3D 办公室（窗户/桌子/显示器/小人）。现改为
固定高度约 104px 的横向卡片状态条：

    ┌────────────────────────┐  ┌────────────────────────┐
    │ 📋  主编               │  │ ✍️  写手               │
    │ ● 待命                 │  │ ● 写作中               │
    │ 章节规划 #2            │  │ 正文写作 #3            │
    └────────────────────────┘  └────────────────────────┘

卡片全部由 QWidget/QLabel 组合，颜色只来自 apply_theme(colors) 传入的
主题 token；卡片过多时外层 QScrollArea 横向滚动，不裁切。

对外仍保留 OfficeScene / AgentState / AgentSlot 以及原有方法签名。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from .agent_animation import AgentIndicator
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# 跨平台字体
_EMOJI_FONT = "Segoe UI Emoji" if sys.platform == "win32" else "Apple Color Emoji"
_TEXT_FONT = "Microsoft YaHei" if sys.platform == "win32" else "PingFang SC"

# 状态条尺寸（AgentPanel 直接用 OfficeScene.HEIGHT 设置固定高度）
_HEIGHT = 104
# 110px：默认 480px 宽的 Agent 面板可完整显示 4 张卡片，更多时横向滚动
_CARD_WIDTH = 110
_CARD_HEIGHT = 72
_MAX_TASK_CHARS = 8
_COLUMNS = 4


class AgentState(Enum):
    IDLE = "idle"
    WORKING = "working"
    WAITING = "waiting"
    DONE = "done"
    ERROR = "error"
    SLACKING = "slacking"


# 状态文案：待命/写作中/等待/完成/错误/摸鱼
_STATE_TEXT = {
    AgentState.IDLE: "待命",
    AgentState.WORKING: "写作中",
    AgentState.WAITING: "等待",
    AgentState.DONE: "完成",
    AgentState.ERROR: "错误",
    AgentState.SLACKING: "摸鱼",
}


@dataclass
class AgentSlot:
    """一个智能体的状态数据（卡片只负责显示，数据保存在这里）。"""

    name: str
    emoji: str
    title: str
    color: str
    state: AgentState = AgentState.IDLE
    selected: bool = False
    # 当前正在执行的任务描述
    task_desc: str = ""


def _truncate_task(text: str, limit: int = _MAX_TASK_CHARS) -> str:
    """压缩空白并截断任务描述，总长度（含省略号）不超过 limit。"""
    text = " ".join((text or "").split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _rgba(color: str, alpha: float, fallback: str = "transparent") -> str:
    """把颜色转成 rgba(...)；不引入任何新的硬编码色值。"""
    c = QColor(color or "")
    if not c.isValid():
        c = QColor(fallback or "")
    if not c.isValid():
        return "transparent"
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha:.2f})"


class _AgentCard(QFrame):
    """单个 Agent 卡片：emoji + 名称/标题 + 状态点/文案 + 当前任务。"""

    clicked = Signal(str)

    def __init__(self, slot: AgentSlot, theme: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._slot = slot
        self._theme = theme or {}

        self.setObjectName("agentCard")
        self.setFixedSize(_CARD_WIDTH, _CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(2)

        # ── 第一行：emoji + 名称/标题 ──
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)

        self.emoji_label = QLabel(self)
        self.emoji_label.setObjectName("agentEmoji")
        self.emoji_label.setFixedSize(22, 20)
        self.emoji_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_row.addWidget(self.emoji_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.title_label = QLabel(self)
        self.title_label.setObjectName("agentTitle")
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top_row.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top_row)

        # ── 第二行：状态点 + 状态文案 ──
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(5)

        self.indicator = AgentIndicator(self)
        self.indicator.setFixedSize(16, 16)
        status_row.addWidget(self.indicator, 0, Qt.AlignmentFlag.AlignVCenter)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("agentStatus")
        status_row.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignVCenter)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        # ── 第三行：当前任务 ──
        self.task_label = QLabel(self)
        self.task_label.setObjectName("agentTask")
        self.task_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.task_label)

        # 点击整张卡片都能选中 Agent（文字不拦截鼠标）
        for label in (self.emoji_label, self.title_label,
                      self.status_label, self.task_label, self.indicator):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.update_view(slot, self._theme)

    # ── 数据刷新 ──

    def update_view(self, slot: AgentSlot, theme: dict):
        self._slot = slot
        self._theme = theme or {}
        state_text = _STATE_TEXT.get(slot.state, "")
        task_text = _truncate_task(slot.task_desc)

        self.emoji_label.setText(slot.emoji or "🤖")
        self.title_label.setText(slot.title or slot.name)
        self.status_label.setText(state_text)
        self.task_label.setText(task_text or "—")

        tooltip = f"{slot.emoji or '🤖'} {slot.title or slot.name}"
        if slot.name and slot.name != slot.title:
            tooltip += f"（{slot.name}）"
        tooltip += f"\n状态：{state_text}"
        if slot.task_desc:
            tooltip += f"\n当前任务：{slot.task_desc}"
        self.setToolTip(tooltip)
        self.setAccessibleName(f"{slot.title or slot.name} {state_text}")

        self._restyle()
        if slot.state == AgentState.WORKING:
            self.indicator.start(slot.color or self._tok("accent", "#4f8cff"))
        else:
            self.indicator.stop()
            self.indicator.update_theme(self._theme)

    # ── 主题样式 ──

    def _tok(self, key: str, fallback: str) -> str:
        value = self._theme.get(key)
        return value if value else fallback

    def _restyle(self):
        surface = self._tok("surface", self._tok("card", "transparent"))
        elevated = self._tok("elevated", surface)
        border = self._tok("border", self._tok("divider", "transparent"))
        fg = self._tok("fg", "inherit")
        fg2 = self._tok("fg2", fg)
        fg3 = self._tok("fg3", fg2)
        accent = self._tok("accent", fg)
        accent_hover = self._tok("accent_hover", accent)
        accent_bg = self._tok("accent_bg", "transparent")

        slot = self._slot
        state = slot.state
        selected = slot.selected
        working = state == AgentState.WORKING

        # 卡片底色 / 边框：选中和 WORKING 都用 accent 强调
        if selected:
            bg, border_color, border_width = accent_bg, accent, 2
        elif working:
            bg, border_color, border_width = accent_bg, accent, 1
        else:
            bg, border_color, border_width = surface, border, 1

        if selected or working:
            hover_bg, hover_border = bg, accent
        else:
            hover_bg, hover_border = elevated, fg3

        title_color = accent if working else fg
        if state == AgentState.WORKING:
            status_color = accent
        elif state == AgentState.ERROR:
            status_color = fg
        elif state == AgentState.SLACKING:
            status_color = fg3
        else:
            status_color = fg2
        task_color = fg2 if slot.task_desc else fg3

        # 状态点颜色只从主题 token（以及传入的 Agent 自身颜色）取
        if state == AgentState.WORKING:
            dot_color = accent
        elif state == AgentState.WAITING:
            dot_color = accent_hover
        elif state == AgentState.DONE:
            dot_color = accent
        elif state == AgentState.ERROR:
            dot_color = slot.color or accent
        else:  # IDLE / SLACKING
            dot_color = fg3

        if state == AgentState.WAITING:
            dot_bg, dot_border = "transparent", f"1px solid {dot_color}"
        else:
            dot_bg, dot_border = dot_color, "none"

        emoji_bg = _rgba(slot.color, 0.14, accent)

        self.setStyleSheet(f"""
            QFrame#agentCard {{
                background-color: {bg};
                border: {border_width}px solid {border_color};
                border-radius: 10px;
            }}
            QFrame#agentCard:hover {{
                background-color: {hover_bg};
                border-color: {hover_border};
            }}
            QLabel#agentEmoji {{
                background-color: {emoji_bg};
                border-radius: 7px;
                font-family: "{_EMOJI_FONT}";
                font-size: 15px;
            }}
            QLabel#agentTitle {{
                color: {title_color};
                font-family: "{_TEXT_FONT}";
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }}
            QLabel#agentStatus {{
                color: {status_color};
                font-family: "{_TEXT_FONT}";
                font-size: 11px;
                background: transparent;
            }}
            QLabel#agentTask {{
                color: {task_color};
                font-family: "{_TEXT_FONT}";
                font-size: 11px;
                background: transparent;
            }}
        """)

    # ── 交互 ──

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._slot.name)
            event.accept()
            return
        super().mousePressEvent(event)


class OfficeScene(QWidget):
    """Agent 状态条：横向排列的 Agent 卡片，超出宽度时横向滚动。"""

    #: 状态条固定高度（96–110px），AgentPanel 用同一个值设置高度
    HEIGHT = _HEIGHT

    agent_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._slots: list[AgentSlot] = []
        self._cards: dict[str, _AgentCard] = {}
        self._theme: dict = {}

        self.setObjectName("agentStatusBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("agentStatusScroll")
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._content = QWidget()
        self._content.setObjectName("agentStatusContent")
        self._layout = QGridLayout(self._content)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setHorizontalSpacing(6)
        self._layout.setVerticalSpacing(6)
        for col in range(_COLUMNS):
            self._layout.setColumnStretch(col, 0)

        self._scroll.setWidget(self._content)
        outer.addWidget(self._scroll)

        self.apply_theme({})

    # ── 主题 ──

    def apply_theme(self, colors: dict):
        """按主题 token 刷新状态条与所有卡片。"""
        self._theme = dict(colors or {})
        self._apply_shell_style()
        for slot in self._slots:
            card = self._cards.get(slot.name)
            if card is not None:
                card.update_view(slot, self._theme)

    def _tok(self, key: str, fallback: str) -> str:
        value = self._theme.get(key)
        return value if value else fallback

    def _apply_shell_style(self):
        bg = self._tok("bg", self._tok("surface", "transparent"))
        fg3 = self._tok("fg3", self._tok("fg2", "inherit"))

        self.setStyleSheet(f"""
            QWidget#agentStatusBar {{
                background-color: {bg};
            }}
            QScrollArea#agentStatusScroll {{
                background: transparent;
                border: none;
            }}
            QScrollArea#agentStatusScroll > QWidget {{
                background: transparent;
            }}
            QWidget#agentStatusContent {{
                background: transparent;
            }}
            QScrollBar:horizontal {{
                height: 6px;
                background: transparent;
                margin: 0px 8px;
            }}
            QScrollBar::handle:horizontal {{
                background-color: {fg3};
                border-radius: 3px;
                min-width: 24px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background-color: {self._tok("fg2", fg3)};
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0px;
                background: transparent;
            }}
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {{
                background: transparent;
            }}
        """)

    # ── 外部 API（保持原有行为） ──

    def setup_agents(self, agents_cfg: dict, colors: dict):
        """按智能体配置重建卡片。colors 为各 Agent 的标识色。"""
        self._clear_cards()
        self._slots.clear()
        self._cards.clear()

        for name, info in (agents_cfg or {}).items():
            slot = AgentSlot(
                name=name,
                emoji=info.get("emoji", "🤖"),
                title=info.get("title", name),
                color=colors.get(name, "") if colors else "",
            )
            self._slots.append(slot)

            card = _AgentCard(slot, self._theme, self._content)
            card.clicked.connect(self.agent_clicked)
            index = len(self._cards)
            self._layout.addWidget(card, index // _COLUMNS, index % _COLUMNS)
            self._cards[name] = card

        self._layout.activate()
        rows = max(1, (len(self._cards) + _COLUMNS - 1) // _COLUMNS)
        # 一行 104px；每多一行 +78px。最多展示两行（8 个），更多则纵向滚动，
        # 避免 Agent 数量增长把聊天区无限压缩。
        full_height = 104 + (rows - 1) * 78
        max_height = 104 + 78
        self._content.setMinimumHeight(max(0, full_height - 12))
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if full_height > max_height else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setFixedHeight(min(full_height, max_height))
        self.update()

    def set_agent_state(self, agent_name: str, state: AgentState,
                        task_desc: str = ""):
        for slot in self._slots:
            if slot.name == agent_name:
                slot.state = state
                slot.task_desc = task_desc
                self._refresh_card(slot)
                return

    def set_selected(self, agent_name: str):
        for slot in self._slots:
            slot.selected = (slot.name == agent_name)
            self._refresh_card(slot)
        card = self._cards.get(agent_name)
        if card is not None:
            self._ensure_visible(card)

    def reset_states(self, state: AgentState = AgentState.IDLE):
        """把所有 Agent 置为同一状态并清空任务（供工作流结束等场景使用）。"""
        for slot in self._slots:
            slot.state = state
            slot.task_desc = ""
            self._refresh_card(slot)

    def iter_slots(self) -> tuple[AgentSlot, ...]:
        """返回当前 AgentSlot 的快照，替代外部直接访问 _slots。"""
        return tuple(self._slots)

    # ── 内部 ──

    def _refresh_card(self, slot: AgentSlot):
        card = self._cards.get(slot.name)
        if card is not None:
            card.update_view(slot, self._theme)

    def _ensure_visible(self, card: _AgentCard):
        self._layout.activate()
        self._scroll.ensureWidgetVisible(card, 8, 0)

    def _clear_cards(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
