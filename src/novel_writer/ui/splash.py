"""启动画面 — 品牌渐变 + 进度条缓动动画。

纯 QPainter 绘制，MainWindow 各构造阶段回调 set_stage() 推进进度，
把「卡住不动」变成「看得见的进度」。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer, QRectF, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import (
    QPainter, QColor, QLinearGradient, QFont, QPixmap, QPen, QPainterPath,
)
from PySide6.QtWidgets import QWidget, QApplication

_BRAND = QColor("#2B579A")
_BRAND_LIGHT = QColor("#4A90D9")
_TEXT_MAIN = QColor("#1F2A44")
_TEXT_SUB = QColor("#6B7A99")
_BG_TOP = QColor("#FFFFFF")
_BG_BOTTOM = QColor("#E9F0FA")
_BAR_TRACK = QColor("#D5DFEE")

W, H = 480, 300

# 主窗口启动阶段表：(标题, 该阶段结束时的总进度 fraction)
STARTUP_STAGES = [
    ("初始化界面框架", 0.15),
    ("加载侧边栏", 0.35),
    ("加载编辑区", 0.55),
    ("加载智能体面板", 0.75),
    ("加载模型与配置", 0.90),
    ("就绪", 1.00),
]


class SplashScreen(QWidget):
    """品牌启动画面，带平滑进度条。

    用法：
        splash = SplashScreen()
        splash.show()
        QApplication.processEvents()
        # ... 构建 MainWindow 各阶段 ...
        splash.set_stage("加载侧边栏", 0.35)
        splash.finish(window)
    """

    def __init__(self):
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint)
        self.setFixedSize(W, H)

        self._stage_text = "启动中..."
        self._target_fraction = 0.0
        self._display_fraction = 0.0  # 显示值（缓动逼近目标）
        self._dot_phase = 0.0

        # 进度缓动：显示值滑行逼近目标
        self._ease_timer = QTimer(self)
        self._ease_timer.setInterval(16)  # ~60fps
        self._ease_timer.timeout.connect(self._tick_ease)
        self._ease_timer.start()

        # 动词色点缀脉冲
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(50)
        self._pulse_timer.timeout.connect(self._tick_pulse)
        self._pulse_timer.start()

        # 居中到主屏
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                (geo.width() - W) // 2 + geo.x(),
                (geo.height() - H) // 2 + geo.y(),
            )

    def set_stage(self, text: str, fraction: float):
        """设置当前阶段文案与目标进度（单调不降）。"""
        self._stage_text = text
        self._target_fraction = max(self._target_fraction, min(1.0, fraction))
        self.update()

    def finish(self, window):
        """窗口就绪后关闭启动画面。"""
        self._ease_timer.stop()
        self._pulse_timer.stop()
        window.show()
        self.close()
        self.deleteLater()

    def _tick_ease(self):
        """显示值向目标值滑行（缓动补真）。"""
        if self._display_fraction < self._target_fraction:
            gap = self._target_fraction - self._display_fraction
            self._display_fraction += max(gap * 0.15, 0.003)
            if self._display_fraction > self._target_fraction:
                self._display_fraction = self._target_fraction
            self.update()

    def _tick_pulse(self):
        self._dot_phase = (self._dot_phase + 0.08) % 3.14159
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 背景渐变
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, _BG_TOP)
        grad.setColorAt(1, _BG_BOTTOM)
        p.fillRect(0, 0, w, h, grad)

        # 顶部品牌条
        p.fillRect(0, 0, w, 4, _BRAND)

        # Logo 图标
        logo_path = self._find_logo()
        if logo_path:
            pix = QPixmap(str(logo_path))
            if not pix.isNull():
                p.drawPixmap(w // 2 - 32, 36, 64, 64, pix)

        # 标题
        p.setPen(_TEXT_MAIN)
        title_font = QFont("Microsoft YaHei" if sys.platform == "win32" else "PingFang SC", 20, QFont.Weight.Bold)
        p.setFont(title_font)
        p.drawText(QRectF(0, 110, w, 30), Qt.AlignmentFlag.AlignCenter, "Novel Writer")

        # 副标题
        p.setPen(_TEXT_SUB)
        sub_font = QFont("Microsoft YaHei" if sys.platform == "win32" else "PingFang SC", 10)
        p.setFont(sub_font)
        p.drawText(QRectF(0, 142, w, 20), Qt.AlignmentFlag.AlignCenter, "AI 小说创作助手")

        # 进度条
        bar_x, bar_y, bar_w, bar_h = 60, 210, w - 120, 8
        track = QPainterPath()
        track.addRoundedRect(bar_x, bar_y, bar_w, bar_h, 4, 4)
        p.fillPath(track, _BAR_TRACK)

        fill_w = int(bar_w * self._display_fraction)
        if fill_w > 0:
            fill = QPainterPath()
            fill.addRoundedRect(bar_x, bar_y, fill_w, bar_h, 4, 4)
            bar_grad = QLinearGradient(bar_x, 0, bar_x + bar_w, 0)
            bar_grad.setColorAt(0, _BRAND)
            bar_grad.setColorAt(1, _BRAND_LIGHT)
            p.fillPath(fill, bar_grad)

        # 流光点（进度条上的脉冲光点）
        if 0.02 < self._display_fraction < 0.98:
            import math
            glow_x = bar_x + fill_w
            glow_alpha = int(80 + 60 * math.sin(self._dot_phase * 2))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, glow_alpha))
            p.drawEllipse(glow_x - 3, bar_y - 1, 6, bar_h + 2)

        # 阶段文字 + 百分比
        p.setPen(_TEXT_SUB)
        stage_font = QFont("Microsoft YaHei" if sys.platform == "win32" else "PingFang SC", 9)
        p.setFont(stage_font)
        pct = int(self._display_fraction * 100)
        p.drawText(QRectF(60, bar_y + 14, bar_w - 60, 20), Qt.AlignmentFlag.AlignLeft, self._stage_text)
        p.drawText(QRectF(0, bar_y + 14, w - 60, 20), Qt.AlignmentFlag.AlignRight, f"{pct}%")

        p.end()

    @staticmethod
    def _find_logo():
        """查找 logo.png（支持开发环境和 PyInstaller 打包后）。"""
        candidates = [
            Path(__file__).resolve().parents[2] / "logo.png",  # dev
            Path(__file__).resolve().parents[1] / "logo.png",
            Path(sys._MEIPASS) / "logo.png" if hasattr(sys, "_MEIPASS") else None,
        ]
        for p in candidates:
            if p and p.exists():
                return p
        return None


# 需要 import Path
from pathlib import Path  # noqa: E402
