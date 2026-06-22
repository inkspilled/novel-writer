from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget,
    QLabel, QLineEdit, QComboBox, QPushButton, QFormLayout,
    QGroupBox, QMessageBox, QDoubleSpinBox, QListWidget,
    QListWidgetItem, QInputDialog, QTextEdit, QColorDialog,
    QScrollArea,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor

from .styles import THEMES, get_theme_colors
from ..locales import t, get_languages
from ..core.agents import load_agents, save_agents, reset_agents
from pathlib import Path
import json

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config"


def load_default_providers() -> list[dict]:
    """从 default_providers.json 加载默认模型供应商列表。"""
    path = _CONFIG_DIR / "default_providers.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


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


# ════════════════════════════════════════
#  外观设置对话框
# ════════════════════════════════════════

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


# ════════════════════════════════════════
#  模型设置对话框
# ════════════════════════════════════════

class ModelDialog(QDialog):
    """模型设置：供应商配置 + 已保存模型管理。"""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings_tab_model"))
        self.setMinimumSize(720, 600)
        self.config = dict(config)
        self._providers = load_default_providers()
        # 加载自定义供应商
        custom = self.config.get("custom_providers", [])
        if custom:
            self._providers.extend(custom)
        self._saved_models: dict = dict(self.config.get("saved_models", {}))
        self._setup_ui()
        self._load_config()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(14)

        # ── 顶部：默认模型选择 ──
        default_group = QGroupBox(t("model_default_group"))
        dg_layout = QHBoxLayout(default_group)
        dg_layout.setSpacing(10)

        dg_layout.addWidget(QLabel(t("model_default_label")))
        self._default_combo = QComboBox()
        self._default_combo.setMinimumWidth(260)
        self._default_combo.currentIndexChanged.connect(self._on_default_changed)
        dg_layout.addWidget(self._default_combo, 1)

        self._default_status = QLabel()
        self._default_status.setStyleSheet("color: gray; font-size: 12px;")
        dg_layout.addWidget(self._default_status)

        layout.addWidget(default_group)

        # ── 中部：左侧列表 + 右侧编辑 ──
        body = QHBoxLayout()
        body.setSpacing(16)

        # 左侧：已保存模型列表
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(QLabel(t("model_saved_models")))
        self.model_list = QListWidget()
        self.model_list.currentItemChanged.connect(self._on_model_selected)
        left_layout.addWidget(self.model_list)

        btn_row = QHBoxLayout()
        self._btn_del_model = QPushButton(t("model_delete_config"))
        self._btn_del_model.setObjectName("danger")
        self._btn_del_model.clicked.connect(self._delete_model_config)
        btn_row.addWidget(self._btn_del_model)
        left_layout.addLayout(btn_row)
        body.addWidget(left, 1)

        # 右侧：配置编辑
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(12)

        provider_group = QGroupBox(t("settings_provider_group"))
        pg = QFormLayout(provider_group)
        pg.setSpacing(10)

        # 供应商选择 + 新增按钮
        provider_row = QHBoxLayout()
        provider_row.setSpacing(8)
        self.provider_combo = QComboBox()
        for p in self._providers:
            self.provider_combo.addItem(p["name"])
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_row.addWidget(self.provider_combo, 1)
        self._btn_add_provider = QPushButton("➕ 新增")
        self._btn_add_provider.setFixedHeight(32)
        self._btn_add_provider.setFixedWidth(70)
        self._btn_add_provider.clicked.connect(self._add_custom_provider)
        provider_row.addWidget(self._btn_add_provider)
        pg.addRow(t("settings_provider"), provider_row)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText(t("settings_ph_api_key"))
        pg.addRow(t("settings_api_key"), self.api_key_input)

        self.base_url_input = QLineEdit()
        self.base_url_input.setPlaceholderText(t("settings_ph_base_url"))
        pg.addRow(t("settings_base_url"), self.base_url_input)

        self.model_input = QLineEdit()
        self.model_input.setPlaceholderText(t("settings_ph_model"))
        pg.addRow(t("settings_model_name"), self.model_input)

        test_btn = QPushButton(t("settings_test_conn"))
        test_btn.clicked.connect(self._test_connection)
        pg.addRow("", test_btn)
        scroll_layout.addWidget(provider_group)

        # Ollama
        self.ollama_group = QGroupBox(t("settings_ollama_group"))
        og = QVBoxLayout(self.ollama_group)
        self.ollama_status = QLabel(t("settings_ollama_detecting"))
        og.addWidget(self.ollama_status)
        self.ollama_model_list = QListWidget()
        self.ollama_model_list.setMaximumHeight(120)
        self.ollama_model_list.itemDoubleClicked.connect(self._on_ollama_model_selected)
        og.addWidget(self.ollama_model_list)
        self._refresh_btn = QPushButton(t("settings_ollama_refresh"))
        self._refresh_btn.clicked.connect(self._refresh_ollama_models)
        og.addWidget(self._refresh_btn)
        scroll_layout.addWidget(self.ollama_group)
        self.ollama_group.setVisible(False)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        right_layout.addWidget(scroll, 1)

        # 右侧底部按钮
        btn_row2 = QHBoxLayout()
        btn_row2.setSpacing(10)
        btn_row2.addStretch()
        self._btn_save_as = QPushButton(t("model_save_as"))
        self._btn_save_as.setFixedHeight(34)
        self._btn_save_as.clicked.connect(self._save_as_model_config)
        btn_row2.addWidget(self._btn_save_as)
        self._btn_set_default = QPushButton(t("model_set_default"))
        self._btn_set_default.setObjectName("primary")
        self._btn_set_default.setFixedHeight(34)
        self._btn_set_default.clicked.connect(self._set_as_default)
        btn_row2.addWidget(self._btn_set_default)
        right_layout.addLayout(btn_row2)

        body.addWidget(right, 2)
        layout.addLayout(body, 1)

        # ── 底部：关闭按钮 ──
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        self._btn_close = QPushButton("关闭")
        self._btn_close.setFixedHeight(36)
        self._btn_close.setFixedWidth(90)
        self._btn_close.clicked.connect(self._close_dialog)
        bottom_row.addWidget(self._btn_close)
        layout.addLayout(bottom_row)
        layout.addLayout(bottom_row)

        self._refresh_model_list()
        self._on_provider_changed(0)

    def _refresh_model_list(self):
        self.model_list.clear()
        for name in self._saved_models:
            self.model_list.addItem(name)
        self._refresh_default_combo()

    def _on_model_selected(self, current, _prev):
        if not current:
            return
        name = current.text()
        info = self._saved_models.get(name)
        if not info:
            return
        # 填充右侧字段
        provider_name = info.get("name", "")
        for i in range(self.provider_combo.count()):
            if self.provider_combo.itemText(i) == provider_name:
                self.provider_combo.setCurrentIndex(i)
                break
        self.api_key_input.setText(info.get("api_key", ""))
        self.base_url_input.setText(info.get("base_url", ""))
        self.model_input.setText(info.get("model", ""))

    def _save_as_model_config(self):
        """将当前右侧配置保存为命名模型（不关闭对话框）。"""
        model_name = self.model_input.text().strip()
        if not model_name:
            QMessageBox.warning(self, t("dialog_prompt"), t("msg_input_model"))
            return
        name, ok = QInputDialog.getText(self, t("model_save_as"), t("model_name_prompt"), text=model_name)
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._saved_models:
            if QMessageBox.question(self, t("dialog_prompt"),
                                    t("model_name_exists", name)) != QMessageBox.StandardButton.Yes:
                return
        provider_name = self.provider_combo.currentText()
        provider = next((p for p in self._providers if p["name"] == provider_name), None)
        if not provider:
            return
        self._saved_models[name] = {
            "name": provider_name,
            "type": provider["type"],
            "api_key": self.api_key_input.text().strip(),
            "base_url": self.base_url_input.text().strip(),
            "model": model_name,
        }
        self._refresh_model_list()

    def _set_as_default(self):
        """将当前右侧配置设为默认模型（不关闭对话框）。"""
        model_name = self.model_input.text().strip()
        if not model_name:
            QMessageBox.warning(self, t("dialog_prompt"), t("msg_input_model"))
            return
        provider_name = self.provider_combo.currentText()
        provider = next((p for p in self._providers if p["name"] == provider_name), None)
        if not provider:
            return
        self.config["current_provider"] = {
            "name": provider_name,
            "type": provider["type"],
            "api_key": self.api_key_input.text().strip(),
            "base_url": self.base_url_input.text().strip(),
            "model": model_name,
        }
        self._refresh_default_combo()
        self._default_status.setText(t("model_default_set"))
        QTimer.singleShot(2000, lambda: self._default_status.setText(""))

    def _refresh_default_combo(self):
        """刷新默认模型下拉框。"""
        self._default_combo.blockSignals(True)
        self._default_combo.clear()
        self._default_combo.addItem(t("model_select_default"), "")
        for name in self._saved_models:
            self._default_combo.addItem(name, name)
        # 选中当前默认
        current = self.config.get("current_provider", {})
        current_model = current.get("model", "")
        for i in range(self._default_combo.count()):
            if self._default_combo.itemData(i) == current_model:
                self._default_combo.setCurrentIndex(i)
                break
        self._default_combo.blockSignals(False)

    def _on_default_changed(self, index: int):
        """默认模型下拉框变更：自动填充右侧表单。"""
        key = self._default_combo.itemData(index) if index >= 0 else ""
        if not key:
            return
        info = self._saved_models.get(key)
        if not info:
            return
        provider_name = info.get("name", "")
        for i in range(self.provider_combo.count()):
            if self.provider_combo.itemText(i) == provider_name:
                self.provider_combo.setCurrentIndex(i)
                break
        self.api_key_input.setText(info.get("api_key", ""))
        self.base_url_input.setText(info.get("base_url", ""))
        self.model_input.setText(info.get("model", ""))

    def _delete_model_config(self):
        current = self.model_list.currentItem()
        if not current:
            return
        name = current.text()
        if QMessageBox.question(self, t("dialog_confirm"),
                                t("msg_delete_agent", name)) == QMessageBox.StandardButton.Yes:
            self._saved_models.pop(name, None)
            self._refresh_model_list()

    def _on_provider_changed(self, index: int):
        if index < 0:
            return
        # "自定义供应商" 选项
        if index == len(self._providers):
            self._add_custom_provider()
            return
        if index >= len(self._providers):
            return
        provider = self._providers[index]
        self.base_url_input.setText(provider["base_url"])
        self.model_input.clear()
        is_ollama = provider["type"] == "ollama"
        self.ollama_group.setVisible(is_ollama)
        if is_ollama:
            self._refresh_ollama_models()

    def _close_dialog(self):
        """关闭对话框，自动保存当前配置。"""
        self._save()

    def _add_custom_provider(self):
        """弹出对话框添加自定义供应商。"""
        dialog = QDialog(self)
        dialog.setWindowTitle("新增自定义供应商")
        dialog.setMinimumWidth(420)
        form = QFormLayout(dialog)
        form.setSpacing(12)
        form.setContentsMargins(20, 20, 20, 20)

        name_input = QLineEdit()
        name_input.setPlaceholderText("如：我的 API 服务")
        form.addRow("供应商名称", name_input)

        type_combo = QComboBox()
        type_combo.addItem("OpenAI 兼容", "openai_compat")
        type_combo.addItem("Ollama 本地", "ollama")
        form.addRow("接口类型", type_combo)

        url_input = QLineEdit()
        url_input.setPlaceholderText("https://api.example.com/v1")
        form.addRow("Base URL", url_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(dialog.reject)
        btn_ok = QPushButton("添加")
        btn_ok.setObjectName("primary")
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        form.addRow("", btn_row)

        def do_add():
            name = name_input.text().strip()
            url = url_input.text().strip()
            if not name:
                QMessageBox.warning(dialog, "提示", "请输入供应商名称")
                return
            if not url:
                QMessageBox.warning(dialog, "提示", "请输入 Base URL")
                return
            # 检查重名
            for p in self._providers:
                if p["name"] == name:
                    QMessageBox.warning(dialog, "提示", f"供应商「{name}」已存在")
                    return
            new_provider = {
                "name": name,
                "type": type_combo.currentData(),
                "base_url": url,
            }
            self._providers.append(new_provider)
            # 插入到"自定义供应商"之前
            self.provider_combo.blockSignals(True)
            insert_pos = self.provider_combo.count() - 1
            self.provider_combo.insertItem(insert_pos, name)
            self.provider_combo.setCurrentIndex(insert_pos)
            self.provider_combo.blockSignals(False)
            self.base_url_input.setText(url)
            self.model_input.clear()
            dialog.accept()

        btn_ok.clicked.connect(do_add)
        dialog.exec()

    def _on_ollama_model_selected(self, item: QListWidgetItem):
        name = item.text().split("  (")[0].strip()
        self.model_input.setText(name)

    def _refresh_ollama_models(self):
        import httpx
        self.ollama_model_list.clear()
        try:
            resp = httpx.get("http://localhost:11434/api/tags", timeout=3)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            if models:
                self.ollama_status.setText(t("test_connected_models", len(models)))
                for m in models:
                    size_gb = m.get("size", 0) / 1e9
                    self.ollama_model_list.addItem(f"{m['name']}  ({size_gb:.1f} GB)")
            else:
                self.ollama_status.setText(t("test_connected_no_models"))
        except Exception as e:
            self.ollama_status.setText(t("test_not_connected", str(e)))

    def _test_connection(self):
        provider_name = self.provider_combo.currentText()
        provider = next((p for p in self._providers if p["name"] == provider_name), None)
        if not provider:
            return
        api_key = self.api_key_input.text().strip()
        base_url = self.base_url_input.text().strip()
        model = self.model_input.text().strip()

        if not model:
            QMessageBox.warning(self, t("dialog_prompt"), t("msg_input_model"))
            return

        # Ollama 先检查模型是否存在
        if provider.get("type") == "ollama":
            import httpx
            try:
                tags = httpx.get(base_url + "/api/tags", timeout=5)
                tags.raise_for_status()
                available = [m["name"] for m in tags.json().get("models", [])]
                if model not in available:
                    QMessageBox.warning(self, t("dialog_fail"),
                                        t("msg_model_not_found", model, ", ".join(available)))
                    return
            except Exception as e:
                QMessageBox.warning(self, t("dialog_fail"), t("msg_conn_fail", str(e)))
                return
            base_url = base_url.rstrip("/") + "/v1"

        # 统一用 OpenAI 兼容接口测试
        try:
            from openai import OpenAI as _OpenAI
            client = _OpenAI(api_key=api_key or "ollama", base_url=base_url)
            resp = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": "hi"}], max_tokens=5)
            QMessageBox.information(self, t("dialog_success"), t("test_success_conn", resp.model))
        except Exception as e:
            QMessageBox.warning(self, t("dialog_fail"), str(e))

    def _load_config(self):
        saved_provider = self.config.get("current_provider")
        if saved_provider:
            for i in range(self.provider_combo.count()):
                if self.provider_combo.itemText(i) == saved_provider.get("name"):
                    self.provider_combo.setCurrentIndex(i)
                    break
            self.api_key_input.setText(saved_provider.get("api_key", ""))
            self.base_url_input.setText(saved_provider.get("base_url", ""))
            self.model_input.setText(saved_provider.get("model", ""))

    def _save(self):
        # 保存当前编辑的配置为默认模型
        provider_name = self.provider_combo.currentText()
        provider = next((p for p in self._providers if p["name"] == provider_name), None)
        if provider:
            self.config["current_provider"] = {
                "name": provider_name,
                "type": provider["type"],
                "api_key": self.api_key_input.text().strip(),
                "base_url": self.base_url_input.text().strip(),
                "model": self.model_input.text().strip(),
            }
        self.config["saved_models"] = self._saved_models
        # 保存自定义供应商（非默认列表的）
        default_names = {p["name"] for p in load_default_providers()}
        custom_providers = [p for p in self._providers if p["name"] not in default_names]
        if custom_providers:
            self.config["custom_providers"] = custom_providers
        self.accept()

    def get_config(self) -> dict:
        return self.config


# ════════════════════════════════════════
#  Agent 管理对话框
# ════════════════════════════════════════

class AgentDialog(QDialog):
    """Agent 管理：增删改查 Agent 配置。"""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings_tab_agent"))
        self.setMinimumSize(640, 500)
        self.config = dict(config)
        self._agents = load_agents()
        self._saved_models: dict = self.config.get("saved_models", {})
        self._setup_ui()
        self._load_config()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        body = QHBoxLayout()
        body.setSpacing(14)

        # 左侧列表
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        self._agent_list_label = QLabel(t("settings_agent_list"))
        left_layout.addWidget(self._agent_list_label)
        self.agent_list = QListWidget()
        self.agent_list.currentItemChanged.connect(self._on_agent_selected)
        left_layout.addWidget(self.agent_list)
        btn_row = QHBoxLayout()
        self._btn_add = QPushButton(t("settings_btn_add"))
        self._btn_add.clicked.connect(self._add_agent)
        self._btn_del = QPushButton(t("settings_btn_delete"))
        self._btn_del.setObjectName("danger")
        self._btn_del.clicked.connect(self._del_agent)
        self._btn_reset = QPushButton(t("settings_btn_reset"))
        self._btn_reset.clicked.connect(self._reset_agents)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_del)
        btn_row.addWidget(self._btn_reset)
        left_layout.addLayout(btn_row)
        body.addWidget(left, 1)

        # 右侧详情
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.agent_detail_group = QGroupBox(t("settings_agent_detail"))
        form = QFormLayout(self.agent_detail_group)
        form.setSpacing(10)

        self.agent_name_input = QLineEdit()
        self.agent_name_input.setPlaceholderText(t("settings_ph_agent_id"))
        form.addRow(t("settings_agent_id"), self.agent_name_input)

        self.agent_title_input = QLineEdit()
        self.agent_title_input.setPlaceholderText(t("settings_ph_agent_title"))
        form.addRow(t("settings_agent_title"), self.agent_title_input)

        self.agent_emoji_combo = QComboBox()
        self.agent_emoji_combo.setEditable(True)
        self._emoji_list = [
            "📋", "🗺️", "✍️", "🔍", "✅", "✨", "💡", "🎯",
            "📖", "🖊️", "🧠", "💬", "🎨", "📐", "🔮", "🎭",
            "📝", "🤖", "📚", "🌟", "⚡", "🔥", "💎", "🎪",
        ]
        self.agent_emoji_combo.addItems(self._emoji_list)
        self.agent_emoji_combo.setPlaceholderText(t("settings_ph_emoji"))
        form.addRow(t("settings_agent_emoji"), self.agent_emoji_combo)

        self.agent_model_combo = QComboBox()
        self.agent_model_combo.addItem(t("model_use_global"), "")
        for model_name in self._saved_models:
            self.agent_model_combo.addItem(model_name, model_name)
        form.addRow(t("settings_agent_model"), self.agent_model_combo)

        self.agent_temp_spin = QDoubleSpinBox()
        self.agent_temp_spin.setRange(0.0, 2.0)
        self.agent_temp_spin.setSingleStep(0.1)
        self.agent_temp_spin.setValue(0.7)
        form.addRow(t("settings_temperature"), self.agent_temp_spin)

        self.agent_skills_input = QLineEdit()
        self.agent_skills_input.setPlaceholderText(t("settings_ph_skills"))
        form.addRow(t("settings_agent_skills"), self.agent_skills_input)

        self.agent_prompt_input = QTextEdit()
        self.agent_prompt_input.setPlaceholderText(t("settings_ph_prompt"))
        self.agent_prompt_input.setMaximumHeight(100)
        form.addRow(t("settings_agent_prompt"), self.agent_prompt_input)

        right_layout.addWidget(self.agent_detail_group)
        right_layout.addStretch()
        body.addWidget(right, 2)

        layout.addLayout(body)

        btn_row2 = QHBoxLayout()
        btn_row2.addStretch()
        self._btn_save = QPushButton(t("settings_save_all"))
        self._btn_save.setObjectName("primary")
        self._btn_save.clicked.connect(self._save)
        self._btn_cancel = QPushButton(t("settings_cancel"))
        self._btn_cancel.clicked.connect(self.reject)
        btn_row2.addWidget(self._btn_save)
        btn_row2.addWidget(self._btn_cancel)
        layout.addLayout(btn_row2)

    def _refresh_agent_list(self):
        self.agent_list.clear()
        for name, info in self._agents.items():
            emoji = info.get("emoji", "🤖")
            title = info.get("title", name)
            self.agent_list.addItem(f"{emoji} {title}")

    def _on_agent_selected(self, current, _prev):
        if not current:
            return
        row = self.agent_list.currentRow()
        names = list(self._agents.keys())
        if row >= len(names):
            return
        name = names[row]
        info = self._agents[name]
        self.agent_name_input.setText(name)
        self.agent_name_input.setEnabled(True)
        self.agent_title_input.setText(info.get("title", ""))
        emoji = info.get("emoji", "🤖")
        idx = self.agent_emoji_combo.findText(emoji)
        if idx >= 0:
            self.agent_emoji_combo.setCurrentIndex(idx)
        else:
            self.agent_emoji_combo.setEditText(emoji)
        model_val = info.get("model", "")
        idx = self.agent_model_combo.findData(model_val)
        self.agent_model_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.agent_temp_spin.setValue(info.get("temperature", 0.7))
        self.agent_skills_input.setText(", ".join(info.get("skills", [])))
        self.agent_prompt_input.setPlainText(info.get("system_prompt", ""))

    def _save_current_agent(self):
        name = self.agent_name_input.text().strip()
        if not name:
            return
        if name not in self._agents:
            self._agents[name] = {}
        self._agents[name]["title"] = self.agent_title_input.text().strip()
        self._agents[name]["emoji"] = self.agent_emoji_combo.currentText().strip() or "🤖"
        self._agents[name]["temperature"] = self.agent_temp_spin.value()
        self._agents[name]["skills"] = [s.strip() for s in self.agent_skills_input.text().split(",") if s.strip()]
        self._agents[name]["system_prompt"] = self.agent_prompt_input.toPlainText().strip()
        self._agents[name]["model"] = self.agent_model_combo.currentData() or ""

    def _add_agent(self):
        name, ok = QInputDialog.getText(self, t("settings_btn_add"), t("settings_ph_agent_id"))
        if ok and name:
            name = name.strip().lower().replace(" ", "_")
            if name in self._agents:
                QMessageBox.warning(self, t("dialog_prompt"), t("msg_agent_exists", name))
                return
            self._agents[name] = {"title": name, "emoji": "🤖", "skills": [], "temperature": 0.7, "system_prompt": ""}
            self._refresh_agent_list()
            self.agent_list.setCurrentRow(self.agent_list.count() - 1)

    def _del_agent(self):
        row = self.agent_list.currentRow()
        if row < 0:
            return
        names = list(self._agents.keys())
        if row >= len(names):
            return
        name = names[row]
        if QMessageBox.question(self, t("dialog_confirm"),
                                t("msg_delete_agent", name)) == QMessageBox.StandardButton.Yes:
            del self._agents[name]
            self._refresh_agent_list()

    def _reset_agents(self):
        if QMessageBox.question(self, t("dialog_confirm"),
                                t("msg_reset_agents")) == QMessageBox.StandardButton.Yes:
            self._agents = reset_agents()
            self._refresh_agent_list()
            if self.agent_list.count() > 0:
                self.agent_list.setCurrentRow(0)

    def _load_config(self):
        self._refresh_agent_list()

    def _save(self):
        self._save_current_agent()
        save_agents(self._agents)
        self.accept()

    def get_config(self) -> dict:
        return self.config


# ── 保留 SettingsDialog 作为兼容别名 ──
SettingsDialog = AppearanceDialog
