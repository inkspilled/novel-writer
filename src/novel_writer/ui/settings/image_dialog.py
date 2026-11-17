"""独立的图片生成服务配置。"""
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse
import json
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit,
    QComboBox, QSpinBox, QDialogButtonBox, QFileDialog, QPushButton, QMessageBox, QLabel, QWidget,
    QCheckBox)


class ImageDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = deepcopy(config)
        self.setWindowTitle("图片生成 API")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("用于短剧资产中的人物、场景参考图生成。"))
        form = QFormLayout()
        layout.addLayout(form)
        cfg = config.get("image_generation") or {}
        comfy = config.get("comfyui") or {}
        self.provider = QComboBox()
        self.provider.addItem("图片 API（兼容 /images/generations）", "api")
        self.provider.addItem("ComfyUI", "comfyui")
        self.provider.addItem("Codex CLI（本机已登录）", "codex")
        self.provider.setCurrentIndex(max(0, self.provider.findData(cfg.get("provider", "comfyui" if comfy.get("workflow") else "api"))))
        form.addRow("服务类型", self.provider)
        self.api_fields = QWidget()
        api = QFormLayout(self.api_fields)
        self.base_url = QLineEdit(cfg.get("base_url", ""))
        self.base_url.setPlaceholderText("https://服务地址/v1")
        self.api_key = QLineEdit(cfg.get("api_key", ""))
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model = QLineEdit(cfg.get("model", ""))
        self.model.setPlaceholderText("服务商提供的图片模型名称")
        self.negative_check = QCheckBox("服务支持独立负面词字段（negative_prompt）")
        self.negative_check.setChecked(bool(cfg.get("supports_negative_prompt")))
        self.negative_check.setToolTip(
            "勾选后负面词走 negative_prompt 字段；\n"
            "不勾选则附在正向词末尾（「避免：…」，已知降级）。"
        )
        api.addRow("API 地址", self.base_url)
        api.addRow("API Key", self.api_key)
        api.addRow("图片模型", self.model)
        api.addRow("负面词", self.negative_check)
        form.addRow(self.api_fields)
        self.comfy_fields = QWidget()
        cf = QFormLayout(self.comfy_fields)
        self.comfy_url = QLineEdit(comfy.get("base_url") or "http://127.0.0.1:8188")
        self.workflow = QLineEdit(comfy.get("workflow", ""))
        choose = QPushButton("选择 API 工作流 JSON…")
        choose.clicked.connect(self._choose)
        cf.addRow("ComfyUI 地址", self.comfy_url)
        cf.addRow("工作流模板", self.workflow)
        cf.addRow(choose)
        self.reference_workflow = QLineEdit(comfy.get("reference_workflow", ""))
        self.reference_workflow.setPlaceholderText("可选：用定妆图保持人物／场景外观")
        choose_ref = QPushButton("选择参考图工作流 JSON…")
        choose_ref.clicked.connect(self._choose_reference)
        cf.addRow("一致性工作流", self.reference_workflow)
        cf.addRow(choose_ref)
        builtin = QPushButton("使用内置短剧工作流（Qwen-Image 2.1）")
        builtin.clicked.connect(self._use_drama_workflows)
        cf.addRow(builtin)
        hint = QLabel("从 ComfyUI 导出 API 格式 JSON，提示词使用 {{positive}} / {{negative}}，种子用 {{seed}}，文件名前缀用 {{prefix}}。")
        hint.setWordWrap(True)
        cf.addRow(hint)
        form.addRow(self.comfy_fields)
        self.codex_bin = QLineEdit(cfg.get("codex_bin", ""))
        self.codex_bin.setPlaceholderText("可选，留空从 PATH 查找 codex")
        form.addRow("Codex 可执行文件", self.codex_bin)
        self._form = form
        self.timeout = QSpinBox()
        self.timeout.setRange(10, 3600)
        self.timeout.setSuffix(" 秒")
        self.timeout.setValue(int(cfg.get("timeout") or comfy.get("timeout") or 300))
        form.addRow("生成超时", self.timeout)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.provider.currentIndexChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self):
        api = self.provider.currentData() == "api"
        self.api_fields.setVisible(api)
        self.comfy_fields.setVisible(self.provider.currentData() == "comfyui")
        self._form.setRowVisible(self.codex_bin, self.provider.currentData() == "codex")

    def _choose(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 API 工作流", "", "JSON (*.json)")
        if path:
            self.workflow.setText(path)

    def _choose_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择参考图 API 工作流", "", "JSON (*.json)")
        if path:
            self.reference_workflow.setText(path)

    def _use_drama_workflows(self):
        from ...core.app_config import resolve_config_path
        self.workflow.setText(str(resolve_config_path("comfyui/drama_portrait_api.json")))
        self.reference_workflow.setText(str(resolve_config_path("comfyui/drama_reference_api.json")))
        self.timeout.setValue(900)

    def _save(self):
        provider = self.provider.currentData()
        url = (self.base_url if provider == "api" else self.comfy_url).text().strip()
        parsed = urlparse(url)
        if provider != "codex" and (parsed.scheme not in ("http", "https") or not parsed.netloc):
            QMessageBox.warning(self, "配置不完整", "请填写有效的 HTTP / HTTPS 服务地址。")
            return
        if provider == "api" and not self.model.text().strip():
            QMessageBox.warning(self, "配置不完整", "请填写图片模型名称。")
            return
        if provider == "comfyui":
            try:
                workflow = json.loads(Path(self.workflow.text().strip()).read_text(encoding="utf-8"))
                if not isinstance(workflow, dict) or not workflow or "nodes" in workflow:
                    raise ValueError("请使用 API 格式工作流")
                if self.reference_workflow.text().strip():
                    ref = json.loads(Path(self.reference_workflow.text().strip()).read_text(encoding="utf-8"))
                    if not isinstance(ref, dict) or not ref or "nodes" in ref or "{{reference_image}}" not in json.dumps(ref):
                        raise ValueError("一致性工作流需要 API 格式，并使用 {{reference_image}} 作为参考图输入")
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "工作流无效", str(exc))
                return
        image_cfg = dict(self.config.get("image_generation") or {})
        image_cfg.update({
            "provider": provider, "base_url": self.base_url.text().strip().rstrip("/"),
            "api_key": self.api_key.text().strip(), "model": self.model.text().strip(),
            "timeout": self.timeout.value(),
            "supports_negative_prompt": self.negative_check.isChecked(),
            "codex_bin": self.codex_bin.text().strip(),
        })
        self.config["image_generation"] = image_cfg
        self.config["comfyui"] = {**(self.config.get("comfyui") or {}),
            "base_url": self.comfy_url.text().strip(), "workflow": self.workflow.text().strip(),
            "reference_workflow": self.reference_workflow.text().strip(),
            "timeout": self.timeout.value()}
        self.accept()

    def get_config(self):
        return self.config
