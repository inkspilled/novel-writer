"""局部修图：只贴回指定区域；所有修改由用户点按钮触发，旧版本留存。"""
import io
from pathlib import Path

from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout)


def paste_patch(original, edited, box):
    from PIL import Image
    with Image.open(original) as base, Image.open(edited) as patch:
        base = base.convert("RGB")
        left, top, right, bottom = box
        if not (0 <= left < right <= base.width and 0 <= top < bottom <= base.height):
            raise ValueError("修补区域必须在原图范围内")
        region = patch.crop(box) if patch.size == base.size else patch.resize((right-left, bottom-top))
        base.paste(region.convert("RGB"), (left, top))
        out = io.BytesIO()
        base.save(out, format="PNG")
        return out.getvalue()


class FrameToolsDialog(QDialog):
    def __init__(self, store, episode, parent=None):
        super().__init__(parent)
        self.store, self.episode = store, episode
        self.setWindowTitle("首尾帧裁切与局部贴回")
        self.resize(580, 420)
        layout = QVBoxLayout(self)
        label = QLabel("选择修改位置，修改后必须重新执行视觉审核。旧图自动保留。")
        label.setWordWrap(True)
        layout.addWidget(label)
        form = QFormLayout()
        self.shot = QComboBox()
        for shot in store.load_storyboard(episode)["shots"]:
            self.shot.addItem(shot["shot_id"])
        self.which = QComboBox()
        self.which.addItem("首帧", "first")
        self.which.addItem("尾帧", "last")
        form.addRow("镜号", self.shot)
        form.addRow("画面", self.which)
        self.patch = QLineEdit()
        form.addRow("改好后的图片", self.patch)
        choose = QPushButton("选择改好的图片…")
        choose.clicked.connect(self._choose)
        form.addRow(choose)
        self.box = []
        for label in ("左", "上", "右", "下"):
            spin = QSpinBox()
            spin.setRange(0, 32768)
            self.box.append(spin)
            form.addRow(label, spin)
        layout.addLayout(form)
        paste = QPushButton("只贴回指定区域")
        paste.clicked.connect(self._paste)
        layout.addWidget(paste)
        crop = QPushButton("首尾帧一起居中裁切为剧集画幅")
        crop.clicked.connect(self._crop)
        layout.addWidget(crop)

    def _choose(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择改图", "", "图片 (*.png *.jpg *.jpeg *.webp)")
        if path:
            self.patch.setText(path)

    def _paste(self):
        try:
            sid, which = self.shot.currentText(), self.which.currentData()
            data = paste_patch(self.store.keyframe_image_path(self.episode, sid, which),
                               Path(self.patch.text()), tuple(s.value() for s in self.box))
            self.store.save_keyframe_image(self.episode, sid, which, data)
            QMessageBox.information(self, "已保存", "指定区域已贴回，其余像素保持原样。请重新审核。")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "未修改", str(exc))

    def _crop(self):
        from PIL import Image, ImageOps
        from ...core.drama_keyframes import keyframe_canvas
        try:
            sid = self.shot.currentText()
            size = keyframe_canvas(self.store.load_series()["aspect"])
            results = []
            for which in ("first", "last"):
                with Image.open(self.store.keyframe_image_path(self.episode, sid, which)) as im:
                    out = io.BytesIO()
                    ImageOps.fit(im, size, Image.Resampling.LANCZOS).save(out, format="PNG")
                    results.append((which, out.getvalue()))
            for which, data in results:
                self.store.save_keyframe_image(self.episode, sid, which, data)
            QMessageBox.information(self, "已保存", "首尾帧已裁为相同尺寸。请检查构图并重新审核。")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "未修改", str(exc))
