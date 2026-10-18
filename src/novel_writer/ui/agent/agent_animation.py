"""Agent 工作状态指示器 — 旋转弧 + 呼吸光，全部走淡入淡出。"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class AgentIndicator(QWidget):
    """状态指示器：WORKING 时旋转 + 呼吸；启动/停止用 300ms 淡入淡出，不闪烁。"""

    _FADE_IN = 0.32
    _FADE_OUT = 0.26
    _ROTATE_SPEED = 150.0   # deg/s
    _BREATH_PERIOD = 3.2    # s

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("agentIndicator")
        self.setFixedSize(32, 32)
        # 全局 QWidget QSS 会填背景；指示器必须透明，只画弧/圆/光晕
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self._active = False
        self._fade = 0.0
        self._phase = 0.0
        self._angle = 0.0
        self._last = time.monotonic()
        self._color = QColor("#89b4fa")
        self._inactive_color = QColor("#45475a")
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    # ── 外部 API ──

    def start(self, color: str = "#89b4fa"):
        self._color = QColor(color)
        self._active = True
        self._last = time.monotonic()
        if not self._timer.isActive():
            self._timer.start(16)
        self.update()

    def stop(self):
        if not self._active and self._fade <= 0.001:
            return
        self._active = False
        self._last = time.monotonic()
        if not self._timer.isActive():
            self._timer.start(16)
        self.update()

    def update_theme(self, colors: dict):
        self._inactive_color = QColor(colors.get("fg3", "#45475a"))
        if not self._active and self._fade <= 0.001:
            self.update()

    # ── 动画 ──

    def _tick(self):
        now = time.monotonic()
        dt = min(0.05, max(0.0, now - self._last))
        self._last = now
        self._angle = (self._angle + self._ROTATE_SPEED * dt) % 360.0
        self._phase = (self._phase + dt * 2 * math.pi / self._BREATH_PERIOD) % (2 * math.pi)

        target = 1.0 if self._active else 0.0
        if self._fade < target:
            self._fade = min(target, self._fade + dt / self._FADE_IN)
        elif self._fade > target:
            self._fade = max(target, self._fade - dt / self._FADE_OUT)

        if not self._active and self._fade <= 0.001:
            self._fade = 0.0
            self._timer.stop()
        self.update()

    # ── 绘制 ──

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self.rect().center()
        r = max(4, min(self.width(), self.height()) // 2 - 2)
        visible = self._active or self._fade > 0.001

        if visible:
            # 连续呼吸：0.62 → 1.0，不会从 1 跳回 0.3
            breath = 0.62 + 0.38 * (0.5 - 0.5 * math.cos(self._phase))
            alpha = self._fade * breath

            # 外发光：低透明度大圆，做柔和过渡
            glow = QColor(self._color)
            glow.setAlphaF(0.16 * alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            glow_r = max(6, int(r * 1.55))
            painter.drawEllipse(center, glow_r, glow_r)

            # 双旋转弧
            pen = QPen(self._color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setOpacity(alpha * 0.72)
            painter.drawArc(center.x() - r, center.y() - r, r * 2, r * 2,
                            int(self._angle * 16), 110 * 16)
            painter.drawArc(center.x() - r, center.y() - r, r * 2, r * 2,
                            int((self._angle + 180) * 16), 110 * 16)

            # 内圈呼吸灯
            inner_r = max(2, int(r * 0.56))
            painter.setOpacity(alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._color)
            painter.drawEllipse(center, inner_r, inner_r)
        else:
            # 静止灰色小圆
            painter.setOpacity(0.32)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._inactive_color)
            painter.drawEllipse(center, max(3, int(r * 0.42)), max(3, int(r * 0.42)))

        painter.end()
