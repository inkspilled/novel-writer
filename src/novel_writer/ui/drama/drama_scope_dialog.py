"""短剧一键生成的范围选择：当前章节/当前集 vs 全部。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QHBoxLayout, QLabel, QPushButton, QRadioButton,
    QVBoxLayout,
)


class DramaScopeDialog(QDialog):
    """选择一键生成范围；全部会逐章/逐集调用模型，可中途停止。"""

    def __init__(
        self, title: str, current_label: str, all_label: str, parent=None,
        hint: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        label = QLabel("选择生成范围")
        label.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(label)

        self.radio_current = QRadioButton(current_label)
        self.radio_current.setChecked(True)
        self.radio_all = QRadioButton(all_label)
        group = QButtonGroup(self)
        group.addButton(self.radio_current)
        group.addButton(self.radio_all)
        layout.addWidget(self.radio_current)
        layout.addWidget(self.radio_all)

        hint_text = hint or (
            "「全部」会逐章/逐集调用当前模型，耗时和 token 会明显增加；"
            "运行中可以在右侧面板点「停止」。\n"
            "生成结果会覆盖同来源的旧剧本/分镜。"
        )
        hint_label = QLabel(hint_text)
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("font-size: 11px; color: palette(placeholderText);")
        layout.addWidget(hint_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = QPushButton("开始生成")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def scope(self) -> str:
        return "current" if self.radio_current.isChecked() else "all"
