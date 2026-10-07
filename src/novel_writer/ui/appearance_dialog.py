"""外观设置对话框 — 主题、语言、自定义配色。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget,
    QLabel, QComboBox, QPushButton, QGroupBox,
    QColorDialog, QLineEdit, QScrollArea,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from .styles import THEMES, get_theme_colors
from ..locales import t, get_languages


class ColorPicker(QWidget):
    """色盘选择器：色块 + 十六进制输入 + 点击弹出颜色对话框。"""
    color_changed = Signal(str)

    def __init__(self, label: str, default_color: str = "#1a1a2e", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.label = QLabel(label)
        self.label.setFixedWidth(60)
        layout.addWidget(self.label)

        self.color_btn = QPushButton(t("color_pick"))
        self.color_btn.setFixedSize(100, 44)
        self.color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.color_btn.clicked.connect(self._pick_color)
        layout.addWidget(self.color_btn)

        self.hex_input = QLineEdit(default_color)
        self.hex_input.setFixedWidth(130)
        self.hex_input.setFixedHeight(44)
        self.hex_input.setPlaceholderText("#rrggbb")
        self.hex_input.setStyleSheet("QLineEdit { padding: 4px 10px; font-size: 15px; border-radius: 8px; }")
        self.hex_input.textChanged.connect(self._on_hex_changed)
        layout.addWidget(self.hex_input)

        layout.addStretch()
        self._color = default_color
        self._update_btn_style(default_color)

    def _update_btn_style(self, color: str):
        self.color_btn.setStyleSheet(
            f"QPushButton {{ background-color: {color}; color: {'#000' if self._is_light(color) else '#fff'};"
            f"border-radius: 6px; border: 2px solid rgba(128,128,128,0.4); font-size: 12px; }}"
            f"QPushButton:hover {{ border-color: rgba(255,255,255,0.6); }}")

    @staticmethod
    def _is_light(hex_color: str) -> bool:
        h = hex_color.lstrip("#")
        if len(h) != 6:
            return False
        try:
            r, g, b = int(h[:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except ValueError:
            return False
        return (r * 299 + g * 587 + b * 114) / 1000 > 128

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(self._color), self, t("color_pick"))
        if color.isValid():
            self.set_color(color.name())

    def _on_hex_changed(self, text: str):
        text = text.strip()
        if len(text) == 7 and text.startswith("#"):
            self._color = text
            self._update_btn_style(text)
            self.color_changed.emit(text)

    def set_color(self, color: str):
        self._color = color
        self.hex_input.blockSignals(True)
        self.hex_input.setText(color)
        self.hex_input.blockSignals(False)
        self._update_btn_style(color)

    def get_color(self) -> str:
        return self._color


class AppearanceDialog(QDialog):
    """外观设置：主题、语言。"""

    theme_preview = Signal(str, dict)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings_tab_appearance"))
        self.resize(560, 640)
        self.config = dict(config)
        self._setup_ui()
        self._load_config()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # 滚动区域包裹所有内容
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 语言
        lang_group = QGroupBox(t("settings_language").rstrip(":"))
        lg = QHBoxLayout(lang_group)
        lg.addWidget(QLabel(t("settings_language")))
        self.lang_combo = QComboBox()
        for code, label in get_languages():
            self.lang_combo.addItem(label, code)
        lg.addWidget(self.lang_combo, 1)
        layout.addWidget(lang_group)

        # 主题
        theme_group = QGroupBox(t("settings_theme_group"))
        tg = QVBoxLayout(theme_group)
        tg.setSpacing(12)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel(t("settings_select_theme")))
        self.theme_combo = QComboBox()
        for key, (label, _) in THEMES.items():
            self.theme_combo.addItem(label, key)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        row1.addWidget(self.theme_combo, 1)
        tg.addLayout(row1)

        self.theme_preview_label = QLabel()
        self.theme_preview_label.setFixedHeight(44)
        self.theme_preview_label.setStyleSheet("border-radius: 10px;")
        tg.addWidget(self.theme_preview_label)

        # 自定义配色
        self.custom_group = QGroupBox(t("settings_custom_colors"))
        cg = QVBoxLayout(self.custom_group)
        cg.setSpacing(6)
        self.color_bg = ColorPicker(t("color_bg"), "#1a1a2e")
        self.color_bg.color_changed.connect(self._on_custom_color_changed)
        cg.addWidget(self.color_bg)
        self.color_surface = ColorPicker(t("color_surface"), "#16213e")
        self.color_surface.color_changed.connect(self._on_custom_color_changed)
        cg.addWidget(self.color_surface)
        self.color_fg = ColorPicker(t("color_fg"), "#e0e0e0")
        self.color_fg.color_changed.connect(self._on_custom_color_changed)
        cg.addWidget(self.color_fg)
        self.color_fg2 = ColorPicker(t("color_fg2"), "#a0a0a0")
        self.color_fg2.color_changed.connect(self._on_custom_color_changed)
        cg.addWidget(self.color_fg2)
        self.color_accent = ColorPicker(t("color_accent"), "#e94560")
        self.color_accent.color_changed.connect(self._on_custom_color_changed)
        cg.addWidget(self.color_accent)
        tg.addWidget(self.custom_group)
        self.custom_group.setVisible(False)

        self._apply_btn = QPushButton(t("settings_apply_now"))
        self._apply_btn.setObjectName("primary")
        self._apply_btn.setFixedHeight(40)
        self._apply_btn.clicked.connect(self._apply_theme_now)
        tg.addWidget(self._apply_btn)
        layout.addWidget(theme_group)

        layout.addStretch()

        scroll.setWidget(container)
        root.addWidget(scroll)

        # 底部按钮（固定在底部，不随滚动）
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(24, 12, 24, 16)
        btn_row.addStretch()
        self._btn_save = QPushButton(t("settings_save_all"))
        self._btn_save.setObjectName("primary")
        self._btn_save.setFixedHeight(38)
        self._btn_save.clicked.connect(self._save)
        self._btn_cancel = QPushButton(t("settings_cancel"))
        self._btn_cancel.setFixedHeight(38)
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_save)
        btn_row.addWidget(self._btn_cancel)
        root.addLayout(btn_row)

    def _on_theme_changed(self):
        idx = self.theme_combo.currentIndex()
        key = self.theme_combo.itemData(idx)
        is_custom = (key == "custom")
        self.custom_group.setVisible(is_custom)
        colors = get_theme_colors("custom", self.config) if is_custom else get_theme_colors(key or "dark")
        self.theme_preview_label.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 {colors['bg']}, stop:0.25 {colors['surface']}, stop:0.5 {colors['card']}, "
            f"stop:0.75 {colors['accent']}, stop:1 {colors['elevated']}); "
            f"border-radius: 10px;")

    def _on_custom_color_changed(self):
        self.config["custom_bg"] = self.color_bg.get_color()
        self.config["custom_surface"] = self.color_surface.get_color()
        self.config["custom_fg"] = self.color_fg.get_color()
        self.config["custom_fg2"] = self.color_fg2.get_color()
        self.config["custom_accent"] = self.color_accent.get_color()
        self._on_theme_changed()

    def _apply_theme_now(self):
        key = self.theme_combo.itemData(self.theme_combo.currentIndex())
        self.config["theme"] = key or "dark"
        self.config["language"] = self.lang_combo.itemData(self.lang_combo.currentIndex()) or "zh"
        if key == "custom":
            self._on_custom_color_changed()
        self.theme_preview.emit(self.config["theme"], self.config)

    def _load_config(self):
        # 阻塞信号，防止设置 combo 值时触发 _on_theme_changed 导致重复渲染
        self.theme_combo.blockSignals(True)
        self.lang_combo.blockSignals(True)

        saved_lang = self.config.get("language", "zh")
        for i in range(self.lang_combo.count()):
            if self.lang_combo.itemData(i) == saved_lang:
                self.lang_combo.setCurrentIndex(i)
                break
        saved_theme = self.config.get("theme", "dark")
        for i in range(self.theme_combo.count()):
            if self.theme_combo.itemData(i) == saved_theme:
                self.theme_combo.setCurrentIndex(i)
                break
        self.color_bg.set_color(self.config.get("custom_bg", "#1a1a2e"))
        self.color_surface.set_color(self.config.get("custom_surface", "#16213e"))
        self.color_fg.set_color(self.config.get("custom_fg", "#e0e0e0"))
        self.color_fg2.set_color(self.config.get("custom_fg2", "#a0a0a0"))
        self.color_accent.set_color(self.config.get("custom_accent", "#e94560"))

        self.theme_combo.blockSignals(False)
        self.lang_combo.blockSignals(False)

        # 初始化预览（仅一次）
        self._on_theme_changed()

    def _save(self):
        self.config["theme"] = self.theme_combo.itemData(self.theme_combo.currentIndex()) or "dark"
        self.config["language"] = self.lang_combo.itemData(self.lang_combo.currentIndex()) or "zh"
        self.config["custom_bg"] = self.color_bg.get_color()
        self.config["custom_surface"] = self.color_surface.get_color()
        self.config["custom_fg"] = self.color_fg.get_color()
        self.config["custom_fg2"] = self.color_fg2.get_color()
        self.config["custom_accent"] = self.color_accent.get_color()
        self.accept()

    def get_config(self) -> dict:
        return self.config


# ── 兼容别名 ──
SettingsDialog = AppearanceDialog
