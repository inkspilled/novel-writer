from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget,
    QLabel, QLineEdit, QComboBox, QPushButton, QFormLayout,
    QGroupBox, QMessageBox, QDoubleSpinBox, QListWidget,
    QListWidgetItem, QInputDialog, QTextEdit, QColorDialog,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from .styles import THEMES, get_theme_colors
from ..locales import t, get_languages


DEFAULT_PROVIDERS = [
    {"name": "DeepSeek", "type": "openai_compat", "base_url": "https://api.deepseek.com/v1"},
    {"name": "Moonshot (Kimi)", "type": "openai_compat", "base_url": "https://api.moonshot.cn/v1"},
    {"name": "智谱 GLM", "type": "openai_compat", "base_url": "https://open.bigmodel.cn/api/paas/v4"},
    {"name": "通义千问", "type": "openai_compat", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    {"name": "OpenAI", "type": "openai_compat", "base_url": "https://api.openai.com/v1"},
    {"name": "Claude", "type": "claude", "base_url": ""},
    {"name": "Ollama (本地)", "type": "ollama", "base_url": "http://localhost:11434"},
]

BUILTIN_AGENTS = {
    "editor": {"emoji": "📋", "title": "主编", "skills": ["选题分析", "立意规划", "风格定位", "市场建议", "创作指导"]},
    "planner": {"emoji": "🗺️", "title": "策划", "skills": ["故事结构", "章节规划", "人物设定", "世界观构建", "伏笔设计"]},
    "writer": {"emoji": "✍️", "title": "写手", "skills": ["正文写作", "对话创作", "场景描写", "人物刻画", "剧情推进"]},
    "proofreader": {"emoji": "🔍", "title": "校对", "skills": ["错别字检查", "语法校对", "标点规范", "一致性检查", "格式规范"]},
    "reviewer": {"emoji": "✅", "title": "审核", "skills": ["剧情审查", "人物一致性", "节奏评估", "伏笔检查", "主题审核"]},
    "polisher": {"emoji": "✨", "title": "润色", "skills": ["文笔润色", "场景强化", "对话优化", "情感渲染", "修辞提升"]},
}


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
        self.hex_input.returnPressed.connect(self._apply_hex_input)
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

    def _apply_hex_input(self):
        text = self.hex_input.text().strip()
        if len(text) == 7 and text.startswith("#"):
            self.set_color(text)
            self.color_changed.emit(text)

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
        self.setMinimumSize(520, 560)
        self.config = dict(config)
        self._setup_ui()
        self._load_config()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

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
        tg.setSpacing(10)

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

        self.custom_group = QGroupBox(t("settings_custom_colors"))
        cg = QFormLayout(self.custom_group)
        cg.setSpacing(10)
        self.color_bg = ColorPicker(t("color_bg"), "#1a1a2e")
        self.color_bg.color_changed.connect(self._on_custom_color_changed)
        cg.addRow(self.color_bg)
        self.color_surface = ColorPicker(t("color_surface"), "#16213e")
        self.color_surface.color_changed.connect(self._on_custom_color_changed)
        cg.addRow(self.color_surface)
        self.color_fg = ColorPicker(t("color_fg"), "#e0e0e0")
        self.color_fg.color_changed.connect(self._on_custom_color_changed)
        cg.addRow(self.color_fg)
        self.color_fg2 = ColorPicker(t("color_fg2"), "#a0a0a0")
        self.color_fg2.color_changed.connect(self._on_custom_color_changed)
        cg.addRow(self.color_fg2)
        self.color_accent = ColorPicker(t("color_accent"), "#e94560")
        self.color_accent.color_changed.connect(self._on_custom_color_changed)
        cg.addRow(self.color_accent)
        tg.addWidget(self.custom_group)
        self.custom_group.setVisible(False)

        self._apply_btn = QPushButton(t("settings_apply_now"))
        self._apply_btn.setObjectName("primary")
        self._apply_btn.clicked.connect(self._apply_theme_now)
        tg.addWidget(self._apply_btn)
        layout.addWidget(theme_group)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_save = QPushButton(t("settings_save_all"))
        self._btn_save.setObjectName("primary")
        self._btn_save.clicked.connect(self._save)
        self._btn_cancel = QPushButton(t("settings_cancel"))
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_save)
        btn_row.addWidget(self._btn_cancel)
        layout.addLayout(btn_row)

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
    """模型设置：供应商、API Key、模型选择。"""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings_tab_model"))
        self.setMinimumSize(500, 460)
        self.config = dict(config)
        self._setup_ui()
        self._load_config()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        provider_group = QGroupBox(t("settings_provider_group"))
        pg = QFormLayout(provider_group)
        pg.setSpacing(10)

        self.provider_combo = QComboBox()
        for p in DEFAULT_PROVIDERS:
            self.provider_combo.addItem(p["name"])
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        pg.addRow(t("settings_provider"), self.provider_combo)

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
        layout.addWidget(provider_group)

        # Ollama
        self.ollama_group = QGroupBox(t("settings_ollama_group"))
        og = QVBoxLayout(self.ollama_group)
        self.ollama_status = QLabel(t("settings_ollama_detecting"))
        og.addWidget(self.ollama_status)
        self.ollama_model_list = QListWidget()
        self.ollama_model_list.setMaximumHeight(100)
        self.ollama_model_list.itemDoubleClicked.connect(self._on_ollama_model_selected)
        og.addWidget(self.ollama_model_list)
        self._refresh_btn = QPushButton(t("settings_ollama_refresh"))
        self._refresh_btn.clicked.connect(self._refresh_ollama_models)
        og.addWidget(self._refresh_btn)
        layout.addWidget(self.ollama_group)
        self.ollama_group.setVisible(False)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_save = QPushButton(t("settings_save_all"))
        self._btn_save.setObjectName("primary")
        self._btn_save.clicked.connect(self._save)
        self._btn_cancel = QPushButton(t("settings_cancel"))
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_save)
        btn_row.addWidget(self._btn_cancel)
        layout.addLayout(btn_row)

        self._on_provider_changed(0)

    def _on_provider_changed(self, index: int):
        if index < 0 or index >= len(DEFAULT_PROVIDERS):
            return
        provider = DEFAULT_PROVIDERS[index]
        self.base_url_input.setText(provider["base_url"])
        is_ollama = provider["type"] == "ollama"
        self.ollama_group.setVisible(is_ollama)
        if is_ollama:
            self._refresh_ollama_models()

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
        provider = next((p for p in DEFAULT_PROVIDERS if p["name"] == provider_name), None)
        if not provider:
            return
        ptype = provider["type"]
        api_key = self.api_key_input.text().strip()
        base_url = self.base_url_input.text().strip()
        model = self.model_input.text().strip()

        if not model:
            QMessageBox.warning(self, t("dialog_prompt"), t("msg_input_model"))
            return

        if ptype == "ollama":
            import httpx
            try:
                tags = httpx.get(base_url + "/api/tags", timeout=5)
                tags.raise_for_status()
                available = [m["name"] for m in tags.json().get("models", [])]
                if model not in available:
                    QMessageBox.warning(self, t("dialog_fail"),
                                        t("msg_model_not_found", model, ", ".join(available)))
                    return
                resp = httpx.post(base_url + "/api/chat", json={
                    "model": model,
                    "messages": [{"role": "user", "content": "你好"}],
                    "stream": False,
                    "options": {"num_predict": 30},
                }, timeout=300)
                resp.raise_for_status()
                msg = resp.json().get("message", {})
                reply = (msg.get("content", "") or msg.get("thinking", ""))[:150]
                QMessageBox.information(self, t("dialog_success"), t("test_success_model", model, reply))
            except httpx.ConnectError:
                QMessageBox.warning(self, t("dialog_fail"), t("msg_conn_fail", base_url))
            except httpx.ReadTimeout:
                QMessageBox.warning(self, t("dialog_fail"), t("msg_timeout", model))
            except httpx.HTTPStatusError as e:
                QMessageBox.warning(self, t("dialog_error"), t("msg_http_error", str(e.response.status_code)))
            except Exception as e:
                QMessageBox.warning(self, t("dialog_fail"), str(e))
        elif ptype == "openai_compat":
            try:
                from openai import OpenAI
                client = OpenAI(api_key=api_key, base_url=base_url)
                resp = client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": "hi"}], max_tokens=5)
                QMessageBox.information(self, t("dialog_success"), t("test_success_conn", resp.model))
            except Exception as e:
                QMessageBox.warning(self, t("dialog_fail"), str(e))
        elif ptype == "claude":
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=api_key)
                client.messages.create(
                    model=model, messages=[{"role": "user", "content": "hi"}], max_tokens=5)
                QMessageBox.information(self, t("dialog_success"), t("test_success_claude"))
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
        provider_name = self.provider_combo.currentText()
        provider = next((p for p in DEFAULT_PROVIDERS if p["name"] == provider_name), None)
        if provider:
            self.config["current_provider"] = {
                "name": provider_name,
                "type": provider["type"],
                "api_key": self.api_key_input.text().strip(),
                "base_url": self.base_url_input.text().strip(),
                "model": self.model_input.text().strip(),
            }
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
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_del)
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

        self.agent_emoji_input = QLineEdit()
        self.agent_emoji_input.setPlaceholderText(t("settings_ph_emoji"))
        form.addRow(t("settings_agent_emoji"), self.agent_emoji_input)

        self.agent_model_input = QLineEdit()
        self.agent_model_input.setPlaceholderText(t("settings_ph_model_hint"))
        form.addRow(t("settings_agent_model"), self.agent_model_input)

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
        for name, info in self.config.get("agents", {}).items():
            emoji = info.get("emoji", "🤖")
            title = info.get("title", name)
            self.agent_list.addItem(f"{emoji} {title}")

    def _on_agent_selected(self, current, _prev):
        if not current:
            return
        row = self.agent_list.currentRow()
        agents = self.config.get("agents", {})
        names = list(agents.keys())
        if row >= len(names):
            return
        name = names[row]
        info = agents[name]
        self.agent_name_input.setText(name)
        self.agent_name_input.setEnabled(name not in BUILTIN_AGENTS)
        self.agent_title_input.setText(info.get("title", ""))
        self.agent_emoji_input.setText(info.get("emoji", ""))
        self.agent_model_input.setText(info.get("model", ""))
        self.agent_temp_spin.setValue(info.get("temperature", 0.7))
        self.agent_skills_input.setText(", ".join(info.get("skills", [])))
        self.agent_prompt_input.setPlainText(info.get("system_prompt", ""))

    def _save_current_agent(self):
        name = self.agent_name_input.text().strip()
        if not name:
            return
        agents = self.config.setdefault("agents", {})
        if name not in agents:
            agents[name] = {}
        agents[name]["title"] = self.agent_title_input.text().strip()
        agents[name]["emoji"] = self.agent_emoji_input.text().strip() or "🤖"
        agents[name]["temperature"] = self.agent_temp_spin.value()
        agents[name]["skills"] = [s.strip() for s in self.agent_skills_input.text().split(",") if s.strip()]
        agents[name]["system_prompt"] = self.agent_prompt_input.toPlainText().strip()
        model = self.agent_model_input.text().strip()
        if model:
            agents[name]["model"] = model

    def _add_agent(self):
        name, ok = QInputDialog.getText(self, t("settings_btn_add"), t("settings_ph_agent_id"))
        if ok and name:
            name = name.strip().lower().replace(" ", "_")
            agents = self.config.setdefault("agents", {})
            if name in agents:
                QMessageBox.warning(self, t("dialog_prompt"), t("msg_agent_exists", name))
                return
            agents[name] = {"title": name, "emoji": "🤖", "skills": [], "temperature": 0.7, "system_prompt": ""}
            self._refresh_agent_list()
            self.agent_list.setCurrentRow(self.agent_list.count() - 1)

    def _del_agent(self):
        row = self.agent_list.currentRow()
        if row < 0:
            return
        agents = self.config.get("agents", {})
        names = list(agents.keys())
        if row >= len(names):
            return
        name = names[row]
        if name in BUILTIN_AGENTS:
            QMessageBox.warning(self, t("dialog_prompt"), t("msg_builtin_no_delete"))
            return
        if QMessageBox.question(self, t("dialog_confirm"),
                                t("msg_delete_agent", name)) == QMessageBox.StandardButton.Yes:
            del agents[name]
            self._refresh_agent_list()

    def _load_config(self):
        self._refresh_agent_list()

    def _save(self):
        self._save_current_agent()
        self.accept()

    def get_config(self) -> dict:
        return self.config


# ── 保留 SettingsDialog 作为兼容别名 ──
SettingsDialog = AppearanceDialog
