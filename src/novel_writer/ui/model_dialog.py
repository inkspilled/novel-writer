"""模型设置对话框 — 供应商配置、已保存模型管理、连接测试。"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget,
    QLabel, QLineEdit, QComboBox, QPushButton, QFormLayout,
    QGroupBox, QMessageBox, QSpinBox, QListWidget,
    QListWidgetItem, QInputDialog, QScrollArea,
)
from PySide6.QtCore import QTimer

from .workers import TestConnectionWorker
from ..locales import t
from ..core.logger import get_logger

logger = get_logger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def load_default_providers() -> list[dict]:
    """从 default_providers.json 加载默认模型供应商列表。"""
    path = _CONFIG_DIR / "default_providers.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


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

        # ── 上下文大小设置 ──
        ctx_group = QGroupBox("上下文配置")
        ctx_layout = QHBoxLayout(ctx_group)
        ctx_layout.setSpacing(10)

        ctx_layout.addWidget(QLabel("最大上下文 tokens:"))
        self._ctx_tokens_spin = QSpinBox()
        self._ctx_tokens_spin.setRange(4096, 200_000)
        self._ctx_tokens_spin.setSingleStep(4096)
        self._ctx_tokens_spin.setValue(self.config.get("max_context_tokens", 120_000))
        self._ctx_tokens_spin.setToolTip("写作用的上下文上限，留空间给输出。140K模型建议设120K")
        ctx_layout.addWidget(self._ctx_tokens_spin)

        ctx_layout.addWidget(QLabel("(140K模型建议120K，留20K给输出)"))
        ctx_layout.addStretch()

        layout.addWidget(ctx_group)

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
        btn_row.setSpacing(8)
        self._btn_new_model = QPushButton("➕ 新增模型")
        self._btn_new_model.clicked.connect(self._new_model_config)
        btn_row.addWidget(self._btn_new_model)
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

        # 供应商选择
        provider_row = QHBoxLayout()
        provider_row.setSpacing(8)
        self.provider_combo = QComboBox()
        for p in self._providers:
            self.provider_combo.addItem(p["name"])
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_row.addWidget(self.provider_combo, 1)
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

        self._btn_test = QPushButton(t("settings_test_conn"))
        self._btn_test.clicked.connect(self._test_connection)
        pg.addRow("", self._btn_test)
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

        # ── 底部：保存 + 关闭按钮 ──
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)
        bottom_row.addStretch()
        self._btn_save = QPushButton(t("settings_save"))
        self._btn_save.setObjectName("primary")
        self._btn_save.setFixedHeight(34)
        self._btn_save.clicked.connect(self._save)
        bottom_row.addWidget(self._btn_save)
        self._btn_close = QPushButton(t("settings_close"))
        self._btn_close.setFixedHeight(34)
        self._btn_close.clicked.connect(self.reject)
        bottom_row.addWidget(self._btn_close)
        layout.addLayout(bottom_row)

        self._refresh_model_list()
        self._init_provider_ui()

    def _init_provider_ui(self):
        """初始化供应商 UI 状态，不触发 Ollama 网络请求。"""
        if not self._providers:
            return
        provider = self._providers[0]
        self.base_url_input.setText(provider["base_url"])
        is_ollama = provider["type"] == "ollama"
        self.ollama_group.setVisible(is_ollama)
        if is_ollama:
            self.ollama_status.setText(t("settings_ollama_detecting"))

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
            self._save_config_only()
            self._refresh_model_list()

    def _on_provider_changed(self, index: int):
        if index < 0 or index >= len(self._providers):
            return
        provider = self._providers[index]
        self.api_key_input.clear()
        self.base_url_input.setText(provider["base_url"])
        self.model_input.clear()
        is_ollama = provider["type"] == "ollama"
        self.ollama_group.setVisible(is_ollama)
        if is_ollama:
            self._refresh_ollama_models()

    def _new_model_config(self):
        """新增模型：清空表单，让用户填写新配置。"""
        self.model_list.clearSelection()
        self.provider_combo.setCurrentIndex(0)
        self.api_key_input.clear()
        self.base_url_input.setText(self._providers[0]["base_url"] if self._providers else "")
        self.model_input.clear()

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
        """测试连接（异步，不阻塞UI）。"""
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

        # 禁用按钮，显示测试中
        self._btn_test.setEnabled(False)
        self._btn_test.setText("⏳ 测试中...")

        # 启动后台线程
        self._test_worker = TestConnectionWorker(provider, api_key, base_url, model)
        self._test_worker.success.connect(self._on_test_success)
        self._test_worker.error.connect(self._on_test_error)
        self._test_worker.start()

    def _on_test_success(self, model_name: str):
        self._btn_test.setEnabled(True)
        self._btn_test.setText("🔌 " + t("settings_test_conn"))
        QMessageBox.information(self, t("dialog_success"), f"✅ 连接成功！\n模型: {model_name}")

    def _on_test_error(self, error_msg: str):
        self._btn_test.setEnabled(True)
        self._btn_test.setText("🔌 " + t("settings_test_conn"))
        QMessageBox.warning(self, t("dialog_fail"), f"❌ 连接失败\n{error_msg}")

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

    def _save_config_only(self):
        """仅保存配置到内存，不关闭对话框。"""
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
        self.config["max_context_tokens"] = self._ctx_tokens_spin.value()
        default_names = {p["name"] for p in load_default_providers()}
        custom_providers = [p for p in self._providers if p["name"] not in default_names]
        if custom_providers:
            self.config["custom_providers"] = custom_providers

    def _save(self):
        self._save_config_only()
        self.accept()

    def get_config(self) -> dict:
        return self.config
