"""全局交互小件 — 学 smt-intelligent：下拉/数值框禁滚轮等。"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject

from ...core.logger import get_logger

logger = get_logger(__name__)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QSpinBox,
)


class _NoWheelFilter(QObject):
    """全局事件过滤器：吞掉 QComboBox / QSpinBox 的鼠标滚轮事件。

    滚轮悬停在下拉框/数值框上会误滚改动当前值（用户误操作重灾区）。
    下拉列表弹开时的滚轮仍可用：事件目标是 QListView，不在此拦截。
    """

    _WHEEL_TYPES = (QComboBox, QSpinBox, QDoubleSpinBox)

    def eventFilter(self, obj, event):
        # shiboken 偶发把 paint 类事件包成 QPainter，不是 QEvent 就放行
        if not isinstance(event, QEvent):
            return False
        if event.type() == QEvent.Type.Wheel and isinstance(obj, self._WHEEL_TYPES):
            return True  # 拦截，不改变当前值
        return False


def install_no_wheel_combo_filter(app: QApplication) -> None:
    """给整个应用装上「下拉框/数值框不响应滚轮」（幂等）。"""
    if getattr(app, "_no_wheel_combo_filter", None) is None:
        app._no_wheel_combo_filter = _NoWheelFilter(app)
        app.installEventFilter(app._no_wheel_combo_filter)
        logger.info("已安装全局滚轮拦截（下拉/数值框禁滚轮）")
