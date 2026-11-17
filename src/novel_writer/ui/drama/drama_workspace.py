"""短剧工作区 — 小说项目下的独立大页签：企划 / 剧本 / 分镜 / 资产。

短剧不单独建项目，数据写在当前小说的 drama/ 目录；但页面和小说正文/规划/世界
完全分开，右侧还有自己的短剧工作流入口。
"""
from __future__ import annotations

from pathlib import Path
import json

from PySide6.QtCore import Qt, QSettings, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QSplitter, QStackedWidget, QTableWidget, QCheckBox, QScrollArea,
    QTableWidgetItem, QVBoxLayout, QWidget, QMenu,
)

from ...core.drama import (
    ASSET_COLUMNS, BIBLE_FILE, FULLBODY_VIEWS, MAKEUP_VIEWS, PROP_VIEWS,
    DramaStore, SHOT_COLUMNS, new_shot,
)
from ...core.app_config import load_config
from ...core.drama_scene import SCENE_VIEWS
from ...core.logger import get_logger
from ...designsystem import get_theme_colors
from ...models.project import Project

from .preview import EpisodePreviewDialog, ImagePreviewDialog, MakeupPreviewDialog

logger = get_logger(__name__)

_GENRES = ["仙侠", "玄幻", "都市", "悬疑", "言情", "古装", "战神", "逆袭", "其他"]
_STYLES = ["快节奏", "爽感", "甜宠", "虐恋", "悬疑", "搞笑", "其他"]
_PLATFORMS = ["抖音", "快手", "视频号", "红果", "其他"]
_ASPECTS = ["9:16", "16:9", "3:4"]


def _make_script_editor(placeholder: str = "") -> QPlainTextEdit:
    edit = QPlainTextEdit()
    edit.setPlaceholderText(placeholder)
    font = QFont("Microsoft YaHei", 14)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    edit.setFont(font)
    edit.document().setDocumentMargin(20)
    return edit


def _episode_label(ep: dict) -> str:
    number = int(ep.get("number") or 0)
    title = str(ep.get("title") or "")
    default = f"第{number}集"
    return f"第{number}集 · {title}" if title and title != default else default


def _set_table_item(table: QTableWidget, row: int, col: int, value) -> None:
    text = "" if value is None else str(value)
    item = QTableWidgetItem(text)
    table.setItem(row, col, item)


class _SeriesPage(QWidget):
    """企划页：剧集参数 + 改编圣经。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store: DramaStore | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        root = QVBoxLayout(content)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        title = QLabel("企划")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(title)
        hint = QLabel("先定平台、画幅、集数和单集时长，再写改编规则。分镜和资产都从这里取参数。")
        hint.setStyleSheet("font-size: 12px; color: palette(placeholderText);")
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("剧名")
        form.addRow("剧名", self.title_edit)

        self.genre_combo = QComboBox()
        self.genre_combo.setEditable(True)
        self.genre_combo.addItems(_GENRES)
        form.addRow("题材", self.genre_combo)

        self.style_combo = QComboBox()
        self.style_combo.setEditable(True)
        self.style_combo.addItems(_STYLES)
        form.addRow("风格", self.style_combo)

        self.platform_combo = QComboBox()
        self.platform_combo.setEditable(True)
        self.platform_combo.addItems(_PLATFORMS)
        form.addRow("平台", self.platform_combo)

        self.aspect_combo = QComboBox()
        self.aspect_combo.setEditable(True)
        self.aspect_combo.addItems(_ASPECTS)
        form.addRow("画幅", self.aspect_combo)

        self.episodes_spin = QSpinBox()
        self.episodes_spin.setRange(1, 999)
        self.episodes_spin.setSuffix(" 集")
        form.addRow("目标集数", self.episodes_spin)

        self.seconds_spin = QSpinBox()
        self.seconds_spin.setRange(15, 600)
        self.seconds_spin.setSuffix(" 秒/集")
        form.addRow("单集时长", self.seconds_spin)

        self.logline_edit = QLineEdit()
        self.logline_edit.setPlaceholderText("一句话卖点：谁想要什么、谁挡着、局面怎么失控")
        form.addRow("一句话卖点", self.logline_edit)

        self.palette_edit = QLineEdit()
        self.palette_edit.setPlaceholderText("例：夜戏冷蓝 + 暖黄灯笼；日戏青灰 + 土黄（画第一张图前定死）")
        self.palette_edit.setToolTip(
            "配色单独设计，不交给模型发挥。\n"
            "定完把画面转成黑白看一遍：人和背景分不开，说明明暗对比不够，要重配。"
        )
        form.addRow("配色", self.palette_edit)
        self.music_edit = QLineEdit()
        self.music_edit.setPlaceholderText("不配乐，或歌名 / 时长 / BPM")
        form.addRow("配乐说明", self.music_edit)
        self.music_file = QLineEdit()
        self.music_file.setPlaceholderText("原曲文件绝对路径，用于拍点检测和后期配乐")
        form.addRow("原曲文件", self.music_file)
        self.subtitles_check = QCheckBox("需要字幕（后期依据剧本制作，不让视频模型画字）")
        form.addRow("字幕", self.subtitles_check)
        self.reference_edit = QLineEdit()
        self.reference_edit.setPlaceholderText("参考片绝对路径；留空则按分镜规则构思")
        form.addRow("参考片", self.reference_edit)
        self.requirements_edit = QPlainTextEdit()
        self.requirements_edit.setPlaceholderText("你的要求与偏好原文，供监督和代理核对")
        self.requirements_edit.setFixedHeight(100)
        form.addRow("要求与偏好", self.requirements_edit)
        # 后期参数表单：常用项直接勾选，不再要求手写 JSON。
        # 键与 drama_post_pipeline.validate_options 一一对应，repair 仍要指名镜号。
        post_box = QWidget()
        post_form = QFormLayout(post_box)
        post_form.setContentsMargins(0, 0, 0, 0)
        post_form.setSpacing(6)
        self.post_transitions = QCheckBox("镜头间加转场")
        self.post_transition_kind = QComboBox()
        self.post_transition_kind.setEditable(True)
        self.post_transition_kind.addItems([
            "fade", "fadeblack", "fadewhite", "dissolve", "wipeleft", "wiperight",
            "wipeup", "wipedown", "slideleft", "slideright", "slideup", "slidedown",
            "circleopen", "circleclose", "radial", "smoothleft", "smoothright", "motion_blur",
        ])
        post_row = QHBoxLayout()
        post_row.addWidget(self.post_transitions)
        post_row.addWidget(QLabel("方式"))
        post_row.addWidget(self.post_transition_kind)
        self.post_transition_seconds = QDoubleSpinBox()
        self.post_transition_seconds.setRange(0.05, 2.0)
        self.post_transition_seconds.setSingleStep(0.05)
        self.post_transition_seconds.setValue(0.4)
        self.post_transition_seconds.setSuffix(" 秒")
        post_row.addWidget(self.post_transition_seconds)
        post_form.addRow("转场", post_row)

        output_row = QHBoxLayout()
        self.post_grade = QCheckBox("调色统一")
        self.post_grade.setChecked(True)
        output_row.addWidget(self.post_grade)
        self.post_upscale = QCheckBox("放大 1080×1920")
        self.post_upscale.setChecked(True)
        output_row.addWidget(self.post_upscale)
        self.post_fps = QSpinBox()
        self.post_fps.setRange(1, 60)
        self.post_fps.setValue(24)
        self.post_fps.setSuffix(" fps")
        output_row.addWidget(QLabel("帧率"))
        output_row.addWidget(self.post_fps)
        self.post_source_audio = QCheckBox("保留原片音频")
        output_row.addWidget(self.post_source_audio)
        post_form.addRow("画质", output_row)

        self.post_to709 = QCheckBox("按 BT.709 解码（确认源是 BT.601 再勾）")
        post_form.addRow("", self.post_to709)

        repair_row = QHBoxLayout()
        self.post_drop_first = QCheckBox("去首帧黑帧")
        repair_row.addWidget(self.post_drop_first)
        self.post_freeze_tail = QCheckBox("尾帧冻结")
        repair_row.addWidget(self.post_freeze_tail)
        self.post_rebalance = QCheckBox("重平衡")
        repair_row.addWidget(self.post_rebalance)
        post_form.addRow("片段修复", repair_row)
        self.post_repair_shots = QLineEdit()
        self.post_repair_shots.setPlaceholderText("修复作用镜号：E01-S001,E01-S002（不填则不修复）")
        post_form.addRow("", self.post_repair_shots)
        form.addRow("后期参数", post_box)
        root.addLayout(form)

        bible_label = QLabel("改编圣经（人设/视觉/台词/节奏规则）")
        bible_label.setStyleSheet("font-size: 12px; font-weight: 600; margin-top: 6px;")
        root.addWidget(bible_label)
        self.bible_edit = _make_script_editor(
            "例：\n"
            "- 每集 90 秒，前 3 秒必须出现冲突或异常。\n"
            "- 主角林乐天：外表隐忍、内里狠，不轻易解释。\n"
            "- 视觉：冷灰底 + 骨牌暖白发光；所有夜景统一青蓝。\n"
            "- 台词：短句、口语，禁用大段心理独白。\n"
        )
        self.bible_edit.setMinimumHeight(220)
        root.addWidget(self.bible_edit, 1)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("font-size: 11px; color: palette(placeholderText);")
        bottom.addWidget(self.status, 1)
        self.btn_save = QPushButton("保存企划")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(lambda: self.save())
        bottom.addWidget(self.btn_save)
        root.addLayout(bottom)

    def load(self, store: DramaStore, project: Project) -> None:
        self._store = store
        series = store.load_series()
        self.title_edit.setText(series.get("title") or project.title)
        self.genre_combo.setCurrentText(series.get("genre") or project.genre)
        self.style_combo.setCurrentText(series.get("style") or project.style)
        self.platform_combo.setCurrentText(series.get("platform") or "抖音")
        self.aspect_combo.setCurrentText(series.get("aspect") or "9:16")
        self.episodes_spin.setValue(int(series.get("episode_count") or project.target_chapters or 30))
        self.seconds_spin.setValue(int(series.get("episode_seconds") or 90))
        self.logline_edit.setText(series.get("logline") or project.synopsis)
        self.palette_edit.setText(series.get("palette") or "")
        self.music_edit.setText(series.get("music") or "不配乐")
        self.music_file.setText(series.get("music_file") or "")
        self.subtitles_check.setChecked(bool(series.get("subtitles")))
        self.reference_edit.setText(series.get("reference_video") or "")
        self.requirements_edit.setPlainText(series.get("requirements") or "")
        self.set_post_options(series.get("post_options") or {})
        bible_path = store.root / BIBLE_FILE
        try:
            self.bible_edit.setPlainText(
                bible_path.read_text(encoding="utf-8") if bible_path.exists() else ""
            )
        except OSError:
            self.bible_edit.setPlainText("")
        self.status.setText("")

    def post_options(self) -> dict:
        """表单 → 后期参数 dict（键与 validate_options 对齐）。"""
        options: dict = {
            "transitions": self.post_transitions.isChecked(),
            "transition_kind": self.post_transition_kind.currentText().strip() or "fade",
            "transition_seconds": float(self.post_transition_seconds.value()),
            "grade": self.post_grade.isChecked(),
            "to_709": self.post_to709.isChecked(),
            "upscale": self.post_upscale.isChecked(),
            "fps": int(self.post_fps.value()),
            "use_source_audio": self.post_source_audio.isChecked(),
        }
        shots = [s.strip() for s in self.post_repair_shots.text().replace("，", ",").split(",") if s.strip()]
        repair_flags = {
            "drop_first": self.post_drop_first.isChecked(),
            "freeze_tail": self.post_freeze_tail.isChecked(),
            "rebalance": self.post_rebalance.isChecked(),
        }
        if shots or any(repair_flags.values()):
            options["repair"] = {"shots": shots, **repair_flags}
        return options

    def set_post_options(self, options: dict) -> None:
        options = options if isinstance(options, dict) else {}
        self.post_transitions.setChecked(bool(options.get("transitions")))
        self.post_transition_kind.setCurrentText(str(options.get("transition_kind") or "fade"))
        try:
            self.post_transition_seconds.setValue(float(options.get("transition_seconds", 0.4)))
        except (TypeError, ValueError):
            self.post_transition_seconds.setValue(0.4)
        self.post_grade.setChecked(bool(options.get("grade", True)))
        self.post_upscale.setChecked(bool(options.get("upscale", True)))
        try:
            self.post_fps.setValue(int(options.get("fps") or 24))
        except (TypeError, ValueError):
            self.post_fps.setValue(24)
        self.post_source_audio.setChecked(bool(options.get("use_source_audio")))
        self.post_to709.setChecked(bool(options.get("to_709")))
        repair = options.get("repair") if isinstance(options.get("repair"), dict) else {}
        self.post_drop_first.setChecked(bool(repair.get("drop_first")))
        self.post_freeze_tail.setChecked(bool(repair.get("freeze_tail")))
        self.post_rebalance.setChecked(bool(repair.get("rebalance")))
        shots = repair.get("shots") or []
        self.post_repair_shots.setText(",".join(str(s) for s in shots if isinstance(s, str)))

    def validation_error(self):
        try:
            from ...core.drama_post import validate_options
            validate_options(self.post_options())
        except (ValueError, TypeError) as exc:
            return f"后期参数无效：{exc}"
        return ""

    def save(self, silent: bool = False) -> dict:
        if self._store is None:
            return {}
        if self.validation_error():
            self.status.setText(self.validation_error())
            return {}
        data = {
            "title": self.title_edit.text().strip(),
            "genre": self.genre_combo.currentText().strip(),
            "style": self.style_combo.currentText().strip(),
            "platform": self.platform_combo.currentText().strip(),
            "aspect": self.aspect_combo.currentText().strip(),
            "episode_count": self.episodes_spin.value(),
            "episode_seconds": self.seconds_spin.value(),
            "logline": self.logline_edit.text().strip(),
            "palette": self.palette_edit.text().strip(),
            "music": self.music_edit.text().strip(),
            "music_file": self.music_file.text().strip(),
            "subtitles": self.subtitles_check.isChecked(),
            "reference_video": self.reference_edit.text().strip(),
            "requirements": self.requirements_edit.toPlainText().strip(),
            "post_options": self.post_options(),
            "intake": {"music_answered": True},
        }
        series = self._store.save_series(data)
        try:
            self._store.save_bible(self.bible_edit.toPlainText())
        except OSError as e:
            logger.warning("保存改编圣经失败: %s", e)
        if not silent:
            self.status.setText("已保存企划")
        return series


class _ScriptPage(QWidget):
    """剧本页：分集列表 + 本集目标/钩子 + 剧本正文。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store: DramaStore | None = None
        self._episodes: list[dict] = []
        self._current = 0
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget()
        bar.setObjectName("editorToolbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(20, 10, 20, 10)
        title = QLabel("剧本")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        bl.addWidget(title)
        bl.addStretch()
        self.btn_add = QPushButton("＋ 新增一集")
        self.btn_add.clicked.connect(self._add_episode)
        bl.addWidget(self.btn_add)
        self.btn_del = QPushButton("删除本集")
        self.btn_del.setObjectName("danger")
        self.btn_del.clicked.connect(self._delete_episode)
        bl.addWidget(self.btn_del)
        self.btn_save = QPushButton("保存剧本")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(lambda: self.save())
        bl.addWidget(self.btn_save)
        root.addWidget(bar)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        self.list = QListWidget()
        self.list.setFixedWidth(190)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self._on_episode_changed)
        body.addWidget(self.list)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(16, 8, 20, 12)
        rv.setSpacing(8)
        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("本集标题")
        self.title_edit.editingFinished.connect(lambda: self.save(silent=True))
        meta_row.addWidget(self.title_edit, 1)
        rv.addLayout(meta_row)

        goal_row = QHBoxLayout()
        goal_row.setSpacing(8)
        self.goal_edit = QLineEdit()
        self.goal_edit.setPlaceholderText("本集目标：这一集结束时局面发生了什么不可逆的变化？")
        self.goal_edit.editingFinished.connect(lambda: self.save(silent=True))
        goal_row.addWidget(self.goal_edit, 1)
        self.hook_edit = QLineEdit()
        self.hook_edit.setPlaceholderText("集尾钩子：最后 5 秒留什么问题？")
        self.hook_edit.editingFinished.connect(lambda: self.save(silent=True))
        goal_row.addWidget(self.hook_edit, 1)
        rv.addLayout(goal_row)

        self.source_label = QLabel("未关联小说章节")
        self.source_label.setWordWrap(True)
        rv.addWidget(self.source_label)

        self.editor = _make_script_editor(
            "用 ## 第N场 地点 · 日/夜 开场；\n"
            "分镜页可以按场次生成草稿，再把每场拆成多个镜头。"
        )
        self.editor.textChanged.connect(self._on_text_changed)
        rv.addWidget(self.editor, 1)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("font-size: 11px; color: palette(placeholderText);")
        bottom.addWidget(self.status, 1)
        rv.addLayout(bottom)
        body.addWidget(right)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes([190, 760])
        root.addWidget(body, 1)

        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(1200)
        self._autosave.timeout.connect(lambda: self.save(silent=True))

    def load(self, store: DramaStore, keep: int = 0) -> None:
        self._autosave.stop()
        self._store = store
        current = keep or self._current or 1
        self._loading = True
        self._episodes = store.load_episodes()
        self.list.clear()
        for ep in self._episodes:
            item = QListWidgetItem(_episode_label(ep))
            sources = ep.get("source_chapters") or ([ep["source_chapter"]] if ep.get("source_chapter") else [])
            if sources:
                item.setToolTip("来源：小说第 " + "、".join(map(str, sources)) + " 章")
            item.setData(Qt.ItemDataRole.UserRole, ep["number"])
            self.list.addItem(item)
        if not self._episodes:
            self._current = 0
            self.editor.clear()
            self.title_edit.clear()
            self.goal_edit.clear()
            self.hook_edit.clear()
            self.source_label.setText("未关联小说章节")
            self._loading = False
            self.status.setText("暂无剧集，点击「新增一集」开始")
            return
        numbers = [int(ep["number"]) for ep in self._episodes]
        idx = numbers.index(current) if current in numbers else 0
        self.list.setCurrentRow(idx)
        self._loading = False
        self._load_episode(self._episodes[idx]["number"])

    def _load_episode(self, number: int) -> None:
        if self._store is None:
            return
        ep = next((e for e in self._episodes if int(e["number"]) == int(number)), None)
        if ep is None:
            return
        self._current = int(number)
        self._loading = True
        self.title_edit.setText(ep.get("title", ""))
        self.goal_edit.setText(ep.get("goal", ""))
        self.hook_edit.setText(ep.get("hook", ""))
        sources = ep.get("source_chapters") or ([ep["source_chapter"]] if ep.get("source_chapter") else [])
        self.source_label.setText("来源：小说第 " + "、".join(map(str, sources)) + " 章" if sources else "未关联小说章节")
        plan = ep.get("adaptation_plan") or {}
        self.source_label.setToolTip("剧情节拍：" + " → ".join(plan.get("beats", [])))
        self.editor.setPlainText(self._store.load_script(number))
        self._loading = False
        self.status.setText(f"第{number}集 · {len(self.editor.toPlainText())} 字")

    def _on_episode_changed(self, row: int) -> None:
        if self._loading or row < 0 or row >= len(self._episodes):
            return
        if self._current and self._current != int(self._episodes[row]["number"]):
            self.save(silent=True)
        self._load_episode(int(self._episodes[row]["number"]))

    def _on_text_changed(self) -> None:
        if not self._loading:
            self.status.setText("编辑中…")
            self._autosave.start()

    def _add_episode(self) -> None:
        if self._store is None:
            return
        self.save(silent=True)
        ep = self._store.add_episode()
        self.load(self._store, keep=int(ep["number"]))
        self.list.setCurrentRow(len(self._episodes) - 1)

    def _delete_episode(self) -> None:
        if self._store is None or not self._current:
            return
        ret = QMessageBox.question(
            self, "删除本集",
            f"删除第{self._current}集的剧本和分镜文件？\n（小说正文不受影响）",
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._store.delete_episode(self._current)
        self._current = 0
        self.load(self._store)

    def save(self, silent: bool = False) -> None:
        if self._store is None or not self._current:
            return
        self._autosave.stop()
        text = self.editor.toPlainText()
        self._store.save_script(self._current, text)
        title = self.title_edit.text().strip() or f"第{self._current}集"
        self._store.update_episode_meta(
            self._current,
            title=title,
            goal=self.goal_edit.text().strip(),
            hook=self.hook_edit.text().strip(),
        )
        # 刷新列表标题，但不触发 currentRowChanged
        row = self.list.currentRow()
        if 0 <= row < self.list.count():
            self.list.blockSignals(True)
            self.list.item(row).setText(_episode_label({"number": self._current, "title": title}))
            self.list.blockSignals(False)
        self.status.setText(f"第{self._current}集 · 已保存")
        if not silent:
            self._autosave.stop()


class _StoryboardPage(QWidget):
    """分镜页：一集一张表，背后是 storyboard/epNNN.json。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store: DramaStore | None = None
        self._episodes: list[dict] = []
        self._current = 0
        self._loading = False
        self._dirty = False
        # 默认只留 8 个关键列；「全部字段」展开其余（选择会记住，下次不再展开）
        self._extra_keys = {"scene_id", "start_sec", "gen_seconds", "time", "blocking",
                            "emotion", "motion", "sfx", "transition", "status", "notes"}
        self._settings = QSettings("novel_writer", "drama")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget()
        bar.setObjectName("editorToolbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(20, 10, 20, 10)
        title = QLabel("分镜")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        bl.addWidget(title)
        self.episode_combo = QComboBox()
        self.episode_combo.setMinimumWidth(130)
        self.episode_combo.currentIndexChanged.connect(self._on_episode_changed)
        bl.addWidget(self.episode_combo)
        bl.addStretch()
        self.btn_draft = QPushButton("按剧本场次生成草稿")
        self.btn_draft.setToolTip("从剧本的 ## 场次标题生成一镜一场，之后再拆细")
        self.btn_draft.clicked.connect(self._generate_from_script)
        bl.addWidget(self.btn_draft)
        self.btn_add = QPushButton("＋ 镜头")
        self.btn_add.clicked.connect(self._add_shot)
        bl.addWidget(self.btn_add)
        self.btn_del = QPushButton("删除")
        self.btn_del.setObjectName("danger")
        self.btn_del.clicked.connect(self._delete_shots)
        bl.addWidget(self.btn_del)
        self.btn_all_fields = QPushButton("全部字段")
        self.btn_all_fields.setCheckable(True)
        self.btn_all_fields.setToolTip("显示/隐藏次要列（场次、片中时间、生成秒数、走位、表情等）。选择会记住。")
        self.btn_all_fields.blockSignals(True)  # 表格还没建，先只设状态
        self.btn_all_fields.setChecked(self._settings.value("storyboard_full_fields", False, type=bool))
        self.btn_all_fields.blockSignals(False)
        self.btn_all_fields.toggled.connect(self._toggle_extra_fields)
        bl.addWidget(self.btn_all_fields)
        self.btn_save = QPushButton("保存")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(lambda: self.save())
        bl.addWidget(self.btn_save)
        root.addWidget(bar)

        tools = QHBoxLayout()
        tools.setContentsMargins(20, 6, 20, 6)
        tools.setSpacing(8)
        self.status = QLabel("")
        self.status.setStyleSheet("font-size: 11px; color: palette(placeholderText);")
        tools.addWidget(self.status, 1)
        self.btn_csv = QPushButton("导出 CSV")
        self.btn_csv.clicked.connect(self._export_csv)
        tools.addWidget(self.btn_csv)
        self.btn_json = QPushButton("导出 JSON")
        self.btn_json.clicked.connect(self._export_json)
        tools.addWidget(self.btn_json)
        self.btn_check = QPushButton("分镜检查")
        self.btn_check.clicked.connect(self._check_storyboard)
        tools.addWidget(self.btn_check)
        self.btn_video = QPushButton("导出视频提示词")
        self.btn_video.setToolTip("按七段式把每镜展开成视频提示词（Markdown），附提交前核对清单")
        self.btn_video.clicked.connect(self._export_video_prompts)
        tools.addWidget(self.btn_video)
        self.btn_kf_batch = QPushButton("导出首尾帧批次")
        self.btn_kf_batch.setToolTip("把本集每镜的首帧/尾帧提示词和填好的 ComfyUI 工作流打包")
        self.btn_kf_batch.clicked.connect(self._export_keyframe_batch)
        tools.addWidget(self.btn_kf_batch)
        self.btn_preview = QPushButton("预览本集")
        self.btn_preview.setToolTip("看每镜首尾帧图，并播放整集视频")
        self.btn_preview.clicked.connect(self._preview_episode)
        menu = QMenu(self.btn_preview)
        menu.addAction("预览本集", self._preview_episode)
        menu.addAction("首尾帧裁切 / 局部贴回", self._frame_tools)
        self.btn_preview.setMenu(menu)
        tools.addWidget(self.btn_preview)
        root.addLayout(tools)

        self.table = QTableWidget(0, len(SHOT_COLUMNS))
        self.table.setHorizontalHeaderLabels([label for _key, label in SHOT_COLUMNS])
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        widths = {
            "shot_id": 88, "scene_id": 62, "shot_size": 70, "camera": 82,
            "duration_sec": 68, "location": 110, "time": 64, "characters": 92,
            "sfx": 100, "transition": 64, "status": 68, "notes": 100,
        }
        for idx, (key, _label) in enumerate(SHOT_COLUMNS):
            if key in ("action", "dialogue"):
                header.setSectionResizeMode(idx, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(idx, QHeaderView.ResizeMode.Interactive)
                self.table.setColumnWidth(idx, widths.get(key, 90))
            if key in self._extra_keys:
                self.table.setColumnHidden(idx, True)
        if self.btn_all_fields.isChecked():
            self._toggle_extra_fields(True)
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table, 1)

        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(900)
        self._autosave.timeout.connect(lambda: self.save(silent=True))

    def _frame_tools(self):
        if self._store and self._current:
            from .frame_tools import FrameToolsDialog
            FrameToolsDialog(self._store, self._current, self).exec()

    def load(self, store: DramaStore, keep: int = 0) -> None:
        self._store = store
        current = keep or self._current or 1
        self._episodes = store.load_episodes()
        self._loading = True
        self.episode_combo.clear()
        for ep in self._episodes:
            self.episode_combo.addItem(_episode_label(ep), int(ep["number"]))
        numbers = [int(e["number"]) for e in self._episodes]
        idx = numbers.index(current) if current in numbers else 0
        self.episode_combo.setCurrentIndex(idx)
        self._loading = False
        self._load_episode(numbers[idx] if numbers else 0)

    def _load_episode(self, number: int) -> None:
        if self._store is None:
            return
        if not number:
            self._current = 0
            self._loading = True
            self.table.setRowCount(0)
            self._loading = False
            self.status.setText("暂无剧集")
            return
        self._current = int(number)
        data = self._store.load_storyboard(number)
        self._loading = True
        self.table.setRowCount(0)
        for shot in data["shots"]:
            self._append_row(shot)
        self._loading = False
        self._dirty = False
        report = self._store.episode_report(number)
        duration = f" · {report['seconds']:g}/{report['target_seconds']:g} 秒"
        if data.get("script_changed"):
            self.status.setText(
                f"第{number}集 · {len(data['shots'])} 个镜头 · ⚠ 剧本已改，分镜可能过期"
            )
        else:
            self.status.setText(f"第{number}集 · {len(data['shots'])} 个镜头{duration}")

    def _append_row(self, shot: dict) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, (key, _label) in enumerate(SHOT_COLUMNS):
            value = shot.get(key, "")
            if key == "duration_sec":
                try:
                    value = f"{float(value):.1f}"
                except (TypeError, ValueError):
                    value = ""
            _set_table_item(self.table, row, col, value)

    def _on_episode_changed(self, _idx: int) -> None:
        if self._loading or self._store is None:
            return
        number = self.episode_combo.currentData()
        if not number:
            return
        if self._current and int(number) != int(self._current):
            self.save(silent=True)
            self._load_episode(int(number))

    def _on_item_changed(self, _item) -> None:
        if self._loading:
            return
        self._dirty = True
        self.status.setText("编辑中…")
        self._autosave.start()

    def _toggle_extra_fields(self, checked: bool) -> None:
        for idx, (key, _label) in enumerate(SHOT_COLUMNS):
            if key in self._extra_keys:
                self.table.setColumnHidden(idx, not checked)
        self._settings.setValue("storyboard_full_fields", bool(checked))

    def _add_shot(self) -> None:
        if self._store is None or not self._current:
            return
        index = self.table.rowCount() + 1
        shot = new_shot(self._current, index)
        self._append_row(shot)
        self.table.scrollToBottom()
        self._dirty = True
        self.save(silent=True)

    def _delete_shots(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for row in rows:
            self.table.removeRow(row)
        self._dirty = True
        self.save(silent=True)

    def _generate_from_script(self) -> bool:
        if self._store is None or not self._current:
            return False
        script = self._store.load_script(self._current)
        shots = self._store.shots_from_script(script, self._current)
        if not shots:
            QMessageBox.information(
                self, "生成分镜", "没有在剧本里找到「## 第N场」或「场景：」标题。\n"
                "先按场次写剧本，再来生成草稿。"
            )
            return False
        if self.table.rowCount():
            ret = QMessageBox.question(
                self, "生成分镜",
                f"将按剧本场次生成 {len(shots)} 个镜头，并覆盖当前分镜表。\n"
                "手工改过的内容会丢失，继续？",
            )
            if ret != QMessageBox.StandardButton.Yes:
                return False
        self._loading = True
        self.table.setRowCount(0)
        for shot in shots:
            self._append_row(shot)
        self._loading = False
        self._dirty = True
        self.save(silent=True, update_source=True)
        self.status.setText(f"第{self._current}集 · 已按场次生成 {len(shots)} 个镜头")
        return True

    def _read_shots(self) -> list[dict]:
        shots: list[dict] = []
        for row in range(self.table.rowCount()):
            shot: dict = {}
            for col, (key, _label) in enumerate(SHOT_COLUMNS):
                item = self.table.item(row, col)
                value = item.text().strip() if item else ""
                if key == "duration_sec":
                    try:
                        value = float(value)
                    except ValueError:
                        value = 0.0
                shot[key] = value
            shots.append(shot)
        return shots

    def save(self, silent: bool = False, update_source: bool = False) -> None:
        if self._store is None or not self._current:
            return
        self._autosave.stop()
        self._store.save_storyboard(
            self._current, {"shots": self._read_shots()}, update_source=update_source
        )
        self._dirty = False
        fresh = self._store.load_storyboard(self._current)
        suffix = " · ⚠ 剧本已改，分镜可能过期" if fresh.get("script_changed") else ""
        self.status.setText(
            f"第{self._current}集 · 已保存 {self.table.rowCount()} 镜{suffix}"
        )

    def _export_csv(self) -> None:
        self.save(silent=True)
        if self._store is None:
            return
        path = self._store.export_storyboard_csv(self._current or None)
        self._show_export(path, "CSV")

    def _export_json(self) -> None:
        self.save(silent=True)
        if self._store is None:
            return
        path = self._store.export_storyboard_json(self._current or None)
        self._show_export(path, "JSON")

    def _export_video_prompts(self) -> None:
        self.save(silent=True)
        if self._store is None:
            return
        path = self._store.export_video_prompts(self._current or None)
        self._show_export(path, "视频提示词")

    def _export_keyframe_batch(self) -> None:
        """本集首尾帧提示词 + 填好的图片工作流，打包给 ComfyUI 画首尾帧。"""
        self.save(silent=True)
        if self._store is None:
            return
        from ...core.comfyui import ComfyError
        from ...core.image_generation import config_from_app

        cfg = config_from_app()
        try:
            path = self._store.export_comfy_batch(
                workflow=str(cfg.get("workflow") or ""),
                base_url=str(cfg.get("base_url") or ""),
                episode=self._current or None,
                include_assets=False,
            )
        except ComfyError as exc:
            QMessageBox.warning(self, "导出首尾帧批次失败", str(exc))
            return
        self._show_export(path, "首尾帧批次")

    def _preview_episode(self) -> None:
        """首尾帧对照 + 整集视频播放。"""
        self.save(silent=True)
        if self._store is None:
            return
        EpisodePreviewDialog(self._store, self._current or 1, self).exec()

    def _check_storyboard(self) -> None:
        """分镜体检：景别连排/平视正面超两次/方向缺失/多人同时动作等。"""
        if self._store is None:
            return
        self.save(silent=True)
        issues = self._store.audit_storyboard(self._read_shots())
        if not issues:
            QMessageBox.information(self, "分镜检查", "没发现分镜问题。")
            return
        lines = [f"{item['shot_id']}：{item['issue']}" for item in issues[:40]]
        if len(issues) > 40:
            lines.append(f"…… 另有 {len(issues) - 40} 条")
        box = QMessageBox(self)
        box.setWindowTitle("分镜检查")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"发现 {len(issues)} 个问题：")
        box.setDetailedText("\n".join(lines))
        box.exec()

    def _show_export(self, path: Path, kind: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(f"导出 {kind}")
        box.setText(f"已导出：\n{path}")
        open_btn = box.addButton("打开所在目录", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


class _AssetsPage(QWidget):
    """资产页：先做注册表，生成接口下一阶段接。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store: DramaStore | None = None
        self._comfy_worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel("资产")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(title)
        hint = QLabel(
            "资产与小说正典对应：人物取自角色卡，地点/道具取自世界状态；"
            "「从小说导入」补登记，「生成提示词」按人物设定+题材风格拼提示词。\n"
            "人物生成四角度头肩定妆 + 全身正/侧/背三视图，场景生成 360° 环景及正反向透视，"
            "道具生成正/侧/背三视图。失败项记进清单，可只重试失败的。\n"
            "复杂场景可在「备注」填主入口、通道、前后景与固定视觉锚点。"
        )
        hint.setStyleSheet("font-size: 12px; color: palette(placeholderText);")
        hint.setWordWrap(True)
        root.addWidget(hint)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.status = QLabel("")
        self.status.setStyleSheet("font-size: 11px; color: palette(placeholderText);")
        bar.addWidget(self.status, 1)
        self.btn_import = QPushButton("从小说导入")
        self.btn_import.clicked.connect(self._import_from_canon)
        bar.addWidget(self.btn_import)
        self.btn_prompts = QPushButton("生成提示词")
        prompt_menu = QMenu(self.btn_prompts)
        prompt_menu.addAction("补齐空白提示词", self._fill_prompts)
        prompt_menu.addAction("重新生成选中行", lambda: self._fill_prompts(regenerate=True))
        prompt_menu.addAction("根据上次审核重写", self._rewrite_reviewed_prompt)
        prompt_menu.addAction("编辑选中提示词", lambda: self.edit_prompt(self._selected_row()))
        self.btn_prompts.setMenu(prompt_menu)
        bar.addWidget(self.btn_prompts)
        self.btn_check = QPushButton("检查资产")
        self.btn_check.clicked.connect(self._check_assets)
        bar.addWidget(self.btn_check)
        self.btn_gen = QPushButton("生成图片")
        self.btn_gen.setObjectName("primary")
        self.btn_gen.clicked.connect(self._generate_images)
        bar.addWidget(self.btn_gen)
        self.btn_batch = QPushButton("导出 ComfyUI 批次")
        self.btn_batch.setToolTip("把素材、提示词和填好的工作流打成自包含批次包，交给 ComfyUI 直接跑")
        self.btn_batch.clicked.connect(self._export_comfy_batch)
        bar.addWidget(self.btn_batch)
        self.btn_import_results = QPushButton("导入生成结果")
        self.btn_import_results.setToolTip("手工跑完批次包后，把出图按文件名前缀回流进项目（画幅不对会逐张提示）")
        self.btn_import_results.clicked.connect(self._import_results)
        bar.addWidget(self.btn_import_results)
        self.btn_records = QPushButton("生成记录")
        self.btn_records.setToolTip("这张图怎么来的：工作流、seed、提示词、参考图、时间")
        self.btn_records.clicked.connect(self._show_records)
        bar.addWidget(self.btn_records)
        self.btn_library_match = QPushButton("匹配公用库")
        self.btn_library_match.setToolTip("AI 判定选中的原创/手工资产能否直接复用跨项目公用素材库"
                                          "（没配模型时按名称匹配）；复用不出图，仍需你确认")
        self.btn_library_match.clicked.connect(self._match_library)
        bar.addWidget(self.btn_library_match)
        self.btn_donate = QPushButton("存入公用库")
        self.btn_donate.setToolTip("把选中资产的成组参考图回存跨项目公用素材库，"
                                   "其他项目就能复用（不成组会提示先补齐）")
        self.btn_donate.clicked.connect(self._donate_library)
        bar.addWidget(self.btn_donate)
        self.btn_add = QPushButton("＋ 资产")
        self.btn_add.clicked.connect(self._add_asset)
        bar.addWidget(self.btn_add)
        self.btn_del = QPushButton("删除")
        self.btn_del.setObjectName("danger")
        self.btn_del.clicked.connect(self._delete_assets)
        bar.addWidget(self.btn_del)
        self.btn_preview = QPushButton("预览图片")
        self.btn_preview.setEnabled(False)
        self.btn_preview.setToolTip("查看人物定妆照、场景环景或道具参考图")
        self.btn_preview.clicked.connect(lambda: self._open_asset_image(self._selected_row()))
        bar.addWidget(self.btn_preview)
        self.btn_open = QPushButton("打开资产目录")
        self.btn_open.clicked.connect(self._open_dir)
        bar.addWidget(self.btn_open)
        self.btn_save = QPushButton("保存")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(lambda: self.save())
        bar.addWidget(self.btn_save)
        root.addLayout(bar)

        self.table = QTableWidget(0, len(ASSET_COLUMNS))
        self.table.setHorizontalHeaderLabels([label for _key, label in ASSET_COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        for idx, (key, _label) in enumerate(ASSET_COLUMNS):
            header.setSectionResizeMode(idx, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(idx, {"kind": 70, "id": 110, "name": 110,
                                             "source": 90, "prompt": 240, "ref": 160,
                                             "status": 60, "notes": 130}.get(key, 100))
        self.table.itemChanged.connect(lambda _: self.status.setText("编辑中…"))
        root.addWidget(self.table, 1)

        self.table.itemSelectionChanged.connect(
            lambda: self.btn_preview.setEnabled(self.table.selectionModel().hasSelection())
        )
        self.table.cellDoubleClicked.connect(self._open_asset_cell)

    def _open_asset_cell(self, row: int, col: int) -> None:
        if ASSET_COLUMNS[col][0] == "prompt":
            self.edit_prompt(row)
        else:
            self._open_asset_image(row, col)

    def select_asset(self, asset_id: str) -> int:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item and item.text().strip() == asset_id:
                self.table.setCurrentCell(row, 4)
                return row
        return -1

    def edit_prompt(self, row: int, review: str = "") -> None:
        if self._store is None or row < 0:
            self.status.setText("请先选中一项资产")
            return
        item = self.table.item(row, 4)
        if item is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑提示词 · " + self.table.item(row, 2).text())
        dialog.resize(680, 440 if not review else 560)
        layout = QVBoxLayout(dialog)
        if review:
            layout.addWidget(QLabel("审核意见"))
            report = QPlainTextEdit(review, dialog)
            report.setReadOnly(True)
            layout.addWidget(report, 1)
        layout.addWidget(QLabel("基础提示词"))
        editor = QPlainTextEdit(item.text(), dialog)
        layout.addWidget(editor, 2)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(dialog.reject)
        buttons.addWidget(cancel)
        save = QPushButton("保存提示词")
        save.setObjectName("primary")
        save.clicked.connect(dialog.accept)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        editor.setFocus()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            item.setText(editor.toPlainText().strip())
            self.save(silent=True)
            self.status.setText("提示词已保存，可重新生成图片")

    def _rewrite_reviewed_prompt(self) -> None:
        row = self._selected_row()
        if self._store is None or row < 0:
            self.status.setText("请先选中审核未通过的资产")
            return
        asset_id = self.table.item(row, 1).text().strip()
        name = self.table.item(row, 2).text().strip() or asset_id
        folder = self._store.root / "review"
        try:
            request = json.loads((folder / "asset_prompt_review.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            try:
                title, _, review = (folder / "asset_review.md").read_text(encoding="utf-8").partition("\n")
            except OSError:
                title, review = "", ""
            request = {"asset_id": asset_id, "review": review.strip(), "reviewed_prompts": ""} if (
                title == f"# {name} · 设定图提示词"
                and sum(a["name"] == name for a in self._store.load_assets()) == 1
            ) else {}
        except (OSError, ValueError):
            request = {}
        if not isinstance(request, dict) or request.get("asset_id") != asset_id or not request.get("review"):
            self.status.setText("选中资产没有可用的提示词审核意见")
            return
        window = self.window()
        if not hasattr(window, "_start_drama_worker") or window.llm is None:
            self.status.setText("根据审核重写需要配置语言模型")
            return
        window._start_drama_worker("asset_prompt_rewrite", "current", prompt_rewrite=request)

    def load(self, store: DramaStore) -> None:
        self._store = store
        assets = store.load_assets()
        self.table.setRowCount(0)
        for asset in assets:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, (key, _label) in enumerate(ASSET_COLUMNS):
                _set_table_item(self.table, row, col, asset.get(key, ""))
                if key == "prompt":
                    item = self.table.item(row, col)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if key in ("reference_image", "status") and asset["kind"] == "character":
                    count = len(store.makeup_paths(asset))
                    self.table.item(row, col).setToolTip(f"妆发定妆照 {count}/{len(MAKEUP_VIEWS)}")
                if key in ("ref", "status") and asset["kind"] == "location":
                    count = len(store.scene_paths(asset))
                    self.table.item(row, col).setToolTip(f"场景参考 {count}/{len(SCENE_VIEWS)}")
        self.status.setText(f"{len(assets)} 个资产")

    def _add_asset(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        # 必须与 ASSET_COLUMNS 一一对应（含 reference_image 列），否则整行错位
        id_col = [key for key, _label in ASSET_COLUMNS].index("id")
        used = {a["id"] for a in self._store.load_assets()} if self._store else set()
        for r in range(row):
            item = self.table.item(r, id_col)
            if item and item.text().strip():
                used.add(item.text().strip())
        n = row + 1
        while f"asset_{n:03d}" in used:
            n += 1
        values = ["character", f"asset_{n:03d}", "", "manual", "", "", "", "draft", ""]
        for col, value in enumerate(values):
            _set_table_item(self.table, row, col, value)
        item = self.table.item(row, 4)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.status.setText("已新增一行，记得保存")

    # ── 图片预览 ──

    def _asset_image_path(self, row: int) -> Path | None:
        if self._store is None or row < 0:
            return None
        columns = [key for key, _label in ASSET_COLUMNS]
        for key in ("reference_image", "ref"):
            if key not in columns:
                continue
            item = self.table.item(row, columns.index(key))
            text = item.text().strip() if item else ""
            if not text:
                continue
            path = Path(text)
            if not path.is_absolute():
                path = self._store.assets_dir / path
            if path.is_file():
                return path
        return None

    def _selected_row(self) -> int:
        current = self.table.currentIndex()
        if self.table.selectionModel().isSelected(current):
            return current.row()
        return min((index.row() for index in self.table.selectedIndexes()), default=-1)

    def _open_asset_image(self, row: int, _col: int = 0) -> None:
        if self._store is not None and row >= 0:
            item = self.table.item(row, 1)
            asset = next((a for a in self._store.load_assets() if item and a["id"] == item.text()), {})
            kind = asset.get("kind")
            if kind == "location" and self._store.scene_paths(asset):
                dialog = MakeupPreviewDialog(asset["name"], self._store.scene_paths(asset),
                                             self, scene=True)
                dialog.exec()
                if dialog.redo_view:
                    self.save(silent=True)
                    self._store.clear_scene_panorama(asset["id"])
                    self._after_redo(row)
                return
            if kind == "character" and (self._store.makeup_paths(asset)
                                        or self._store.fullbody_paths(asset)):
                from ...core.drama import FULLBODY_VIEWS
                labels = {v: label for v, (label, _angle) in MAKEUP_VIEWS.items()}
                fb_labels = {f"fb_{v}": f"全身{label}"
                             for v, (label, _angle) in FULLBODY_VIEWS.items()}
                views = {**self._store.makeup_paths(asset),
                         **{f"fb_{k}": p for k, p in self._store.fullbody_paths(asset).items()}}
                groups = [(labels, (168, 224)), (fb_labels, (150, 200))]
                dialog = MakeupPreviewDialog(asset["name"], views, self,
                                             groups=groups, title="人物参考图")
                dialog.exec()
                if dialog.redo_view:
                    self.save(silent=True)
                    key = dialog.redo_view
                    if str(key).startswith("fb_"):
                        self._store.clear_fullbody_view(asset["id"], str(key)[3:])
                    else:
                        self._store.clear_makeup_view(asset["id"], key)
                    self._after_redo(row)
                return
            if kind == "prop" and self._store.prop_paths(asset):
                from ...core.drama import PROP_VIEWS
                labels = {v: label for v, (label, _angle) in PROP_VIEWS.items()}
                dialog = MakeupPreviewDialog(asset["name"], self._store.prop_paths(asset), self,
                                             groups=[(labels, (200, 200))], title="道具三视图")
                dialog.exec()
                if dialog.redo_view:
                    self.save(silent=True)
                    self._store.clear_prop_view(asset["id"], dialog.redo_view)
                    self._after_redo(row)
                return
        path = self._asset_image_path(row)
        if path is None:
            self.status.setText("这一行还没有图")
            return
        ImagePreviewDialog(path, f"预览：{path.name}", self).exec()

    def _after_redo(self, row: int) -> None:
        self.load(self._store)
        self.table.setCurrentCell(row, 1)
        self.status.setText("已标记重做。点击「生成图片」继续。")

    # ── 与小说正典 / ComfyUI ──

    def _import_from_canon(self) -> None:
        if self._store is None:
            return
        added = self._store.sync_assets_from_canon()
        self.load(self._store)
        self.status.setText(
            f"从小说导入 {added} 个资产" if added else "人物/地点都已登记，没有新增"
        )

    def _fill_prompts(self, *, regenerate=False) -> None:
        if self._store is None:
            return
        rows = {index.row() for index in self.table.selectedIndexes()}
        if regenerate and not rows:
            self.status.setText("请先选中需要重新生成提示词的资产")
            return
        selected = {self.table.item(row, 1).text().strip()
                    for row in rows if self.table.item(row, 1)}
        if regenerate and QMessageBox.question(
            self, "重新生成提示词", "用当前资产设定重新生成选中行的提示词？这会替换原提示词。",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.save(silent=True)
        assets = self._store.load_assets()
        changed = 0
        for asset in assets:
            needs_prompt = asset["id"] in selected if regenerate else not asset["prompt"]
            if not needs_prompt:
                continue
            prompt = self._store.scene_prompt if asset["kind"] == "location" else self._store.asset_prompt
            positive, _negative = prompt({**asset, "prompt": ""})
            asset["prompt"] = positive
            changed += 1
        if changed:
            self._store.save_assets(assets)
            self.reload_images()
        self.status.setText(f"生成 {changed} 条提示词" if changed else "提示词都已存在")

    def _selected_assets(self) -> list[dict]:
        """选中行对应的资产；没选就处理全部。"""
        if self._store is None:
            return []
        assets = {a["id"]: a for a in self._store.load_assets()}
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows:
            rows = list(range(self.table.rowCount()))
        picked: list[dict] = []
        for row in rows:
            item = self.table.item(row, 1)  # ID 列
            if item and item.text().strip() in assets:
                picked.append(assets[item.text().strip()])
        return picked

    def _check_assets(self) -> None:
        if self._store is None:
            return
        report = self._store.check_assets()
        lines = [
            f"共 {report['total']} 个资产：小说正典 {report['canon']} · "
            f"短剧原创 {report['original']} · 手工 {report['manual']}",
            f"缺提示词 {report['missing_prompt']} 个 · 缺参考图 {report['missing_image']} 个",
        ]
        if report["makeup_incomplete"]:
            lines.append(f"{report['makeup_incomplete']} 个人物的妆发定妆照待补齐")
        if report["scene_incomplete"]:
            lines.append(f"{report['scene_incomplete']} 个场景的环景参考待补齐")
        if report["orphan"]:
            lines.append("正典失联（角色/地点可能已改名）：" + "、".join(report["orphan"][:8]))
        if report["duplicates"]:
            lines.append("重名：" + "、".join(report["duplicates"][:8]))
        risks = report.get("prompt_risk") or []
        if risks:
            lines.append(f"提示词风险 {len(risks)} 个（生成前会逐条提醒）：")
            for item in risks[:6]:
                lines.append(f"  · {item['name']}：{'；'.join(item['issues'][:2])}")
        aspect = report.get("aspect_risk") or []
        if aspect:
            lines.append(f"画幅不符 {len(aspect)} 个（出图比例和类型画幅对不上，先裁切再用）：")
            for item in aspect[:6]:
                lines.append(f"  · {item['name']}：{item['issue']}")
        lines.append(
            "\n短剧原创资产（龙套/临时场景）不算错，但不会出现在小说人物/地点里；"
            "正典资产改动小说设定后，重新「从小说导入」只补缺、不覆盖已有行。"
        )
        QMessageBox.information(self, "资产检查", "\n".join(lines))

    def _generate_images(self) -> None:
        if self._store is None:
            return
        if self._comfy_worker is not None and self._comfy_worker.isRunning():
            self.status.setText("正在生成，等这一批结束")
            return
        from ...core.image_generation import config_from_app, configuration_error
        from .comfy_worker import ComfyImageWorker

        cfg = config_from_app()
        if configuration_error(cfg):
            window = self.window()
            if hasattr(window, "_open_image_settings"):
                window._open_image_settings()
                cfg = config_from_app()
            if configuration_error(cfg):
                self.status.setText("请在「模型与智能体 → 图片生成 API」完成配置后再生成")
                return
        self.save(silent=True)
        picked = self._selected_assets()
        if not picked:
            self.status.setText("没有可生成的资产（先「从小说导入」或从分镜提取）")
            return
        # 展示资产提示词体检结果。
        risks = [
            f"{asset.get('name') or asset.get('id')}：{issue}"
            for asset in picked
            for issue in self._store.audit_asset_prompt(asset)
        ]
        if risks:
            box = QMessageBox(self)
            box.setWindowTitle("生成前体检")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(f"发现 {len(risks)} 个提示词风险，生成出来大概率要重画：")
            box.setDetailedText("\n".join(risks[:40]))
            go = box.addButton("仍然生成", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not go:
                self.status.setText("已取消生成：先修提示词")
                return
        window = self.window()
        if hasattr(window, "_start_drama_worker"):
            if window._drama_busy():
                self.status.setText("短剧任务正在运行，请等待或停止后再生成资产")
                return
            if window.llm is None:
                self.status.setText("生成资产需要先配置审核模型；视觉审核需要支持图片的模型")
                return
            self._production_selection = [a["id"] for a in picked]
            window._start_drama_worker("asset_images", "current")
            return
        worker = ComfyImageWorker(self._store, picked, cfg, parent=self)
        worker.progress.connect(lambda text, pct: self.status.setText(f"{text} · {pct}%"))
        worker.log.connect(lambda text: self.status.setText(text))
        worker.finished_ok.connect(self._on_images_done)
        worker.failed.connect(self._on_images_failed)
        worker.finished.connect(lambda: self.btn_gen.setEnabled(True))
        self._comfy_worker = worker
        self.btn_gen.setEnabled(False)
        worker.start()

    def _on_images_done(self, summary: str) -> None:
        self._comfy_worker = None
        self.btn_gen.setEnabled(True)
        self.load(self._store)
        self.status.setText(summary)

    def _on_images_failed(self, error: str) -> None:
        self._comfy_worker = None
        self.btn_gen.setEnabled(True)
        self.reload_images()
        self.status.setText(f"生成失败：{error[:160]}")

    def reload_images(self) -> None:
        if self._store is None:
            return
        selected = {(index.row(), index.column()) for index in self.table.selectedIndexes()}
        current = self.table.currentIndex()
        row, column = current.row(), current.column()
        self.load(self._store)
        if (row, column) in selected and row < self.table.rowCount():
            self.table.setCurrentCell(row, column)
        for row, column in selected:
            item = self.table.item(row, column)
            if item:
                item.setSelected(True)

    def _delete_assets(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)
        if rows:
            self.status.setText("已删除，记得保存")

    def _open_dir(self) -> None:
        if self._store is None:
            return
        self._store.assets_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._store.assets_dir)))

    def _export_comfy_batch(self) -> None:
        """素材 + 提示词 + 填好的工作流打成一个包，交给 ComfyUI 跑。"""
        if self._store is None:
            return
        from ...core.comfyui import ComfyError
        from ...core.image_generation import config_from_app

        cfg = config_from_app()
        try:
            path = self._store.export_comfy_batch(
                workflow=str(cfg.get("workflow") or ""),
                reference_workflow=str(cfg.get("reference_workflow") or ""),
                base_url=str(cfg.get("base_url") or ""),
            )
        except (ComfyError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "导出 ComfyUI 批次失败", str(exc))
            return
        self._show_export(path, "ComfyUI 批次")

    def _import_results(self) -> None:
        """手工跑完的批次包 / ComfyUI output 出图按前缀回流进项目。"""
        if self._store is None:
            return
        folder = QFileDialog.getExistingDirectory(
            self, "选择出图目录（批次包 images/ 或 ComfyUI 的 output）")
        if not folder:
            return
        try:
            result = self._store.import_comfy_results(folder)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导入生成结果失败", str(exc))
            return
        self.load(self._store)
        lines = [f"已导入 {result['imported']} 张。"]
        if result["failed"]:
            lines.append(f"失败 {len(result['failed'])} 张（画幅不对等，原图保留在原目录）：")
            lines.extend(f"  · {item}" for item in result["failed"][:10])
        if result["unmatched"]:
            lines.append(f"没认出前缀 {len(result['unmatched'])} 张（文件名要对上资产 ID）：")
            lines.extend(f"  · {name}" for name in result["unmatched"][:10])
        QMessageBox.information(self, "导入生成结果", "\n".join(lines))

    def _show_records(self) -> None:
        """生成记录：选中的资产看它的配方，没选就看最近的。"""
        if self._store is None:
            return
        from ...core.drama_records import read_generation_records, record_markdown

        target = None
        row = self._selected_row()
        if row >= 0:
            item = self.table.item(row, 1)
            asset_id = item.text().strip() if item else ""
            prefix = f"asset:{asset_id}:"
            if asset_id and any(
                str(r.get("target") or "").startswith(prefix)
                for r in read_generation_records(self._store)
            ):
                target = prefix
        text = record_markdown(self._store, target=target, limit=100)
        dialog = QDialog(self)
        dialog.setWindowTitle("生成记录")
        dialog.resize(860, 520)
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit(text.replace("# 生成记录\n", "").strip())
        editor.setReadOnly(True)
        layout.addWidget(editor)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def _match_library(self) -> None:
        """AI 判定选中资产能否复用公用素材库（走 production worker，命中后仍需确认）。"""
        if self._store is None:
            return
        window = self.window()
        if not hasattr(window, "_start_drama_worker"):
            self.status.setText("当前窗口不支持启动短剧任务")
            return
        if window._drama_busy():
            self.status.setText("短剧任务正在运行，请等待或停止后再匹配")
            return
        picked = [a["id"] for a in self._selected_assets()]
        self._production_selection = picked or None
        window._start_drama_worker("library_match", "current")

    def _donate_library(self) -> None:
        """把选中资产的成组参考图回存跨项目公用素材库（本地操作，不依赖模型）。"""
        if self._store is None:
            return
        from ...core import drama_library

        picked = self._selected_assets()
        if not picked:
            self.status.setText("先选中要入库的资产行")
            return
        ok: list[str] = []
        failed: list[str] = []
        for asset in picked:
            name = asset.get("name") or asset.get("id") or "资产"
            try:
                entry_id = drama_library.donate(self._store, asset)
                ok.append(f"{name}（{entry_id}）")
            except (ValueError, OSError) as exc:
                failed.append(f"{name}：{exc}")
        lines = []
        if ok:
            lines.append(f"已入库 {len(ok)} 项：" + "、".join(ok[:6])
                         + ("…" if len(ok) > 6 else ""))
        if failed:
            lines.append(f"{len(failed)} 项未能入库：")
            lines.extend(f"  · {item}" for item in failed[:6])
        QMessageBox.information(self, "存入公用库", "\n".join(lines))
        self.status.setText(f"公用库：入库 {len(ok)} 项" + (f"，失败 {len(failed)}" if failed else ""))

    def save(self, silent: bool = False) -> None:
        if self._store is None:
            return
        old = {a["id"]: a for a in self._store.load_assets()}
        assets: list[dict] = []
        for row in range(self.table.rowCount()):
            asset: dict = {}
            for col, (key, _label) in enumerate(ASSET_COLUMNS):
                item = self.table.item(row, col)
                asset[key] = item.text().strip() if item else ""
            if any(asset.values()):
                # 表格里没有的字段（entity/image）从原记录保留，避免保存时丢关联
                assets.append({**old.get(asset.get("id", ""), {}), **asset})
        self._store.save_assets(assets)
        if not silent:
            self.status.setText(f"已保存 {len(assets)} 个资产")


class _PipelineCard(QFrame):
    """制作流水线主轴：三个主按钮 + 阶段状态灯 + 失败重试 + 云端任务认领。

    UI 只露这条主轴，复杂度在背后：单步工具收在「工作流模式」的高级里，
    分镜次要列收在「全部字段」里，后期参数是表单。失败项可只重跑失败的。
    """

    task_requested = Signal(str, str)      # (task, scope)
    retry_requested = Signal(str, list)    # (stage, target_ids)
    recover_requested = Signal()

    _STAGE_LABELS = {"asset_images": "设定图", "frame_images": "首尾帧", "video": "视频片段",
                     "storyboard": "分镜", "keyframes": "首尾帧说明"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pipelineCard")
        self._store = None
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 10, 20, 10)
        row.setSpacing(10)

        self.btn_script = QPushButton("① 生成剧本")
        self.btn_script.setToolTip("整理改编圣经 → 智能分集 → 分镜 → 提取资产；已完成的自动跳过")
        self.btn_script.clicked.connect(lambda: self.task_requested.emit("full", "all"))
        row.addWidget(self.btn_script)
        self.btn_assets = QPushButton("② 生成素材")
        self.btn_assets.setToolTip("人物定妆+全身三视图、场景 360° 环景、道具三视图；逐张审核确认")
        self.btn_assets.clicked.connect(lambda: self.task_requested.emit("asset_images", "current"))
        row.addWidget(self.btn_assets)
        self.btn_video = QPushButton("③ 生成本集视频")
        self.btn_video.setToolTip("首尾帧 → 逐镜视频 → 后期 → 复核；审核闸门处会停下来确认")
        self.btn_video.clicked.connect(lambda: self.task_requested.emit("production", "current"))
        row.addWidget(self.btn_video)

        row.addStretch()
        self.stage_labels: dict[str, QLabel] = {}
        for key, label in (("script", "剧本"), ("assets", "素材"), ("video", "视频")):
            chip = QLabel("—")
            chip.setStyleSheet("font-size: 11px; color: palette(placeholderText);")
            self.stage_labels[key] = chip
            row.addWidget(chip)

        self.btn_recover = QPushButton("查云端任务")
        self.btn_recover.setToolTip("Seedance 提交后中断的任务：查状态、认领已完成的片段，不重复烧钱")
        self.btn_recover.clicked.connect(self.recover_requested.emit)
        row.addWidget(self.btn_recover)

        self.fail_label = QLabel("")
        self.fail_label.setWordWrap(True)
        row.addWidget(self.fail_label, 1)
        self.btn_retry = QPushButton("重试失败项")
        self.btn_retry.setToolTip("只重跑失败的目标；已成功的靠增量跳过，不会重做")
        self._retry_menu = QMenu(self.btn_retry)
        self.btn_retry.setMenu(self._retry_menu)
        self.btn_retry.setEnabled(False)
        row.addWidget(self.btn_retry)

    def set_store(self, store) -> None:
        self._store = store

    def refresh(self) -> None:
        store = self._store
        if store is None:
            for chip in self.stage_labels.values():
                chip.setText("—")
                chip.setStyleSheet("font-size: 11px; color: palette(placeholderText);")
            self.fail_label.setText("")
            self.btn_retry.setEnabled(False)
            return
        episodes = store.load_episodes()
        scripts = sum(store.has_script(int(e["number"])) for e in episodes)
        boards = [store.load_storyboard(int(e["number"])) for e in episodes]
        ready = sum(bool(b["shots"]) for b in boards)
        stale = sum(bool(b.get("script_changed")) for b in boards)
        script_chip = self.stage_labels["script"]
        if episodes and scripts == len(episodes) and ready == len(episodes) and not stale:
            self._set_chip(script_chip, f"剧本 {scripts}/{len(episodes)} · 分镜齐", "ok")
        elif scripts or ready:
            self._set_chip(script_chip, f"剧本 {scripts}/{len(episodes)} · 分镜 {ready}", "partial")
        else:
            self._set_chip(script_chip, "剧本 未开始", "todo")

        report_assets = store.load_assets()
        total = len(report_assets)
        missing = sum(1 for a in report_assets if not a.get("image"))
        incomplete = 0
        for a in report_assets:
            # 轻量统计（只数文件齐不齐），不跑全量体检——导航每次都会刷新这张卡
            if a["kind"] == "character":
                need = len(MAKEUP_VIEWS) + len(FULLBODY_VIEWS)
                have = len(store.makeup_paths(a)) + len(store.fullbody_paths(a))
            elif a["kind"] == "location":
                need, have = len(SCENE_VIEWS), len(store.scene_paths(a))
            elif a["kind"] == "prop":
                need, have = len(PROP_VIEWS), len(store.prop_paths(a))
            else:
                continue
            if have < need:
                incomplete += 1
        assets_chip = self.stage_labels["assets"]
        if total and not missing and not incomplete:
            self._set_chip(assets_chip, f"素材 {total} 全齐", "ok")
        elif total:
            self._set_chip(assets_chip, f"素材 {total} · 待生成 {max(missing, incomplete)}", "partial")
        else:
            self._set_chip(assets_chip, "素材 未登记", "todo")

        current = next((int(b["episode"]) for b in reversed(boards) if b["shots"]), 0)
        video_chip = self.stage_labels["video"]
        if not current:
            self._set_chip(video_chip, "视频 —", "todo")
        else:
            shots = store.load_storyboard(current)["shots"]
            have_kf = sum(1 for s in shots
                          if store.keyframe_image_path(current, s["shot_id"], "first").is_file())
            clips_path = store.videos_dir / f"ep{current:03d}_clips.json"
            done = 0
            try:
                import json as _json
                done = len(_json.loads(clips_path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                done = 0
            if store.episode_video_path(current).is_file():
                self._set_chip(video_chip, f"第{current}集 成片", "ok")
            elif done >= len(shots) and done:
                self._set_chip(video_chip, f"第{current}集 片段齐，待合成", "partial")
            elif have_kf or done:
                self._set_chip(video_chip, f"第{current}集 首尾帧 {have_kf}/{len(shots)}", "partial")
            else:
                self._set_chip(video_chip, f"第{current}集 未开始", "todo")
        self._refresh_failures()

    @staticmethod
    def _set_chip(chip: QLabel, text: str, state: str) -> None:
        colors = {"ok": "#2ea043", "partial": "#d29922", "todo": "palette(placeholderText)"}
        chip.setText("● " + text)
        chip.setStyleSheet(f"font-size: 11px; color: {colors.get(state, colors['todo'])};")

    def _refresh_failures(self) -> None:
        from ...core.drama_records import load_failures

        items = load_failures(self._store) if self._store is not None else []
        menu = self._retry_menu
        menu.clear()
        if not items:
            self.fail_label.setText("")
            self.btn_retry.setEnabled(False)
            return
        by_stage: dict[str, list[dict]] = {}
        for item in items:
            by_stage.setdefault(str(item.get("stage") or ""), []).append(item)
        parts = []
        for stage, stage_items in by_stage.items():
            label = self._STAGE_LABELS.get(stage, stage)
            parts.append(f"{label} {len(stage_items)} 项")
            ids = [str(i.get("target") or "") for i in stage_items]
            action = menu.addAction(f"重试{label}（{len(stage_items)} 项）")
            action.triggered.connect(
                lambda _checked=False, s=stage, i=ids: self.retry_requested.emit(s, i)
            )
        self.fail_label.setText("⚠ " + " · ".join(parts))
        self.btn_retry.setEnabled(True)


class DramaWorkspace(QWidget):
    """短剧大页签的主工作区；依附当前小说项目，独立于小说正文页。"""

    status_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dramaWorkspace")
        self.project: Project | None = None
        self.store: DramaStore | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("dramaNav")
        self.nav.setFixedWidth(148)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for text in ("企划", "剧本", "分镜", "资产"):
            item = QListWidgetItem(text)
            self.nav.addItem(item)
        root.addWidget(self.nav)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        header = QWidget()
        header.setObjectName("editorToolbar")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 10, 20, 10)
        self.title_label = QLabel("短剧")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        hl.addWidget(self.title_label)
        self.badge = QLabel("短剧 · 依附当前小说")
        self.badge.setStyleSheet(
            "font-size: 11px; padding: 1px 8px; border-radius: 8px;"
            " background: palette(alternateBase); color: palette(placeholderText);"
        )
        hl.addWidget(self.badge)
        hl.addStretch()
        self.btn_save_all = QPushButton("保存全部")
        self.btn_save_all.setObjectName("primary")
        self.btn_save_all.clicked.connect(lambda: self.save_all())
        hl.addWidget(self.btn_save_all)
        self.btn_check = QPushButton("检查剧集")
        self.btn_check.clicked.connect(self._check_episodes)
        hl.addWidget(self.btn_check)
        self.btn_export = QPushButton("导出整季剧本")
        self.btn_export.clicked.connect(self._export_scripts)
        hl.addWidget(self.btn_export)
        rv.addWidget(header)

        self.overview = QLabel("打开小说项目，开始短剧改编")
        self.overview.setWordWrap(True)
        self.overview.setContentsMargins(20, 10, 20, 10)
        rv.addWidget(self.overview)

        # 制作流水线主轴：三个主按钮 + 阶段状态 + 失败重试
        self.pipeline = _PipelineCard()
        self.pipeline.task_requested.connect(self._start_pipeline_task)
        self.pipeline.retry_requested.connect(self._retry_pipeline_failures)
        self.pipeline.recover_requested.connect(self._recover_pipeline_cloud)
        rv.addWidget(self.pipeline)

        self.pages = QStackedWidget()
        self.series_page = _SeriesPage()
        self.script_page = _ScriptPage()
        self.storyboard_page = _StoryboardPage()
        self.assets_page = _AssetsPage()
        for page in (self.series_page, self.script_page, self.storyboard_page, self.assets_page):
            self.pages.addWidget(page)
        rv.addWidget(self.pages, 1)
        root.addWidget(right, 1)

        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.setCurrentRow(0)
        self.set_project(None)
        self._apply_style()

    # ── 主题 ──

    @staticmethod
    def _theme_colors() -> dict:
        cfg = load_config()
        name = cfg.get("theme", "dark")
        return get_theme_colors(name, cfg if name == "custom" else None)

    def _apply_style(self) -> None:
        t = self._theme_colors()
        self.setStyleSheet(
            f"QTableWidget {{ background: {t['surface']}; color: {t['fg']};"
            f" gridline-color: {t['border']}; border: none; font-size: 12px;"
            " alternate-background-color: transparent; }"
            "QTableWidget::item { padding: 3px 6px; }"
            f"QTableWidget::item:selected {{ background: {t['accent_bg']}; color: {t['accent']}; }}"
            f"QHeaderView::section {{ background: {t['elevated']}; color: {t['fg2']};"
            f" border: none; border-right: 1px solid {t['border']};"
            f" border-bottom: 1px solid {t['border']}; padding: 6px 8px; font-size: 11px; }}"
            f"QListWidget#dramaNav {{ background: {t['surface']}; color: {t['fg']};"
            f" border: none; border-right: 1px solid {t['border']}; padding: 8px; font-size: 13px; }}"
            "QListWidget#dramaNav::item { padding: 10px 12px; border-radius: 8px; margin: 1px 2px; }"
            f"QListWidget#dramaNav::item:selected {{ background: {t['accent_bg']}; color: {t['accent']}; }}"
            f"QPlainTextEdit {{ background: {t['surface']}; color: {t['fg']};"
            f" border: 1px solid {t['border']}; border-radius: 10px; padding: 12px; font-size: 13px; }}"
        )

    def refresh_theme(self) -> None:
        self._apply_style()

    # ── 项目装载 ──

    def set_project(self, project: Project | None) -> None:
        # 先解除旧项目引用并停止延迟保存，卸载后不允许定时器继续写旧项目。
        for page in (self.script_page, self.storyboard_page):
            page._autosave.stop()
            page._current = 0
        for page in (self.series_page, self.script_page, self.storyboard_page, self.assets_page):
            page._store = None
        self.project = project
        if project is None or project.project_dir is None:
            self.store = None
            self.title_label.setText("短剧")
            self.nav.setEnabled(False)
            self.btn_save_all.setEnabled(False)
            self.btn_check.setEnabled(False)
            self.btn_export.setEnabled(False)
            self.overview.setText("打开小说项目，开始短剧改编：企划 → 剧本 → 分镜 → 资产")
            for page in (self.series_page, self.script_page, self.storyboard_page, self.assets_page):
                page.setEnabled(False)
            self.pipeline.set_store(None)
            self.pipeline.refresh()
            self.pipeline.setEnabled(False)
            return
        self.store = DramaStore(project.project_dir)
        self.store.ensure({
            "title": project.title,
            "genre": project.genre,
            "style": project.style,
            "target_chapters": project.target_chapters,
            "synopsis": project.synopsis,
        })
        self.title_label.setText(project.title or "短剧")
        self.nav.setEnabled(True)
        self.btn_save_all.setEnabled(True)
        self.btn_check.setEnabled(True)
        self.btn_export.setEnabled(True)
        for page in (self.series_page, self.script_page, self.storyboard_page, self.assets_page):
            page.setEnabled(True)
        self.pipeline.setEnabled(True)
        self.series_page.load(self.store, project)
        self.script_page.load(self.store)
        self.storyboard_page.load(self.store)
        self.assets_page.load(self.store)
        # 装载过程中不要触发导航保存，否则会把旧页面的内容写进新项目
        self.nav.blockSignals(True)
        self.nav.setCurrentRow(0)
        self.nav.blockSignals(False)
        self.pages.setCurrentIndex(0)
        self._refresh_overview()

    def _refresh_overview(self) -> None:
        if self.store is None:
            self.pipeline.set_store(None)
            self.pipeline.refresh()
            return
        episodes = self.store.load_episodes()
        scripts = sum(self.store.has_script(e["number"]) for e in episodes)
        boards = [self.store.load_storyboard(e["number"]) for e in episodes]
        ready = sum(bool(b["shots"]) for b in boards)
        stale = sum(bool(b["script_changed"]) for b in boards)
        series = self.store.load_series()
        pending = self.store.adaptation_state().get("pending_chapters", [])
        pending_text = (" · 待用章节 " + "、".join(map(str, pending[:8])) + ("…" if len(pending) > 8 else "")) if pending else ""
        self.overview.setText(
            f"{series['platform']} · {series['aspect']} · 每集 {series['episode_seconds']} 秒"
            f"    剧本 {scripts}/{len(episodes)} 集 · 分镜 {ready} 集 · 待更新 {stale} 集{pending_text}"
        )
        self.overview.setToolTip(self.store.adaptation_state().get("carry_reason", ""))
        self.pipeline.set_store(self.store)
        self.pipeline.refresh()

    # ── 流水线主轴 ──

    def refresh_pipeline(self) -> None:
        self.pipeline.refresh()

    def _pipeline_task_guard(self, task: str) -> bool:
        """流水线按钮和执行模式走同一套前置检查：企划合法 + 需要模型时已配置。"""
        if self.store is None:
            return False
        error = self.series_page.validation_error()
        if error:
            QMessageBox.warning(self, "企划配置无效", error)
            return False
        from .drama_worker import NEEDS_LLM_TASKS

        if task in NEEDS_LLM_TASKS and getattr(self.window(), "llm", None) is None:
            QMessageBox.warning(
                self, "未配置模型",
                "该任务需要调用大模型。\n请先到「模型与智能体」里配置 API Key / 模型。",
            )
            return False
        return True

    def _start_pipeline_task(self, task: str, scope: str) -> None:
        if not self._pipeline_task_guard(task):
            return
        window = self.window()
        if hasattr(window, "_start_drama_worker"):
            window._start_drama_worker(task, scope)

    @staticmethod
    def _episode_from_targets(ids: list) -> int:
        """从镜号（E05-S003）里取集号；取不到返回 0。"""
        import re as _re

        numbers = set()
        for target in ids:
            m = _re.match(r"E(\d+)-S\d+", str(target))
            if m:
                numbers.add(int(m.group(1)))
        return numbers.pop() if len(numbers) == 1 else 0

    def _retry_pipeline_failures(self, stage: str, ids: list) -> None:
        """重试只打失败目标：素材按资产、分镜/首尾帧说明按集、首尾帧图/片段按镜。"""
        plans = {
            "asset_images": ("asset_images", "current", {"retry_assets": ids}, 0),
            "frame_images": ("frame_images", "current", {"retry_shots": ids},
                             self._episode_from_targets(ids)),
            "video": ("video", "all", {"retry_shots": ids},
                      self._episode_from_targets(ids)),
            "storyboard": ("storyboard", "current", {"retry_episodes": ids}, 0),
            "keyframes": ("keyframes", "current", {"retry_episodes": ids}, 0),
        }
        plan = plans.get(stage)
        if plan is None:
            return
        task, scope, kwargs, episode = plan
        if not self._pipeline_task_guard(task):
            return
        window = self.window()
        if hasattr(window, "_start_drama_worker"):
            window._start_drama_worker(task, scope, current_episode=episode or None, **kwargs)

    def _recover_pipeline_cloud(self) -> None:
        window = self.window()
        if hasattr(window, "_start_drama_worker"):
            window._start_drama_worker("video_recover", "all")

    def _check_episodes(self) -> None:
        if self.store is None:
            return
        self.save_all(silent=True)
        lines = []
        for ep in self.store.load_episodes():
            report = self.store.episode_report(ep["number"])
            issues = "；".join(report["issues"]) or "结构与时长检查通过"
            lines.append(f"第{ep['number']}集 · {report['scenes']} 场 / {report['shots']} 镜：{issues}")
        QMessageBox.information(self, "剧集检查", "\n\n".join(lines) or "还没有剧集，请先新增一集。")

    def _export_scripts(self) -> None:
        if self.store is None:
            return
        self.save_all(silent=True)
        try:
            path = self.store.export_scripts()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.status_message.emit(f"剧本已导出：{path}")
        QMessageBox.information(self, "导出整季剧本", f"已保存到：\n{path}")

    def save_all(self, silent: bool = False) -> None:
        if self.store is None:
            return
        # 只写 drama/ 下的短剧数据；小说 meta 由 ProjectMixin._save_project 负责，
        # 避免剧名/集数反向覆盖小说项目。
        self.series_page.save(silent=True)
        self.script_page.save(silent=True)
        self.storyboard_page.save(silent=True)
        self.assets_page.save(silent=True)
        self._refresh_overview()
        if not silent:
            self.status_message.emit("短剧工作区已保存")

    # ── 导航 ──

    def _on_nav_changed(self, index: int) -> None:
        if index < 0:
            return
        if self.store is not None:
            # 保存离开的页面，再刷新进入的页面
            if self.pages.currentIndex() == 1:
                self.script_page.save(silent=True)
            elif self.pages.currentIndex() == 2:
                self.storyboard_page.save(silent=True)
            elif self.pages.currentIndex() == 3:
                self.assets_page.save(silent=True)
            elif self.pages.currentIndex() == 0:
                self.series_page.save(silent=True)
        self.pages.setCurrentIndex(index)
        if self.store is None:
            return
        if index == 0:
            self.series_page.load(self.store, self.project)
        elif index == 1:
            self.script_page.load(self.store, keep=self.script_page._current)
        elif index == 2:
            self.storyboard_page.load(
                self.store, keep=self.script_page._current or self.storyboard_page._current
            )
        elif index == 3:
            self.assets_page.load(self.store)
        self._refresh_overview()
