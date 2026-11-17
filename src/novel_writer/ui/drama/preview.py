"""短剧素材预览：图片缩放查看、首尾帧对照、视频播放。

视频优先用 QtMultimedia 内嵌播放；没有多媒体后端时退回系统播放器，
不让「预览」把主界面拖死。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

try:  # 打包环境可能没带 QtMultimedia
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget

    MULTIMEDIA_OK = True
except ImportError:  # pragma: no cover - 取决于安装包
    QAudioOutput = QMediaPlayer = QVideoWidget = None
    MULTIMEDIA_OK = False


def thumbnail(path: Path | str | None, width: int, height: int) -> QPixmap | None:
    """读图并等比缩放到指定框内；文件不存在/不是图片返回 None。"""
    if not path:
        return None
    file = Path(path)
    if not file.is_file():
        return None
    pix = QPixmap(str(file))
    if pix.isNull():
        return None
    return pix.scaled(
        width, height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def open_in_system(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class ImagePreviewDialog(QDialog):
    """单张大图预览：打开原图 / 打开所在目录。"""

    def __init__(self, path: Path | str, title: str = "图片预览", parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.setWindowTitle(title)
        self.resize(900, 700)
        layout = QVBoxLayout(self)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = thumbnail(self.path, 860, 600)
        if pix is None:
            self.image.setText("图片不存在或无法读取")
        else:
            self.image.setPixmap(pix)
        layout.addWidget(self.image, 1)

        row = QHBoxLayout()
        row.addStretch()
        open_btn = QPushButton("打开原图")
        open_btn.clicked.connect(lambda: open_in_system(self.path))
        row.addWidget(open_btn)
        folder_btn = QPushButton("打开所在目录")
        folder_btn.clicked.connect(lambda: open_in_system(self.path.parent))
        row.addWidget(folder_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)


class VideoPreviewDialog(QDialog):
    """片段/整集播放：能内嵌就内嵌，否则给系统播放器入口。"""

    def __init__(self, path: Path | str, title: str = "视频预览", parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.setWindowTitle(title)
        self.resize(860, 620)
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        row = QHBoxLayout()
        row.addStretch()
        if self.path.is_file():
            system_btn = QPushButton("用系统播放器打开")
            system_btn.clicked.connect(lambda: open_in_system(self.path))
            row.addWidget(system_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)

        if not self.path.is_file():
            self.status.setText(f"视频还没生成：\n{self.path}")
            layout.addLayout(row)
            return

        if not MULTIMEDIA_OK:
            self.status.setText("当前环境没有 QtMultimedia，已提供系统播放器入口。")
            layout.addLayout(row)
            return

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget(self)
        self.player.setVideoOutput(self.video)
        layout.addWidget(self.video, 1)
        self.player.errorOccurred.connect(self._on_error)
        self.player.setSource(QUrl.fromLocalFile(str(self.path)))

        controls = QHBoxLayout()
        self.play_btn = QPushButton("暂停")
        self.play_btn.clicked.connect(self._toggle)
        controls.addWidget(self.play_btn)
        stop_btn = QPushButton("停止")
        stop_btn.clicked.connect(self.player.stop)
        controls.addWidget(stop_btn)
        controls.addStretch()
        layout.addLayout(controls)
        layout.addLayout(row)
        self.player.play()

    def _toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_btn.setText("播放")
        else:
            self.player.play()
            self.play_btn.setText("暂停")

    def _on_error(self, _error, message: str) -> None:
        self.status.setText(f"内嵌播放失败：{message}\n可以点「用系统播放器打开」。")


class _ImagePane(QWidget):
    """带标题的缩略图，点一下看大图。"""

    def __init__(self, title: str, width=260, height=200, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.caption = QLabel(title)
        self.caption.setStyleSheet("font-size: 11px; color: palette(placeholderText);")
        layout.addWidget(self.caption)
        self.label = QLabel("（未生成）")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setMinimumSize(width, height)
        self.label.setStyleSheet("border: 1px solid palette(mid); background: palette(base);")
        layout.addWidget(self.label, 1)
        self._path: Path | None = None
        self.label.mousePressEvent = self._open  # type: ignore[assignment]

    def set_image(self, path: Path | None, width: int, height: int) -> None:
        self._path = path if path and path.is_file() else None
        pix = thumbnail(self._path, width, height)
        if pix is None:
            self.label.setPixmap(QPixmap())
            self.label.setText("（未生成）")
        else:
            self.label.setPixmap(pix)
            self.label.setText("")

    def _open(self, _event) -> None:
        if self._path:
            ImagePreviewDialog(self._path, f"预览：{self._path.name}", self).exec()


class MakeupPreviewDialog(QDialog):
    """多视角参考图预览。默认单组（场景环景三视图 / 头肩定妆四角度）；
    ``groups`` 传 ``[(labels, (宽, 高)), ...]`` 可多行展示（如头肩+全身两层），
    ``views`` 的键与 labels 的键一致，「标记重做」返回该键。"""

    def __init__(self, name: str, views: dict[str, Path], parent=None, *,
                 scene=False, groups=None, title=None):
        from ...core.drama import MAKEUP_VIEWS
        from ...core.drama_scene import SCENE_VIEWS

        super().__init__(parent)
        self.scene = scene
        if scene:
            default_labels = dict(SCENE_VIEWS)
            default_size = (250, 150)
            default_title = "场景参考"
        else:
            default_labels = {view: label for view, (label, _) in MAKEUP_VIEWS.items()}
            default_size = (168, 224)
            default_title = "妆发定妆照"
        self.setWindowTitle(f"{name} · " + (title or default_title))
        if groups is None:
            groups = [(default_labels, default_size)]
        layout = QVBoxLayout(self)
        ordered_keys: list[str] = []
        for labels, (width, height) in groups:
            row = QHBoxLayout()
            for key, label in labels.items():
                pane = _ImagePane(label, width, height, self)
                pane.set_image(views.get(key), width, height)
                row.addWidget(pane)
                ordered_keys.append(key)
            layout.addLayout(row)
        actions = QHBoxLayout()
        self.angle = QComboBox()
        for key in ordered_keys:
            label = next(lbl for lbls, _size in groups for k, lbl in lbls.items() if k == key)
            if key in views:
                self.angle.addItem(label, key)
        self.redo_view = None
        redo = QPushButton("标记重做")
        redo.clicked.connect(self._redo)
        self.angle.setVisible(not scene)
        actions.addWidget(self.angle)
        actions.addWidget(redo)
        actions.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)

    def _redo(self):
        view = "panorama" if self.scene else self.angle.currentData()
        text = ("下次生成会重做环景及两张透视参考。" if self.scene else
                "下次生成会重做这一组（正面及其余角度）。"
                if view and (view.endswith("front") or view == "front") else
                f"下次生成会重做{self.angle.currentText()}参考图。")
        if QMessageBox.question(self, "标记重做", text + "旧图文件会保留。") == QMessageBox.StandardButton.Yes:
            self.redo_view = view
            self.accept()


class EpisodePreviewDialog(QDialog):
    """本集预览：左边镜号，右边首帧/尾帧 + 中间动作，底部播整集。"""

    def __init__(self, store, episode: int, parent=None):
        super().__init__(parent)
        self.store = store
        self.episode = int(episode)
        self.setWindowTitle(f"第{self.episode}集 · 首尾帧 / 视频预览")
        self.resize(980, 640)
        layout = QVBoxLayout(self)

        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setMinimumWidth(240)
        self.list.currentRowChanged.connect(self._refresh)
        body.addWidget(self.list, 0)

        right = QVBoxLayout()
        panes = QHBoxLayout()
        self.first = _ImagePane("首帧")
        self.last = _ImagePane("尾帧")
        panes.addWidget(self.first, 1)
        panes.addWidget(self.last, 1)
        right.addLayout(panes, 1)
        self.motion = QLabel("")
        self.motion.setWordWrap(True)
        self.motion.setStyleSheet("font-size: 12px; color: palette(placeholderText);")
        right.addWidget(self.motion)
        body.addLayout(right, 1)
        layout.addLayout(body, 1)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("font-size: 11px; color: palette(placeholderText);")
        bottom.addWidget(self.status, 1)
        play_btn = QPushButton("播放本集视频")
        play_btn.clicked.connect(self._play_episode)
        bottom.addWidget(play_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

        self.shots = store.load_storyboard(self.episode)["shots"]
        briefs = {item["shot_id"]: item for item in store.load_keyframes(self.episode)["shots"]}
        self.briefs = briefs
        for shot in self.shots:
            sid = str(shot.get("shot_id") or "")
            has = "✓" if sid in briefs else "✗"
            self.list.addItem(f"{has} {sid} · {shot.get('shot_size') or ''}")
        if self.shots:
            self.list.setCurrentRow(0)
        self._update_status()

    def _update_status(self) -> None:
        video = self._video_path()
        if video.is_file():
            self.status.setText(f"整集视频：{video}")
        else:
            clips = list(self.store.episode_clip_dir(self.episode).glob("*.mp4"))
            self.status.setText(
                f"整集还没合成；已有 {len(clips)} 段片段，可先「用系统播放器」看单镜。"
                if clips else "还没有视频，先在「短剧工作流 → 生成本集视频」生成。"
            )

    def _video_path(self):
        from ...core.drama_production import read_state, file_digest
        try:
            report = read_state(self.store.root / "review" / f"post_ep{self.episode:03d}.json")
            candidate = Path(report.get("output") or "")
            if candidate.is_file() and all(
                file_digest(self.store.episode_clip_dir(self.episode) / name) == expected
                for name, expected in report.get("source_fingerprints", {}).items()
            ):
                return candidate
        except (OSError, ValueError):
            pass
        return self.store.episode_video_path(self.episode)

    def _refresh(self, row: int) -> None:
        if row < 0 or row >= len(self.shots):
            return
        shot = self.shots[row]
        sid = str(shot.get("shot_id") or "")
        self.first.set_image(self.store.keyframe_image_path(self.episode, sid, "first"), 340, 240)
        self.last.set_image(self.store.keyframe_image_path(self.episode, sid, "last"), 340, 240)
        brief = self.briefs.get(sid) or {}
        self.motion.setText("中间：" + (brief.get("motion") or "（还没有首尾帧说明）"))

    def _play_episode(self) -> None:
        video = self._video_path()
        if video.is_file():
            VideoPreviewDialog(video, f"第{self.episode}集", self).exec()
            return
        clips = sorted(self.store.episode_clip_dir(self.episode).glob("*.mp4"))
        if clips:
            QMessageBox.information(
                self, "还没有整集",
                f"整集还没合成，先播放第一个片段：{clips[0].name}",
            )
            VideoPreviewDialog(clips[0], f"片段：{clips[0].name}", self).exec()
            return
        QMessageBox.information(self, "还没有视频", "先跑「短剧工作流 → 生成本集视频」。")
