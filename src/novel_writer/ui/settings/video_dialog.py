"""视频生成 API 配置（ComfyUI 视频工作流）。"""
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QComboBox,
)


class VideoDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = deepcopy(config)
        if isinstance(self.config.get("video_generation"), dict):
            self.config["video_generation"].pop("price_per_second", None)
        self.setWindowTitle("视频生成 API")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("把首尾帧与提示词交给视频服务。"))
        form = QFormLayout()
        layout.addLayout(form)
        cfg = config.get("video_generation") or {}
        self.provider = QComboBox()
        self.provider.addItem("ComfyUI 视频工作流", "comfyui")
        self.provider.addItem("Seedance（火山方舟）", "seedance")
        self.provider.setCurrentIndex(max(0, self.provider.findData(cfg.get("provider", "comfyui"))))
        form.addRow("服务类型", self.provider)
        self.ark_url = QLineEdit((cfg.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3") if cfg.get("provider") == "seedance" else "https://ark.cn-beijing.volces.com/api/v3")
        self.api_key = QLineEdit(cfg.get("api_key", ""))
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("可留空，优先读取环境变量 ARK_API_KEY")
        self.model = QComboBox()
        self.model.setEditable(True)
        from ...core.seedance import MINI_MODEL, MODELS
        self.model.addItems(list(MODELS))
        self.model.setCurrentText(cfg.get("model") or MINI_MODEL)
        self.extra = QLineEdit(json.dumps(cfg.get("extra_parameters") or {}, ensure_ascii=False))
        form.addRow("方舟 API 地址", self.ark_url)
        form.addRow("ARK API Key", self.api_key)
        form.addRow("视频模型", self.model)
        form.addRow("附加参数 JSON", self.extra)

        self.base_url = QLineEdit((cfg.get("base_url") or "http://127.0.0.1:8188") if cfg.get("provider", "comfyui") == "comfyui" else "http://127.0.0.1:8188")
        self.workflow = QLineEdit(cfg.get("workflow", ""))
        choose = QPushButton("选择视频工作流 JSON…")
        choose.clicked.connect(self._choose)
        self.timeout = QSpinBox()
        self.timeout.setRange(30, 7200)
        self.timeout.setSuffix(" 秒")
        self.timeout.setValue(int(cfg.get("timeout") or 900))
        self.fps = QSpinBox()
        self.fps.setRange(1, 60)
        self.fps.setSuffix(" fps")
        self.fps.setValue(int(cfg.get("fps") or 16))
        self.length = QSpinBox()
        self.length.setRange(1, 30)
        self.length.setSuffix(" 秒/镜")
        self.length.setValue(int(cfg.get("length") or 3))

        form.addRow("ComfyUI 地址", self.base_url)
        form.addRow("视频工作流", self.workflow)
        form.addRow(choose)
        form.addRow("生成超时", self.timeout)
        form.addRow("帧率", self.fps)
        form.addRow("默认单镜时长", self.length)

        hint = QLabel(
            "工作流必须是 API 格式，并使用占位符：\n"
            "{{positive}} {{first_frame}} {{last_frame}} {{seed}} {{prefix}} "
            "{{width}} {{height}} {{fps}} {{length}}\n"
            "输出节点用 SaveVideo / VHS_VideoCombine；首尾帧填上传后的文件名。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 11px; color: palette(placeholderText);")
        form.addRow(hint)
        self._form = form
        self._comfy_rows = [self.base_url, self.workflow, choose, hint]
        self._ark_rows = [self.ark_url, self.api_key, self.model, self.extra]
        self.provider.currentIndexChanged.connect(self._refresh)
        self._refresh()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh(self):
        cloud = self.provider.currentData() == "seedance"
        for widget in self._comfy_rows:
            self._form.setRowVisible(widget, not cloud)
        for widget in self._ark_rows:
            self._form.setRowVisible(widget, cloud)

    def _choose(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择视频工作流", "", "JSON (*.json)")
        if path:
            self.workflow.setText(path)

    def _save(self):
        if self.provider.currentData() == "seedance":
            url = self.ark_url.text().strip()
            try:
                parsed = urlparse(url)
                if parsed.scheme not in ("https", "http") or not parsed.netloc:
                    raise ValueError("请填写有效的方舟 API 地址")
                extra = json.loads(self.extra.text() or "{}")
                if not isinstance(extra, dict) or set(extra) & {"model", "content", "duration", "resolution", "ratio", "frames"}:
                    raise ValueError("附加参数需为 JSON 对象，不能覆盖模型、内容、时长、分辨率和画幅")
                if not self.model.currentText().strip():
                    raise ValueError("请填写视频模型")
            except ValueError as exc:
                QMessageBox.warning(self, "视频设置无效", str(exc))
                return
            section = dict(self.config.get("video_generation") or {})
            section.update(provider="seedance", base_url=url.rstrip("/"), api_key=self.api_key.text().strip(),
                           model=self.model.currentText().strip(), resolution="720p", extra_parameters=extra,
                           timeout=self.timeout.value(),
                           fps=self.fps.value(), length=self.length.value())
            self.config["video_generation"] = section
            self.accept()
            return
        url = self.base_url.text().strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            QMessageBox.warning(self, "配置不完整", "请填写有效的 HTTP / HTTPS 服务地址。")
            return
        workflow = self.workflow.text().strip()
        if not workflow:
            QMessageBox.warning(self, "配置不完整", "请选择 ComfyUI 视频工作流（API 格式）。")
            return
        try:
            data = json.loads(Path(workflow).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data or "nodes" in data:
                raise ValueError("请使用 API 格式工作流（Save (API Format) 导出）")
            text = json.dumps(data)
            missing = [
                token for token in ("{{positive}}", "{{width}}", "{{height}}")
                if token not in text
            ]
            if missing:
                raise ValueError("工作流缺少占位符：" + " ".join(missing))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "工作流无效", str(exc))
            return

        section = dict(self.config.get("video_generation") or {})
        section.update({
            "provider": "comfyui",
            "base_url": url.rstrip("/"),
            "workflow": workflow,
            "timeout": self.timeout.value(),
            "fps": self.fps.value(),
            "length": self.length.value(),
        })
        self.config["video_generation"] = section
        self.accept()

    def get_config(self):
        return self.config
