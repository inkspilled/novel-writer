"""主线程审批窗口：先查看具体产物，再明确批准。"""
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from .preview import ImagePreviewDialog, VideoPreviewDialog


class ProductionApprovalDialog(QDialog):
    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.setWindowTitle(request["title"])
        self.resize(760, 580)
        layout = QVBoxLayout(self)
        label = QLabel(request["text"])
        label.setWordWrap(True)
        layout.addWidget(label)
        for value in request.get("paths", []):
            path = Path(value)
            button = QPushButton(f"查看：{path.name}")
            button.clicked.connect(lambda checked=False, p=path: self._open(p))
            layout.addWidget(button)
            if path.suffix.lower() in (".md", ".txt") and path.is_file():
                editor = QPlainTextEdit(path.read_text(encoding="utf-8"))
                editor.setReadOnly(True)
                layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Yes).setText("确认并继续")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("停止，保留已有文件")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open(self, path):
        if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            ImagePreviewDialog(path, parent=self).exec()
        elif path.suffix.lower() in (".mp4", ".mkv", ".mov"):
            VideoPreviewDialog(path, parent=self).exec()
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
