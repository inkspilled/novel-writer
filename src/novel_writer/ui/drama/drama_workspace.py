"""短剧工作区 — 小说项目下的独立大页签：企划 / 剧本 / 分镜 / 资产。

短剧不单独建项目，数据写在当前小说的 drama/ 目录；但页面和小说正文/规划/世界
完全分开，右侧还有自己的短剧工作流入口。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QSplitter, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...core.drama import (
    ASSET_COLUMNS, BIBLE_FILE, DramaStore, SHOT_COLUMNS, new_shot,
)
from ...core.app_config import load_config
from ...core.logger import get_logger
from ...designsystem import get_theme_colors
from ...models.project import Project

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

        root = QVBoxLayout(self)
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
        bible_path = store.root / BIBLE_FILE
        try:
            self.bible_edit.setPlainText(
                bible_path.read_text(encoding="utf-8") if bible_path.exists() else ""
            )
        except OSError:
            self.bible_edit.setPlainText("")
        self.status.setText("")

    def save(self, silent: bool = False) -> dict:
        if self._store is None:
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
        }
        series = self._store.save_series(data)
        try:
            (self._store.root / BIBLE_FILE).write_text(
                self.bible_edit.toPlainText(), encoding="utf-8"
            )
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
        self._extra_keys = {"sfx", "transition", "notes"}

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
        self.btn_all_fields.setToolTip("显示/隐藏音效、转场、备注三列")
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
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table, 1)

        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(900)
        self._autosave.timeout.connect(lambda: self.save(silent=True))

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
        self._loading = False
        self._comfy_worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel("资产")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(title)
        hint = QLabel(
            "资产与小说正典对应：人物取自角色卡，地点/道具取自世界状态；"
            "「从小说导入」补登记，「生成提示词」按人物设定+题材风格拼提示词，"
            "「生成图片」调 ComfyUI 出参考图，写到 assets/generated/。"
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
        self.btn_prompts.clicked.connect(self._fill_prompts)
        bar.addWidget(self.btn_prompts)
        self.btn_gen = QPushButton("生成图片")
        self.btn_gen.setObjectName("primary")
        self.btn_gen.clicked.connect(self._generate_images)
        bar.addWidget(self.btn_gen)
        self.btn_add = QPushButton("＋ 资产")
        self.btn_add.clicked.connect(self._add_asset)
        bar.addWidget(self.btn_add)
        self.btn_del = QPushButton("删除")
        self.btn_del.setObjectName("danger")
        self.btn_del.clicked.connect(self._delete_assets)
        bar.addWidget(self.btn_del)
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
            self.table.setColumnWidth(idx, {"kind": 80, "id": 120, "name": 120,
                                             "prompt": 260, "ref": 180,
                                             "status": 70, "notes": 140}.get(key, 120))
        self.table.itemChanged.connect(lambda _: self.status.setText("编辑中…"))
        root.addWidget(self.table, 1)

    def load(self, store: DramaStore) -> None:
        self._store = store
        assets = store.load_assets()
        self._loading = True
        self.table.setRowCount(0)
        for asset in assets:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, (key, _label) in enumerate(ASSET_COLUMNS):
                _set_table_item(self.table, row, col, asset.get(key, ""))
        self._loading = False
        self.status.setText(f"{len(assets)} 个资产")

    def _add_asset(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = ["character", f"asset_{row + 1:03d}", "", "", "", "draft", ""]
        for col, value in enumerate(values):
            _set_table_item(self.table, row, col, value)
        self.status.setText("已新增一行，记得保存")

    # ── 与小说正典 / ComfyUI ──

    def _import_from_canon(self) -> None:
        if self._store is None:
            return
        added = self._store.sync_assets_from_canon()
        self.load(self._store)
        self.status.setText(
            f"从小说导入 {added} 个资产" if added else "人物/地点都已登记，没有新增"
        )

    def _fill_prompts(self) -> None:
        if self._store is None:
            return
        assets = self._store.load_assets()
        changed = 0
        for asset in assets:
            if not asset.get("prompt"):
                positive, _negative = self._store.asset_prompt(asset)
                asset["prompt"] = positive
                changed += 1
        if changed:
            self._store.save_assets(assets)
            self.load(self._store)
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

    def _generate_images(self) -> None:
        if self._store is None:
            return
        if self._comfy_worker is not None and self._comfy_worker.isRunning():
            self.status.setText("正在生成，等这一批结束")
            return
        from ...core.comfyui import config_from_app
        from .comfy_worker import ComfyImageWorker

        cfg = config_from_app()
        if not cfg.get("workflow"):
            QMessageBox.information(
                self, "ComfyUI",
                "还没配置工作流模板。\n请到「设置 → 模型设置 → 图像生成（ComfyUI）」"
                "选择一个 API 格式的 workflow.json。",
            )
            return
        self.save(silent=True)
        picked = self._selected_assets()
        if not picked:
            self.status.setText("没有可生成的资产（先「从小说导入」或从分镜提取）")
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
        self.status.setText(f"生成失败：{error[:160]}")

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
