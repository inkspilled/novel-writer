from __future__ import annotations

import math

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer, Property, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QColor, QPen


class AgentIndicator(QWidget):
    """Agent 工作状态动画指示器 - 呼吸灯 + 旋转环效果。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 60)
        self._opacity = 0.3
        self._angle = 0
        self._active = False
        self._color = QColor("#89b4fa")

        # 呼吸灯动画
        self._breath_anim = QPropertyAnimation(self, b"opacity")
        self._breath_anim.setDuration(1500)
        self._breath_anim.setStartValue(0.3)
        self._breath_anim.setEndValue(1.0)
        self._breath_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._breath_anim.setLoopCount(-1)

        # 旋转动画
        self._rotate_timer = QTimer(self)
        self._rotate_timer.timeout.connect(self._rotate_step)

    def get_opacity(self):
        return self._opacity

    def set_opacity(self, val):
        self._opacity = val
        self.update()

    opacity = Property(float, get_opacity, set_opacity)

    def start(self, color: str = "#89b4fa"):
        self._color = QColor(color)
        self._active = True
        self._breath_anim.start()
        self._rotate_timer.start(16)  # ~60fps
        self.update()

    def stop(self):
        self._active = False
        self._breath_anim.stop()
        self._rotate_timer.stop()
        self._opacity = 0.3
        self.update()

    def _rotate_step(self):
        self._angle = (self._angle + 3) % 360
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self.rect().center()
        r = 25

        if self._active:
            # 外圈旋转弧线
            pen = QPen(self._color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setOpacity(self._opacity * 0.6)
            painter.drawArc(center.x() - r, center.y() - r, r * 2, r * 2,
                            self._angle * 16, 120 * 16)
            painter.drawArc(center.x() - r, center.y() - r, r * 2, r * 2,
                            (self._angle + 180) * 16, 120 * 16)

            # 内圈呼吸灯
            inner_r = 15
            painter.setOpacity(self._opacity)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._color)
            painter.drawEllipse(center, int(inner_r), int(inner_r))
        else:
            # 静止状态 - 灰色小圆
            painter.setOpacity(0.3)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#45475a"))
            painter.drawEllipse(center, 8, 8)

        painter.end()


class AgentBubble(QWidget):
    """Agent 头像气泡 - 带缩放弹跳效果。"""

    def __init__(self, emoji: str = "🤖", parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self._emoji = emoji
        self._scale = 1.0

        self._bounce_anim = QPropertyAnimation(self, b"scale")
        self._bounce_anim.setDuration(300)
        self._bounce_anim.setStartValue(0.8)
        self._bounce_anim.setEndValue(1.0)
        self._bounce_anim.setEasingCurve(QEasingCurve.Type.OutBack)

    def get_scale(self):
        return self._scale

    def set_scale(self, val):
        self._scale = val
        self.update()

    scale = Property(float, get_scale, set_scale)

    def bounce(self):
        self._bounce_anim.start()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self.rect().center()

        # 背景圆
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#313244"))
        r = int(18 * self._scale)
        painter.drawEllipse(center, r, r)

        # Emoji
        painter.setPen(QColor("#cdd6f4"))
        font = painter.font()
        font.setPixelSize(int(22 * self._scale))
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._emoji)
        painter.end()
