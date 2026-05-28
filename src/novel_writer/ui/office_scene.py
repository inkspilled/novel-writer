"""办公室场景 — 智能体们在虚拟办公室里工作、摸鱼、等待。

用 QPainter 绘制简约办公室背景 + Agent 角色动画。
不依赖外部图片资源，全部用几何图形 + emoji 实现。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer, Signal, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QPainterPath


# ── Agent 状态 ──

class AgentState(Enum):
    IDLE = "idle"           # 空闲（随机行为）
    WORKING = "working"     # 工作中（敲键盘）
    WAITING = "waiting"     # 等待中（喝咖啡/踱步）
    DONE = "done"           # 完成（伸懒腰）
    ERROR = "error"         # 出错（抱头）
    SLACKING = "slacking"   # 摸鱼（打游戏/睡觉）
    GONE = "gone"           # 翻墙溜了


# ── 空闲时的随机行为 ──

class IdleBehavior(Enum):
    COFFEE = "coffee"       # 喝咖啡
    NAP = "nap"             # 打盹
    PHONE = "phone"         # 刷手机
    STARE = "stare"         # 发呆看窗外
    STRETCH = "stretch"     # 伸懒腰
    WANDER = "wander"       # 走来走去


# ── Agent 角色数据 ──

@dataclass
class AgentSlot:
    """办公室里一个 Agent 的位置和状态。"""
    name: str           # agent 配置名
    emoji: str          # 显示的 emoji
    title: str          # 中文标题
    color: str          # 主题色
    state: AgentState = AgentState.IDLE
    behavior: IdleBehavior = IdleBehavior.STARE
    # 工位位置（相对坐标 0-1，运行时换算为像素）
    desk_x: float = 0.0
    desk_y: float = 0.0
    # 动画帧计数
    frame: int = 0
    # 行为切换计时（帧数）
    behavior_timer: int = 0
    behavior_duration: int = 300  # 300 帧 ≈ 60 秒 (200ms/帧)
    # 位移动画（踱步/走向咖啡机）
    offset_x: float = 0.0
    offset_y: float = 0.0
    target_offset_x: float = 0.0
    target_offset_y: float = 0.0
    # 是否被选中
    selected: bool = False
    # 翻墙溜号标记
    escaped: bool = False
    escape_timer: int = 0


# ── 办公室场景 ──

class OfficeScene(QWidget):
    """办公室场景画布。"""

    agent_clicked = Signal(str)  # agent_name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(160)
        self._slots: list[AgentSlot] = []
        self._frame_counter = 0
        self._hover_slot: AgentSlot | None = None
        self.setMouseTracking(True)

        # 动画定时器（200ms ≈ 5fps，足够表现状态且节省 CPU）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(200)

    # ── 初始化 Agent ──

    def setup_agents(self, agents_cfg: dict, colors: dict):
        """从 agents.json 配置初始化 Agent 角色。"""
        self._slots.clear()
        count = len(agents_cfg)
        if count == 0:
            return

        # 工位布局：水平均匀分布
        margin = 0.08
        usable = 1.0 - 2 * margin
        for i, (name, info) in enumerate(agents_cfg.items()):
            emoji = info.get("emoji", "🤖")
            title = info.get("title", name)
            color = colors.get(name, "#6e8efb")
            x = margin + usable * (i + 0.5) / count
            y = 0.42  # 工位行的 y 坐标
            slot = AgentSlot(
                name=name, emoji=emoji, title=title, color=color,
                desk_x=x, desk_y=y,
                behavior_duration=random.randint(200, 500),
            )
            self._slots.append(slot)

        self.update()

    def set_agent_state(self, agent_name: str, state: AgentState):
        """设置某个 Agent 的状态。"""
        for slot in self._slots:
            if slot.name == agent_name:
                old_state = slot.state
                slot.state = state
                slot.frame = 0
                slot.behavior_timer = 0
                slot.offset_x = 0
                slot.offset_y = 0
                if state == AgentState.IDLE:
                    slot.behavior = random.choice(list(IdleBehavior))
                    slot.behavior_duration = random.randint(200, 500)
                if state != AgentState.SLACKING:
                    slot.escaped = False
                self.update()
                return

    def set_selected(self, agent_name: str):
        """高亮选中的 Agent。"""
        for slot in self._slots:
            slot.selected = (slot.name == agent_name)
        self.update()

    def get_slot(self, agent_name: str) -> AgentSlot | None:
        for slot in self._slots:
            if slot.name == agent_name:
                return slot
        return None

    # ── 动画驱动 ──

    def _tick(self):
        self._frame_counter += 1
        changed = False
        for slot in self._slots:
            slot.frame += 1
            if slot.state == AgentState.IDLE and not slot.escaped:
                slot.behavior_timer += 1
                # 随机切换行为
                if slot.behavior_timer >= slot.behavior_duration:
                    slot.behavior_timer = 0
                    slot.behavior_duration = random.randint(200, 500)
                    # 小概率翻墙溜号（5%）
                    if random.random() < 0.05:
                        slot.state = AgentState.SLACKING
                        slot.escaped = True
                        slot.escape_timer = random.randint(300, 600)
                    else:
                        slot.behavior = random.choice(list(IdleBehavior))
                    changed = True
                # 踱步动画
                if slot.behavior == IdleBehavior.WANDER:
                    speed = 0.002
                    dx = slot.target_offset_x - slot.offset_x
                    dy = slot.target_offset_y - slot.offset_y
                    if abs(dx) < 0.005 and abs(dy) < 0.005:
                        slot.target_offset_x = random.uniform(-0.05, 0.05)
                        slot.target_offset_y = random.uniform(-0.03, 0.03)
                    else:
                        slot.offset_x += speed * (1 if dx > 0 else -1)
                        slot.offset_y += speed * (1 if dy > 0 else -1)
                    changed = True

            elif slot.state == AgentState.SLACKING and slot.escaped:
                slot.escape_timer -= 1
                if slot.escape_timer <= 0:
                    slot.state = AgentState.IDLE
                    slot.escaped = False
                    slot.behavior = random.choice(list(IdleBehavior))
                    changed = True

            elif slot.state == AgentState.DONE:
                # 完成状态持续 3 秒后回到 idle
                if slot.frame > 15:
                    slot.state = AgentState.IDLE
                    slot.behavior = random.choice(list(IdleBehavior))
                    changed = True

            elif slot.state == AgentState.WORKING:
                changed = True  # 键盘动画需要持续刷新

        if changed or any(s.state == AgentState.WORKING for s in self._slots):
            self.update()

    # ── 绘制 ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # 背景
        self._draw_background(painter, w, h)

        # 工位和 Agent
        for slot in self._slots:
            self._draw_desk(painter, slot, w, h)
            self._draw_agent(painter, slot, w, h)
            self._draw_status_effect(painter, slot, w, h)

        painter.end()

    def _draw_background(self, painter: QPainter, w: int, h: int):
        """绘制办公室背景。"""
        # 地板
        painter.fillRect(0, 0, w, h, QColor("#1a1a2e"))

        # 窗户（顶部）
        win_y = int(h * 0.02)
        win_h = int(h * 0.18)
        painter.fillRect(int(w * 0.15), win_y, int(w * 0.7), win_h, QColor("#0a1628"))
        painter.setPen(QPen(QColor("#2a2a4a"), 2))
        painter.drawRect(int(w * 0.15), win_y, int(w * 0.7), win_h)
        # 窗户分隔线
        painter.drawLine(int(w * 0.5), win_y, int(w * 0.5), win_y + win_h)

        # 窗外的云
        cloud_x = (self._frame_counter * 2) % (w + 100) - 50
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 20))
        painter.drawEllipse(cloud_x, win_y + 8, 40, 12)
        painter.drawEllipse(cloud_x + 15, win_y + 4, 30, 12)
        painter.drawEllipse(cloud_x + 200, win_y + 14, 35, 10)

        # 地板线（透视感）
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.setOpacity(0.03)
        for i in range(8):
            y = int(h * 0.25 + i * h * 0.1)
            painter.drawLine(0, y, w, y)
        painter.setOpacity(1.0)

        # 左上角植物
        pot_x, pot_y = int(w * 0.04), int(h * 0.22)
        painter.setBrush(QColor("#8B4513"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(pot_x, pot_y + 15, 16, 12, 3, 3)
        painter.setBrush(QColor("#2d6a4f"))
        painter.drawEllipse(pot_x + 2, pot_y, 12, 18)
        painter.drawEllipse(pot_x - 2, pot_y + 3, 10, 14)
        painter.drawEllipse(pot_x + 8, pot_y + 2, 10, 15)

        # 右上角时钟
        clock_x, clock_y = int(w * 0.93), int(h * 0.06)
        painter.setPen(QPen(QColor("#4a4a6a"), 1))
        painter.setBrush(QColor("#16161d"))
        painter.drawEllipse(clock_x - 12, clock_y - 12, 24, 24)
        # 时针
        painter.setPen(QPen(QColor("#8e8e9a"), 2))
        angle = (self._frame_counter % 360) * 0.1
        painter.drawLine(clock_x, clock_y,
                        clock_x + int(6 * math.sin(angle)),
                        clock_y - int(6 * math.cos(angle)))
        # 分针
        painter.setPen(QPen(QColor("#6e6e73"), 1))
        angle2 = (self._frame_counter % 3600) * 0.001
        painter.drawLine(clock_x, clock_y,
                        clock_x + int(9 * math.sin(angle2)),
                        clock_y - int(9 * math.cos(angle2)))

        # 茶水间（右下角）
        tea_x, tea_y = int(w * 0.88), int(h * 0.72)
        painter.setPen(QPen(QColor("#2a2a4a"), 1))
        painter.setBrush(QColor("#16161d"))
        painter.drawRoundedRect(tea_x, tea_y, 50, 30, 4, 4)
        painter.setPen(QPen(QColor("#4a4a6a"), 1))
        painter.setFont(QFont("Apple Color Emoji", 10))
        painter.drawText(tea_x + 5, tea_y + 12, "☕")
        painter.drawText(tea_x + 25, tea_y + 12, "🍵")

        # 健身角（左下角）
        gym_x, gym_y = int(w * 0.03), int(h * 0.72)
        painter.setPen(QPen(QColor("#2a2a4a"), 1))
        painter.setBrush(QColor("#16161d"))
        painter.drawRoundedRect(gym_x, gym_y, 45, 30, 4, 4)
        painter.setFont(QFont("Apple Color Emoji", 10))
        painter.drawText(gym_x + 5, gym_y + 12, "🏋️")
        painter.drawText(gym_x + 25, gym_y + 12, "🏃")

        # 翻墙点（右边缘中部）
        wall_x, wall_y = w - 8, int(h * 0.45)
        painter.setPen(QPen(QColor("#3a3a5a"), 1))
        painter.setBrush(QColor("#2a2a3e"))
        painter.drawRect(wall_x - 4, wall_y, 12, 40)
        painter.setPen(QPen(QColor("#5a5a7a"), 1))
        painter.setFont(QFont("Apple Color Emoji", 8))
        painter.drawText(wall_x - 2, wall_y + 15, "🚪")

    def _draw_desk(self, painter: QPainter, slot: AgentSlot, w: int, h: int):
        """绘制工位（桌子 + 显示器 + 键盘）。"""
        x = int(slot.desk_x * w + slot.offset_x * w)
        y = int(slot.desk_y * h + slot.offset_y * h)

        # 桌子
        desk_w, desk_h = 56, 28
        desk_rect = QRectF(x - desk_w / 2, y - desk_h / 2, desk_w, desk_h)
        painter.setPen(QPen(QColor("#2a2a4a"), 1))
        painter.setBrush(QColor("#1c1c2e"))
        painter.drawRoundedRect(desk_rect, 4, 4)

        # 显示器
        mon_w, mon_h = 24, 16
        mon_rect = QRectF(x - mon_w / 2, y - desk_h / 2 - mon_h - 2, mon_w, mon_h)
        painter.setPen(QPen(QColor("#3a3a5a"), 1))
        if slot.state == AgentState.WORKING:
            # 工作中屏幕亮
            painter.setBrush(QColor("#0a2a4a"))
        elif slot.state == AgentState.SLACKING and slot.behavior == IdleBehavior.PHONE:
            # 刷手机屏幕亮
            painter.setBrush(QColor("#1a1a3a"))
        else:
            painter.setBrush(QColor("#12121e"))
        painter.drawRoundedRect(mon_rect, 2, 2)

        # 屏幕内容（工作中时有文字滚动效果）
        if slot.state == AgentState.WORKING:
            painter.setPen(QPen(QColor("#4da6ff"), 1))
            painter.setFont(QFont("monospace", 6))
            scroll_offset = (slot.frame * 3) % 30
            for line in range(3):
                line_y = int(mon_rect.top() + 4 + line * 4)
                line_x = int(mon_rect.left() + 3)
                bar_w = random.randint(8, 16)
                painter.drawLine(line_x, line_y, line_x + bar_w, line_y)

        # 显示器底座
        painter.setPen(QPen(QColor("#3a3a5a"), 1))
        painter.drawLine(x, int(mon_rect.bottom()), x, int(desk_rect.top()))

        # 键盘（小矩形）
        kb_rect = QRectF(x - 10, y - 4, 20, 6)
        painter.setPen(QPen(QColor("#2a2a3a"), 1))
        painter.setBrush(QColor("#18182a"))
        painter.drawRoundedRect(kb_rect, 1, 1)

    def _draw_agent(self, painter: QPainter, slot: AgentSlot, w: int, h: int):
        """绘制 Agent 角色（emoji + 状态指示）。"""
        x = int(slot.desk_x * w + slot.offset_x * w)
        y = int(slot.desk_y * h + slot.offset_y * h)

        # 翻墙溜了则不绘制
        if slot.escaped:
            # 只显示一个离开的箭头
            painter.setPen(QPen(QColor("#ff6b6b"), 1))
            painter.setFont(QFont("Apple Color Emoji", 10))
            painter.drawText(x - 5, y + 25, "💨")
            return

        # 选中高亮光圈
        if slot.selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(slot.color))
            painter.setOpacity(0.15)
            painter.drawEllipse(QPointF(x, y + 18), 22, 22)
            painter.setOpacity(1.0)

        # Agent emoji
        painter.setFont(QFont("Apple Color Emoji", 20))
        emoji_rect = QRectF(x - 12, y + 8, 24, 24)

        # 工作中轻微上下抖动
        if slot.state == AgentState.WORKING:
            jitter = math.sin(slot.frame * 0.5) * 1.5
            emoji_rect.moveTop(emoji_rect.top() + jitter)

        # 完成状态弹跳
        if slot.state == AgentState.DONE:
            bounce = abs(math.sin(slot.frame * 0.3)) * 4
            emoji_rect.moveTop(emoji_rect.top() - bounce)

        # 错误状态左右摇晃
        if slot.state == AgentState.ERROR:
            shake = math.sin(slot.frame * 0.8) * 2
            emoji_rect.moveLeft(emoji_rect.left() + shake)

        painter.drawText(emoji_rect, Qt.AlignmentFlag.AlignCenter, slot.emoji)

        # 标题（小字）
        painter.setPen(QPen(QColor("#8e8e9a"), 1))
        painter.setFont(QFont("PingFang SC", 7))
        title_rect = QRectF(x - 20, y + 34, 40, 12)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, slot.title)

    def _draw_status_effect(self, painter: QPainter, slot: AgentSlot, w: int, h: int):
        """绘制状态效果（气泡、粒子等）。"""
        x = int(slot.desk_x * w + slot.offset_x * w)
        y = int(slot.desk_y * h + slot.offset_y * h)

        if slot.escaped:
            return

        painter.setFont(QFont("Apple Color Emoji", 10))

        if slot.state == AgentState.WORKING:
            # 敲键盘粒子（小方块弹出）
            for i in range(3):
                px = x - 8 + (slot.frame * 7 + i * 11) % 16
                py = y + 2 + abs(math.sin(slot.frame * 0.3 + i)) * 8
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(slot.color))
                painter.setOpacity(0.4)
                painter.drawEllipse(int(px), int(py), 3, 3)
            painter.setOpacity(1.0)

        elif slot.state == AgentState.DONE:
            # 竖大拇指
            painter.drawText(x + 14, y + 10, "👍")

        elif slot.state == AgentState.ERROR:
            # 红色感叹号
            painter.setPen(QPen(QColor("#ff6b6b"), 1))
            painter.drawText(x + 14, y + 10, "❗")

        elif slot.state == AgentState.IDLE:
            if slot.behavior == IdleBehavior.NAP:
                # ZZZ 气泡
                phase = (slot.frame // 3) % 3
                zzz = "Z" + "z" * phase
                painter.setPen(QPen(QColor("#6e6e73"), 1))
                painter.setFont(QFont("monospace", 8))
                painter.drawText(x + 12, y + 5, zzz)
            elif slot.behavior == IdleBehavior.COFFEE:
                painter.drawText(x + 14, y + 10, "☕")
            elif slot.behavior == IdleBehavior.PHONE:
                painter.drawText(x + 14, y + 10, "📱")
            elif slot.behavior == IdleBehavior.STARE:
                # 看窗外的省略号
                dots = "." * ((slot.frame // 5) % 4)
                painter.setPen(QPen(QColor("#5a5a66"), 1))
                painter.setFont(QFont("PingFang SC", 8))
                painter.drawText(x + 14, y + 10, dots)

        elif slot.state == AgentState.WAITING:
            # 等待中：咖啡杯 + 时钟
            painter.drawText(x + 14, y + 10, "⏳")

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
        """检测鼠标位置是否在某个 Agent 上。"""
        w, h = self.width(), self.height()
        for slot in self._slots:
            if slot.escaped:
                continue
            sx = slot.desk_x * w + slot.offset_x * w
            sy = slot.desk_y * h + slot.offset_y * h
            # 检测范围：以角色位置为中心的圆形区域
            dx = mx - sx
            dy = my - (sy + 18)
            if dx * dx + dy * dy < 25 * 25:
                return slot
        return None
