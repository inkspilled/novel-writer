"""办公室场景 — 智能体们在虚拟办公室里工作、摸鱼、等待。"""
from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass
from enum import Enum

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer, Signal, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QRadialGradient

# 跨平台字体
_EMOJI_FONT = "Segoe UI Emoji" if sys.platform == "win32" else "Apple Color Emoji"
_TEXT_FONT = "Microsoft YaHei" if sys.platform == "win32" else "PingFang SC"


class AgentState(Enum):
    IDLE = "idle"
    WORKING = "working"
    WAITING = "waiting"
    DONE = "done"
    ERROR = "error"
    SLACKING = "slacking"
    GONE = "gone"


class IdleBehavior(Enum):
    COFFEE = "coffee"
    NAP = "nap"
    PHONE = "phone"
    STARE = "stare"
    STRETCH = "stretch"
    WANDER = "wander"


@dataclass
class AgentSlot:
    name: str
    emoji: str
    title: str
    color: str
    state: AgentState = AgentState.IDLE
    behavior: IdleBehavior = IdleBehavior.STARE
    desk_x: float = 0.0
    desk_y: float = 0.0
    frame: int = 0
    behavior_timer: int = 0
    behavior_duration: int = 300
    offset_x: float = 0.0
    offset_y: float = 0.0
    target_offset_x: float = 0.0
    target_offset_y: float = 0.0
    selected: bool = False
    escaped: bool = False
    escape_timer: int = 0
    # M-09 修复：预计算屏幕线条宽度，避免 paintEvent 中使用随机数
    screen_line_widths: list = None

    def __post_init__(self):
        if self.screen_line_widths is None:
            self.screen_line_widths = [random.randint(8, 18) for _ in range(4)]
    # 当前正在执行的任务描述
    task_desc: str = ""


class OfficeScene(QWidget):
    """办公室场景画布。"""

    agent_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slots: list[AgentSlot] = []
        self._frame_counter = 0
        self._hover_slot: AgentSlot | None = None
        self.setMouseTracking(True)

        # M-08/M-10 修复：预创建常用绘图对象，避免 paintEvent 中反复创建
        self._pen_no = Qt.PenStyle.NoPen
        self._brush_clear = QColor(255, 255, 255, 20)
        self._pen_window = QPen(QColor("#2a2a4a"), 2)
        self._pen_floor = QPen(QColor("#ffffff"), 1)
        self._pen_desk = QPen(QColor("#2a2a4a"), 1)
        self._brush_desk = QColor("#1c1c2e")
        self._pen_monitor = QPen(QColor("#3a3a5a"), 1)
        self._brush_monitor_work = QColor("#0a2a4a")
        self._brush_monitor_idle = QColor("#12121e")
        self._brush_monitor_slack = QColor("#1a1a3a")
        self._pen_screen_line = QPen(QColor("#4da6ff"), 1)
        self._pen_stand = QPen(QColor("#3a3a5a"), 1)
        self._pen_keyboard = QPen(QColor("#2a2a3a"), 1)
        self._brush_keyboard = QColor("#18182a")
        self._pen_title = QPen(QColor("#8e8e9a"), 1)
        self._pen_clock = QPen(QColor("#4a4a6a"), 1)
        self._brush_clock = QColor("#16161d")
        self._pen_clock_hand = QPen(QColor("#8e8e9a"), 2)
        self._pen_area = QPen(QColor("#2a2a4a"), 1)
        self._brush_area = QColor("#16161d")
        self._pen_arc = QPen(QColor(Qt.GlobalColor.white), 2)  # 会被 slot.color 覆盖
        self._font_emoji_12 = QFont(_EMOJI_FONT, 12)
        self._font_emoji_20 = QFont(_EMOJI_FONT, 20)
        self._font_emoji_24 = QFont(_EMOJI_FONT, 24)
        self._font_text_7 = QFont(_TEXT_FONT, 7)
        self._font_text_8 = QFont(_TEXT_FONT, 8)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(150)  # 150ms ≈ 7fps，动画更流畅

    def setup_agents(self, agents_cfg: dict, colors: dict):
        self._slots.clear()
        count = len(agents_cfg)
        if count == 0:
            return
        margin = 0.06
        usable = 1.0 - 2 * margin
        for i, (name, info) in enumerate(agents_cfg.items()):
            slot = AgentSlot(
                name=name,
                emoji=info.get("emoji", "🤖"),
                title=info.get("title", name),
                color=colors.get(name, "#6e8efb"),
                desk_x=margin + usable * (i + 0.5) / count,
                desk_y=0.45,
                behavior_duration=random.randint(200, 500),
            )
            self._slots.append(slot)
        self.update()

    def set_agent_state(self, agent_name: str, state: AgentState, task_desc: str = ""):
        for slot in self._slots:
            if slot.name == agent_name:
                slot.state = state
                slot.frame = 0
                slot.behavior_timer = 0
                slot.offset_x = 0
                slot.offset_y = 0
                slot.task_desc = task_desc
                if state == AgentState.IDLE:
                    slot.behavior = random.choice(list(IdleBehavior))
                    slot.behavior_duration = random.randint(200, 500)
                if state != AgentState.SLACKING:
                    slot.escaped = False
                self.update()
                return

    def set_selected(self, agent_name: str):
        for slot in self._slots:
            slot.selected = (slot.name == agent_name)
        self.update()

    # ── 动画驱动 ──

    def _tick(self):
        self._frame_counter += 1
        for slot in self._slots:
            slot.frame += 1
            if slot.state == AgentState.IDLE and not slot.escaped:
                slot.behavior_timer += 1
                if slot.behavior_timer >= slot.behavior_duration:
                    slot.behavior_timer = 0
                    slot.behavior_duration = random.randint(200, 500)
                    if random.random() < 0.03:
                        slot.state = AgentState.SLACKING
                        slot.escaped = True
                        slot.escape_timer = random.randint(300, 600)
                    else:
                        slot.behavior = random.choice(list(IdleBehavior))
                if slot.behavior == IdleBehavior.WANDER:
                    dx = slot.target_offset_x - slot.offset_x
                    dy = slot.target_offset_y - slot.offset_y
                    if abs(dx) < 0.005 and abs(dy) < 0.005:
                        slot.target_offset_x = random.uniform(-0.04, 0.04)
                        slot.target_offset_y = random.uniform(-0.02, 0.02)
                    else:
                        slot.offset_x += 0.002 * (1 if dx > 0 else -1)
                        slot.offset_y += 0.002 * (1 if dy > 0 else -1)
            elif slot.state == AgentState.SLACKING and slot.escaped:
                slot.escape_timer -= 1
                if slot.escape_timer <= 0:
                    slot.state = AgentState.IDLE
                    slot.escaped = False
                    slot.behavior = random.choice(list(IdleBehavior))
            elif slot.state == AgentState.DONE:
                if slot.frame > 20:
                    slot.state = AgentState.IDLE
                    slot.behavior = random.choice(list(IdleBehavior))
        self.update()

    # ── 绘制 ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        self._draw_background(painter, w, h)
        for slot in self._slots:
            self._draw_desk(painter, slot, w, h)
            self._draw_agent(painter, slot, w, h)
            self._draw_status_effect(painter, slot, w, h)
        # 最后绘制工作中的高亮（在最上层）
        for slot in self._slots:
            if slot.state == AgentState.WORKING:
                self._draw_working_highlight(painter, slot, w, h)
        painter.end()

    def _draw_background(self, painter: QPainter, w: int, h: int):
        painter.fillRect(0, 0, w, h, QColor("#1a1a2e"))
        # 窗户
        win_y = int(h * 0.02)
        win_h = int(h * 0.16)
        painter.fillRect(int(w * 0.12), win_y, int(w * 0.76), win_h, QColor("#0a1628"))
        painter.setPen(self._pen_window)
        painter.drawRect(int(w * 0.12), win_y, int(w * 0.76), win_h)
        painter.drawLine(int(w * 0.5), win_y, int(w * 0.5), win_y + win_h)
        # 云
        cx = (self._frame_counter * 2) % (w + 100) - 50
        painter.setPen(self._pen_no)
        painter.setBrush(self._brush_clear)
        painter.drawEllipse(cx, win_y + 8, 40, 12)
        painter.drawEllipse(cx + 15, win_y + 4, 30, 12)
        painter.drawEllipse(cx + 200, win_y + 14, 35, 10)
        # 地板线
        painter.setPen(self._pen_floor)
        painter.setOpacity(0.03)
        for i in range(8):
            painter.drawLine(0, int(h * 0.25 + i * h * 0.1), w, int(h * 0.25 + i * h * 0.1))
        painter.setOpacity(1.0)
        # 植物
        px, py = int(w * 0.03), int(h * 0.22)
        painter.setBrush(QColor("#8B4513"))
        painter.setPen(self._pen_no)
        painter.drawRoundedRect(px, py + 15, 16, 12, 3, 3)
        painter.setBrush(QColor("#2d6a4f"))
        painter.drawEllipse(px + 2, py, 12, 18)
        painter.drawEllipse(px - 2, py + 3, 10, 14)
        # 时钟
        cx2, cy2 = int(w * 0.94), int(h * 0.06)
        painter.setPen(self._pen_clock)
        painter.setBrush(self._brush_clock)
        painter.drawEllipse(cx2 - 12, cy2 - 12, 24, 24)
        painter.setPen(self._pen_clock_hand)
        a = (self._frame_counter % 360) * 0.1
        painter.drawLine(cx2, cy2, cx2 + int(6 * math.sin(a)), cy2 - int(6 * math.cos(a)))
        # 茶水间 & 健身角
        painter.setFont(self._font_emoji_12)
        painter.setPen(self._pen_area)
        painter.setBrush(self._brush_area)
        painter.drawRoundedRect(int(w * 0.87), int(h * 0.72), 50, 30, 4, 4)
        painter.drawText(int(w * 0.88), int(h * 0.74) + 12, "☕🍵")
        painter.drawRoundedRect(int(w * 0.02), int(h * 0.72), 45, 30, 4, 4)
        painter.drawText(int(w * 0.03), int(h * 0.74) + 12, "🏋️🏃")

    def _draw_desk(self, painter: QPainter, slot: AgentSlot, w: int, h: int):
        # 桌子位置固定，不随 agent 移动
        x = int(slot.desk_x * w)
        y = int(slot.desk_y * h)
        # 桌子
        dw, dh = 60, 30
        desk = QRectF(x - dw / 2, y - dh / 2, dw, dh)
        painter.setPen(self._pen_desk)
        painter.setBrush(self._brush_desk)
        painter.drawRoundedRect(desk, 4, 4)
        # 显示器
        mw, mh = 28, 18
        mon = QRectF(x - mw / 2, y - dh / 2 - mh - 2, mw, mh)
        if slot.state == AgentState.WORKING:
            painter.setBrush(self._brush_monitor_work)
        elif slot.state == AgentState.SLACKING and slot.behavior == IdleBehavior.PHONE:
            painter.setBrush(self._brush_monitor_slack)
        else:
            painter.setBrush(self._brush_monitor_idle)
        painter.setPen(self._pen_monitor)
        painter.drawRoundedRect(mon, 2, 2)
        # 屏幕内容
        if slot.state == AgentState.WORKING:
            painter.setPen(self._pen_screen_line)
            for line in range(4):
                ly = int(mon.top() + 4 + line * 4)
                lx = int(mon.left() + 3)
                bw = slot.screen_line_widths[line] if slot.screen_line_widths and line < len(slot.screen_line_widths) else 12
                painter.drawLine(lx, ly, lx + bw, ly)
        # 底座
        painter.setPen(self._pen_stand)
        painter.drawLine(x, int(mon.bottom()), x, int(desk.top()))
        # 键盘
        kb = QRectF(x - 12, y - 5, 24, 7)
        painter.setPen(self._pen_keyboard)
        painter.setBrush(self._brush_keyboard)
        painter.drawRoundedRect(kb, 1, 1)

    def _draw_agent(self, painter: QPainter, slot: AgentSlot, w: int, h: int):
        x = int(slot.desk_x * w + slot.offset_x * w)
        y = int(slot.desk_y * h + slot.offset_y * h)

        if slot.escaped:
            painter.setFont(self._font_emoji_12)
            painter.drawText(x - 6, y + 28, "💨")
            return

        # 选中光圈
        if slot.selected:
            painter.setPen(self._pen_no)
            painter.setBrush(QColor(slot.color))
            painter.setOpacity(0.2)
            painter.drawEllipse(QPointF(x, y + 20), 26, 26)
            painter.setOpacity(1.0)

        # emoji
        emoji_size = 24 if slot.state == AgentState.WORKING else 20
        painter.setFont(self._font_emoji_24 if emoji_size == 24 else self._font_emoji_20)
        er = QRectF(x - 14, y + 8, 28, 28)

        if slot.state == AgentState.WORKING:
            jitter = math.sin(slot.frame * 0.6) * 2
            er.moveTop(er.top() + jitter)
        elif slot.state == AgentState.DONE:
            bounce = abs(math.sin(slot.frame * 0.3)) * 5
            er.moveTop(er.top() - bounce)
        elif slot.state == AgentState.ERROR:
            shake = math.sin(slot.frame * 0.9) * 3
            er.moveLeft(er.left() + shake)

        painter.drawText(er, Qt.AlignmentFlag.AlignCenter, slot.emoji)

        # 标题
        painter.setPen(self._pen_title)
        painter.setFont(self._font_text_7)
        painter.drawText(QRectF(x - 24, y + 38, 48, 12), Qt.AlignmentFlag.AlignCenter, slot.title)

    def _draw_working_highlight(self, painter: QPainter, slot: AgentSlot, w: int, h: int):
        """工作中的 Agent — 大光圈 + 脉冲 + 任务标签。"""
        x = int(slot.desk_x * w + slot.offset_x * w)
        y = int(slot.desk_y * h + slot.offset_y * h)

        # 脉冲光圈（大范围，明显）
        pulse = (math.sin(slot.frame * 0.15) + 1) / 2  # 0~1
        radius = 30 + pulse * 12
        gradient = QRadialGradient(QPointF(x, y + 18), radius)
        c = QColor(slot.color)
        c.setAlpha(int(40 + pulse * 40))
        gradient.setColorAt(0, c)
        c2 = QColor(slot.color)
        c2.setAlpha(0)
        gradient.setColorAt(1, c2)
        painter.setPen(self._pen_no)
        painter.setBrush(gradient)
        painter.drawEllipse(QPointF(x, y + 18), int(radius), int(radius))

        # 旋转弧线
        painter.setPen(QPen(QColor(slot.color), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        start_angle = (slot.frame * 8) % 360 * 16
        painter.drawArc(int(x - 22), int(y + 18 - 22), 44, 44, start_angle, 120 * 16)

        # 任务标签（气泡）
        if slot.task_desc:
            painter.setFont(self._font_text_8)
            fm = painter.fontMetrics()
            text = slot.task_desc[:12] + "..." if len(slot.task_desc) > 12 else slot.task_desc
            tw = fm.horizontalAdvance(text) + 12
            th = 18
            bx = x - tw / 2
            by = y - 20
            painter.setPen(QPen(QColor(slot.color), 1))
            painter.setBrush(QColor("#1a1a2e"))
            painter.drawRoundedRect(int(bx), int(by), tw, th, 8, 8)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(int(bx), int(by), tw, th, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_status_effect(self, painter: QPainter, slot: AgentSlot, w: int, h: int):
        x = int(slot.desk_x * w + slot.offset_x * w)
        y = int(slot.desk_y * h + slot.offset_y * h)
        if slot.escaped:
            return

        painter.setFont(self._font_emoji_12)

        if slot.state == AgentState.WORKING:
            # 敲键盘粒子
            for i in range(4):
                px = x - 10 + (slot.frame * 7 + i * 9) % 20
                py = y + 3 + abs(math.sin(slot.frame * 0.4 + i)) * 10
                painter.setPen(self._pen_no)
                painter.setBrush(QColor(slot.color))
                painter.setOpacity(0.5 + 0.3 * math.sin(slot.frame * 0.2 + i))
                painter.drawEllipse(int(px), int(py), 3, 3)
            painter.setOpacity(1.0)

        elif slot.state == AgentState.DONE:
            painter.drawText(x + 16, y + 10, "👍")

        elif slot.state == AgentState.ERROR:
            painter.drawText(x + 16, y + 10, "❗")

        elif slot.state == AgentState.IDLE:
            if slot.behavior == IdleBehavior.NAP:
                phase = (slot.frame // 3) % 3
                painter.setPen(QPen(QColor("#6e6e73"), 1))
                painter.setFont(QFont("monospace", 9))
                painter.drawText(x + 14, y + 6, "Z" + "z" * phase)
            elif slot.behavior == IdleBehavior.COFFEE:
                painter.drawText(x + 16, y + 10, "☕")
            elif slot.behavior == IdleBehavior.PHONE:
                painter.drawText(x + 16, y + 10, "📱")

        elif slot.state == AgentState.WAITING:
            painter.drawText(x + 16, y + 10, "⏳")

    # ── 鼠标交互 ──

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        slot = self._slot_at(event.position().x(), event.position().y())
        if slot:
            self.agent_clicked.emit(slot.name)

    def mouseMoveEvent(self, event):
        slot = self._slot_at(event.position().x(), event.position().y())
        if slot != self._hover_slot:
            self._hover_slot = slot
            self.setCursor(Qt.CursorShape.PointingHandCursor if slot else Qt.CursorShape.ArrowCursor)

    def _slot_at(self, mx: float, my: float) -> AgentSlot | None:
        w, h = self.width(), self.height()
        for slot in self._slots:
            if slot.escaped:
                continue
            sx = slot.desk_x * w + slot.offset_x * w
            sy = slot.desk_y * h + slot.offset_y * h
            dx, dy = mx - sx, my - (sy + 18)
            if dx * dx + dy * dy < 28 * 28:
                return slot
        return None
