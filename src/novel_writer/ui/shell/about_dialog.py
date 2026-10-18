"""关于对话框 — 应用信息 + 数据目录入口。"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)

from ...core.app_config import DATA_DIR, LOGO_PATH
from ...locales import t


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("menu_about_app"))
        self.setFixedWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(10)

        if LOGO_PATH.exists():
            logo = QLabel()
            pix = QPixmap(str(LOGO_PATH)).scaled(
                72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            logo.setPixmap(pix)
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(logo)

        title = QLabel("Novel Writer")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        layout.addWidget(title)

        version = QLabel(t("about_version"))
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setStyleSheet("font-size: 12px; color: gray;")
        layout.addWidget(version)

        desc = QLabel(t("about_desc"))
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setStyleSheet("font-size: 13px;")
        layout.addWidget(desc)

        tech = QLabel(
            f"PySide6 · Python {sys.version_info.major}.{sys.version_info.minor}"
            f".{sys.version_info.micro}\n{DATA_DIR}"
        )
        tech.setWordWrap(True)
        tech.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tech.setStyleSheet("font-size: 11px; color: gray;")
        layout.addWidget(tech)

        btns = QHBoxLayout()
        btns.addStretch()
        open_dir = QPushButton(t("about_open_data"))
        open_dir.clicked.connect(self._open_data_dir)
        btns.addWidget(open_dir)
        close = QPushButton(t("settings_close"))
        close.setObjectName("primary")
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        layout.addLayout(btns)

    def _open_data_dir(self):
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA_DIR)))
        except Exception:
            pass
