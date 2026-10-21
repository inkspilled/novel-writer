"""故事线页 — 时间线 / 主线 / 支线 / 伏笔的独立 UI（世界菜单第四页）。

设计目标：一眼看清「写到哪了、哪些主线完成、哪些支线在推进、哪些伏笔该回收」。

布局：
    ┌ toolbar：标题 + 当前进度 + 同步/新增/删除 ────────────────┐
    │ 进度胶囊（当前第N章/目标） + 搜索 + 筛选                  │
    ├ 4 张 KPI 卡：时间线 / 主线 / 支线 / 伏笔（含进度条） ────┤
    ├ 线程树（按类型分组 → 按卷分组）─────────┬ 详情编辑区 ────┤
    └──────────────────────────────────────┴───────────────┘

数据来自 ``core.story_threads.StoryLedger``；双击线程跳转到对应章节。
"""
from __future__ import annotations

from pathlib import Path
import re

from PySide6.QtCore import QStringListModel, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QCompleter, QDialog, QDialogButtonBox,
    QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QHeaderView, QScrollArea, QSplitter, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ...core.logger import get_logger
from ...core.story_threads import (
    KIND_LABELS, STATUS_LABELS, ThreadKind, ThreadStatus,
    StoryLedger, infer_owner, sync_story_threads, volume_ordinal,
)
from ...designsystem import get_status_colors

logger = get_logger(__name__)

_KIND_ORDER = [ThreadKind.TIMELINE.value, ThreadKind.MAIN.value,
               ThreadKind.SUBPLOT.value, ThreadKind.FORESHADOW.value]
_KIND_ICONS = {
    ThreadKind.TIMELINE.value: "🕒",
    ThreadKind.MAIN.value: "🎯",
    ThreadKind.SUBPLOT.value: "🌿",
    ThreadKind.FORESHADOW.value: "🧩",
}
_STATUS_CHOICES = [ThreadStatus.PLANNED.value, ThreadStatus.ACTIVE.value,
                   ThreadStatus.DONE.value, ThreadStatus.ABANDONED.value]
_FILTERS = [
    ("all", "全部"),
    ("open", "未完成"),
    ("done", "已完成"),
    ("overdue", "伏笔超期"),
    ("planned", "未开始"),
    ("inactive", "已从规划移除"),
]


def _pill(text: str, tag: str) -> str:
    """状态胶囊 stylesheet（色值来自 designsystem 语义 token）。"""
    colors = get_status_colors(tag)
    return (
        f"color: {colors['fg']}; background: {colors['bg']};"
        "border-radius: 8px; padding: 1px 8px; font-size: 11px; font-weight: 600;"
    )


class _OwnerPicker(QDialog):
    """多选涉及人物（数据来自人物设定/世界状态/角色注册表）。"""

    def __init__(self, names: list[str], selected: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择涉及人物")
        self.resize(260, 360)
        layout = QVBoxLayout(self)
        self.list = QListWidget(self)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        chosen = set(selected)
        for name in names:
            item = QListWidgetItem(name)
            item.setSelected(name in chosen)
            self.list.addItem(item)
        layout.addWidget(self.list)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_names(self) -> list[str]:
        return [item.text() for item in self.list.selectedItems()]


class _KpiCard(QFrame):
    """一张进度卡：标题 + 大数字 + 细进度条 + 说明。"""

    clicked = Signal(str)  # kind

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setObjectName("kpiCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(128)
        self.setStyleSheet(
            "QFrame#kpiCard { background: palette(alternateBase);"
            " border: 1px solid palette(mid); border-radius: 12px; }"
            "QFrame#kpiCard:hover { border-color: palette(highlight); }"
            # 全局 QLabel 带不透明底色，卡片内必须透明，否则文字被底色盖住
            "QFrame#kpiCard QLabel { background: transparent; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.title = QLabel(f"{_KIND_ICONS.get(kind, '')} {KIND_LABELS.get(kind, kind)}")
        self.title.setStyleSheet("font-size: 12px; color: palette(text);")
        head.addWidget(self.title)
        head.addStretch()
        self.ratio = QLabel("—")
        self.ratio.setStyleSheet("font-size: 12px; font-weight: 700;")
        head.addWidget(self.ratio)
        layout.addLayout(head)

        self.value = QLabel("0 / 0")
        self.value.setStyleSheet("font-size: 20px; font-weight: 800;")
        layout.addWidget(self.value)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        layout.addWidget(self.bar)

        self.hint = QLabel("—")
        self.hint.setStyleSheet("font-size: 11px; color: palette(text);")
        self.hint.setMinimumHeight(22)
        layout.addWidget(self.hint)

    def set_data(self, done: int, total: int, percent: int, hint: str = "", tag: str = "active"):
        self.value.setText(f"{done} / {total}")
        self.ratio.setText(f"{percent}%")
        self.bar.setValue(percent)
        colors = get_status_colors(tag)
        self.bar.setStyleSheet(
            "QProgressBar { background: rgba(127,127,127,0.18); border: none; border-radius: 3px; }"
            f"QProgressBar::chunk {{ background: {colors['fg']}; border-radius: 3px; }}"
        )
        self.ratio.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {colors['fg']};")
        self.hint.setText(hint)

    def mousePressEvent(self, event):  # noqa: N802 — Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.kind)
        super().mousePressEvent(event)


class StorylinePage(QWidget):
    """世界菜单 → 故事线：四类线程的统一台账视图 + 编辑。"""

    chapter_activate_requested = Signal(int)   # 双击线程 → 跳章
    ledger_changed = Signal()                  # 台账被改动（外部可刷新）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("editorPanel")
        self._project_dir: Path | None = None
        self.ledger: StoryLedger | None = None
        self._current_id: str | None = None
        self._current_chapter = 0
        self._owner_names: list[str] = []
        self._volume_ranges: list[tuple[int, str, int, int]] = []
        self._build_ui()
        self.set_project_dir(None)

    # ── UI 构建 ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 工具栏 ──
        bar = QWidget()
        bar.setObjectName("editorToolbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(20, 10, 20, 10)
        bl.setSpacing(8)
        title = QLabel("故事线")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        bl.addWidget(title)
        self.step_label = QLabel("打开项目后管理")
        self.step_label.setStyleSheet("font-size: 12px; color: palette(placeholderText);")
        bl.addWidget(self.step_label)
        bl.addStretch()

        self.btn_jump = QPushButton("打开当前章")
        self.btn_jump.setToolTip("跳转到当前最新章节")
        self.btn_jump.clicked.connect(self._jump_current)
        self.btn_sync = QPushButton("同步台账")
        self.btn_sync.setToolTip(
            "重新解析 时间线/主线/支线/伏笔 四份规划文档，"
            "并与记忆、大世界状态对账（手工编辑优先，不会被覆盖）"
        )
        self.btn_sync.clicked.connect(lambda: self.sync(force=True))
        self.btn_add = QPushButton("＋ 新增")
        self.btn_add.clicked.connect(self._add)
        self.btn_del = QPushButton("删除")
        self.btn_del.setObjectName("danger")
        self.btn_del.clicked.connect(self._delete)
        for b in (self.btn_jump, self.btn_sync, self.btn_add, self.btn_del):
            bl.addWidget(b)
        root.addWidget(bar)

        # ── 概览行：进度胶囊 + 搜索 + 筛选 ──
        overview = QWidget()
        ol = QHBoxLayout(overview)
        ol.setContentsMargins(20, 4, 20, 8)
        ol.setSpacing(10)
        self.headline = QLabel("—")
        self.headline.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.headline.setWordWrap(True)
        ol.addWidget(self.headline, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索标题 / 详情 / 人物")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(lambda _: self.reload_tree())
        ol.addWidget(self.search)
        self.filter_combo = QComboBox()
        for key, label in _FILTERS:
            self.filter_combo.addItem(label, key)
        self.filter_combo.setFixedWidth(120)
        self.filter_combo.currentIndexChanged.connect(lambda _: self.reload_tree())
        ol.addWidget(self.filter_combo)
        root.addWidget(overview)

        # ── KPI 卡 ──
        cards = QWidget()
        cl = QHBoxLayout(cards)
        cl.setContentsMargins(20, 0, 20, 10)
        cl.setSpacing(10)
        self.cards: dict[str, _KpiCard] = {}
        for kind in _KIND_ORDER:
            card = _KpiCard(kind)
            card.clicked.connect(self._focus_kind)
            self.cards[kind] = card
            cl.addWidget(card, 1)
        root.addWidget(cards)

        # ── 主体：左树 + 右详情 ──
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["线程", "状态", "章节", "人物/备注"])
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(16)
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.setMinimumWidth(320)
        self._apply_tree_style()
        header = self.tree.header()
        header.setStretchLastSection(False)
        # 线程列吃掉剩余宽度；状态/章节/人物用固定宽度，避免长摘要/长人名把标题列挤成 0
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 104), (2, 104), (3, 140)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.tree.setColumnWidth(column, width)
        self.tree.currentItemChanged.connect(self._on_tree_selected)
        self.tree.itemDoubleClicked.connect(self._on_tree_activated)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        body.addWidget(self.tree)

        detail = QWidget()
        dv = QVBoxLayout(detail)
        dv.setContentsMargins(14, 10, 18, 12)
        dv.setSpacing(8)
        self.detail_title = QLabel("选择左侧一条线程查看/编辑")
        self.detail_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.detail_title.setWordWrap(True)
        dv.addWidget(self.detail_title)
        self.detail_meta = QLabel("")
        self.detail_meta.setStyleSheet("font-size: 11px; color: palette(placeholderText);")
        self.detail_meta.setWordWrap(True)
        dv.addWidget(self.detail_meta)

        self.form = QFormLayout()
        self.form.setSpacing(6)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        self.edit_title = QLineEdit()
        self.edit_title.setPlaceholderText("线程标题")
        self.form.addRow("标题", self.edit_title)
        self.edit_kind = QComboBox()
        for kind in _KIND_ORDER:
            self.edit_kind.addItem(KIND_LABELS[kind], kind)
        self.edit_kind.currentIndexChanged.connect(self._sync_chapter_hints)
        self.form.addRow("类型", self.edit_kind)
        self.edit_status = QComboBox()
        for status in _STATUS_CHOICES:
            self.edit_status.addItem(STATUS_LABELS[status], status)
        self.form.addRow("状态", self.edit_status)
        chapter_row = QHBoxLayout()
        self.edit_plant = QLineEdit()
        self.edit_plant.setPlaceholderText("埋设章")
        self.edit_plant.setFixedWidth(78)
        self.edit_resolve = QLineEdit()
        self.edit_resolve.setPlaceholderText("回收章")
        self.edit_resolve.setFixedWidth(78)
        self.btn_stamp = QToolButton()
        self.btn_stamp.setText("当前章")
        self.btn_stamp.setToolTip("把右侧章节设为当前写作章（标完成时也会自动补）")
        self.btn_stamp.clicked.connect(self._stamp_current_chapter)
        self.edit_plant.editingFinished.connect(self._autofill_group_from_chapter)
        self.edit_resolve.editingFinished.connect(self._autofill_group_from_chapter)
        chapter_row.addWidget(self.edit_plant)
        chapter_row.addWidget(self.edit_resolve)
        chapter_row.addWidget(self.btn_stamp)
        chapter_row.addStretch()
        self.form.addRow("章号", chapter_row)

        owner_row = QHBoxLayout()
        self.edit_owner = QLineEdit()
        self.edit_owner.setPlaceholderText("涉及人物（可多选，用、分隔）")
        self._owner_model = QStringListModel([], self)
        completer = QCompleter(self._owner_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.edit_owner.setCompleter(completer)
        self.btn_pick_owner = QToolButton()
        self.btn_pick_owner.setText("选择")
        self.btn_pick_owner.setToolTip("从人物设定 / 世界状态里选涉及人物")
        self.btn_pick_owner.clicked.connect(self._pick_owners)
        owner_row.addWidget(self.edit_owner, 1)
        owner_row.addWidget(self.btn_pick_owner)
        self.form.addRow("人物", owner_row)

        self.edit_group = QComboBox()
        self.edit_group.setEditable(True)
        self.edit_group.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if self.edit_group.lineEdit() is not None:
            self.edit_group.lineEdit().setPlaceholderText("所属卷 / 分组（可输入或选择）")
        self.form.addRow("卷/组", self.edit_group)
        self.edit_notes = QPlainTextEdit()
        self.edit_notes.setPlaceholderText("作者备注（只显示在这里，不进正文上下文）")
        self.edit_notes.setMaximumHeight(64)
        self.form.addRow("备注", self.edit_notes)
        self.edit_detail = QPlainTextEdit()
        self.edit_detail.setPlaceholderText("详细描述")
        self.edit_detail.setMaximumHeight(64)
        self.form.addRow("详情", self.edit_detail)
        dv.addLayout(self.form)
        self._sync_chapter_hints()

        actions = QHBoxLayout()
        self.btn_save = QPushButton("保存修改")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(self._save_detail)
        self.btn_jump_chapter = QPushButton("跳到该章")
        self.btn_jump_chapter.clicked.connect(self._jump_selected_chapter)
        self.btn_restore = QPushButton("恢复已移除")
        self.btn_restore.clicked.connect(self._restore_selected)
        actions.addWidget(self.btn_save)
        actions.addWidget(self.btn_jump_chapter)
        actions.addWidget(self.btn_restore)
        actions.addStretch()
        dv.addLayout(actions)
        self.detail_status = QLabel("")
        self.detail_status.setStyleSheet("font-size: 11px; color: palette(placeholderText);")
        dv.addWidget(self.detail_status)
        dv.addStretch()

        panel = QWidget()
        panel.setLayout(dv)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(panel)
        body.addWidget(scroll)
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 2)
        body.setSizes([700, 320])
        root.addWidget(body, 1)
        self._apply_responsive_columns()

        self._set_enabled(False)

    def resizeEvent(self, event):  # noqa: N802 — Qt 命名
        super().resizeEvent(event)
        if hasattr(self, "tree"):
            self._apply_responsive_columns()

    def _apply_responsive_columns(self):
        """窄窗口先收起「人物/备注」，把宽度让给线程标题。"""
        if not hasattr(self, "tree"):
            return
        self.tree.setColumnHidden(3, self.tree.width() < 520)

    # ── 主题 ──

    @staticmethod
    def _theme_colors() -> dict:
        """当前主题色（QSS 主题不写 QPalette，不能用 palette(base) 取色）。"""
        from ...core.app_config import load_config
        from ...designsystem import get_theme_colors
        cfg = load_config()
        name = cfg.get("theme", "dark")
        return get_theme_colors(name, cfg if name == "custom" else None)

    def _apply_tree_style(self):
        t = self._theme_colors()
        self.tree.setStyleSheet(
            f"QTreeWidget {{ background: {t['surface']}; color: {t['fg']};"
            " border: none; border-radius: 10px; padding: 4px; font-size: 13px; outline: none; }"
            "QTreeWidget::item { padding: 6px 10px; border-radius: 6px; margin: 1px 2px; }"
            f"QTreeWidget::item:selected {{ background: {t['accent_bg']}; color: {t['accent']}; }}"
            f"QHeaderView::section {{ background: {t['surface']}; color: {t['fg2']};"
            f" border: none; border-bottom: 1px solid {t['border']}; padding: 6px 8px; font-size: 11px; }}"
        )

    def refresh_theme(self):
        """主题切换后重刷树样式（MainWindow._apply_theme 调用）。"""
        self._apply_tree_style()

    def _set_enabled(self, on: bool):
        for w in (self.btn_jump, self.btn_sync, self.btn_add, self.btn_del,
                  self.search, self.filter_combo):
            w.setEnabled(on)
        self._set_detail_enabled(on and self._current_id is not None)

    def _set_detail_enabled(self, on: bool):
        for w in (self.edit_title, self.edit_kind, self.edit_status, self.edit_plant,
                  self.edit_resolve, self.edit_owner, self.edit_group, self.edit_notes,
                  self.edit_detail, self.btn_save, self.btn_jump_chapter,
                  self.btn_stamp, self.btn_pick_owner):
            w.setEnabled(on)
        self.btn_restore.setVisible(False)

    # ── 项目切换 / 刷新 ──

    def set_project_dir(self, project_dir: Path | None):
        self._project_dir = Path(project_dir) if project_dir else None
        self.ledger = None
        self._current_id = None
        if self._project_dir:
            self._set_enabled(True)
            self.sync(force=False)
        else:
            self._set_enabled(False)
            self.tree.clear()
            self.headline.setText("打开项目后管理")
            self.step_label.setText("打开项目后管理")
            self._clear_detail()

    def sync(self, force: bool = False):
        """与规划/记忆/世界状态对账；force=True 忽略文档签名强制重解析。"""
        if not self._project_dir:
            return
        try:
            self.ledger = sync_story_threads(self._project_dir, force_planning=force)
        except Exception as e:
            logger.error("故事线台账同步失败: %s", e, exc_info=True)
            self.detail_status.setText(f"同步失败：{e}")
            return
        self.refresh()

    def reload(self):
        """与规划/记忆/世界状态对账后重绘（WorldPage.reload 统一入口）。"""
        self.sync(force=False)

    def refresh(self):
        """仅重绘（不读写盘），用于工作流每次落盘后的轻量刷新。"""
        if not self._project_dir:
            return
        if self.ledger is None:
            self.ledger = StoryLedger(self._project_dir).load()
        self._refresh_field_choices()
        self.reload_tree()
        self._refresh_headline()
        self._refresh_cards()
        self._refresh_detail_from_ledger()

    def _refresh_field_choices(self):
        """人物/卷组改成真实数据源：可选、可输入、可推断。"""
        if not self.ledger:
            return
        self._owner_names = self.ledger.known_character_names()
        self._owner_model.setStringList(self._owner_names)
        self._volume_ranges = self.ledger.planning_volume_ranges()

        # 下拉只列「卷」级分组（规划稿里的章节小标题不进下拉，避免几十条噪声）；
        # 同一卷有「卷一 xx（1—50章）」和「卷一 xx」两种写法时只留台账那种。
        groups = sorted({
            t.group for t in self.ledger.all()
            if t.group and volume_ordinal(t.group)
        })
        seen_ordinals = {volume_ordinal(g) for g in groups if volume_ordinal(g)}
        for ordinal, name, _, _ in self._volume_ranges:
            if ordinal not in seen_ordinals:
                groups.append(name)
        current = self.edit_group.currentText()
        self.edit_group.blockSignals(True)
        self.edit_group.clear()
        seen: set[str] = set()
        for name in groups:
            if name and name not in seen:
                self.edit_group.addItem(name)
                seen.add(name)
        self.edit_group.setCurrentText(current)
        self.edit_group.blockSignals(False)

    def _infer_owner(self, thread) -> str:
        return infer_owner(f"{thread.title} {thread.detail}", self._owner_names)

    def _infer_group(self, chapter: int) -> str:
        """按章号猜所属卷；优先复用台账里已有的卷名。"""
        if not chapter or not self._volume_ranges:
            return ""
        for ordinal, name, start, end in self._volume_ranges:
            if not start or chapter < start:
                continue
            if end and chapter > end:
                continue
            same = [
                t.group for t in (self.ledger.all() if self.ledger else [])
                if t.group and volume_ordinal(t.group) == ordinal
            ]
            if same:
                from collections import Counter
                counts = Counter(same)
                return max(counts.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
            return name
        return ""

    def _refresh_headline(self):
        if not self.ledger:
            return
        progress = self.ledger.progress()
        self._current_chapter = progress.current_chapter
        self.headline.setText(progress.headline())
        last = (self.ledger.meta or {}).get("last_sync") or "—"
        self.step_label.setText(f"已同步 {last} · 共 {len(self.ledger.all())} 条线程")

    def _refresh_cards(self):
        if not self.ledger:
            return
        p = self.ledger.progress()
        self.cards[ThreadKind.TIMELINE.value].set_data(
            p.timeline_happened, p.timeline_total, p.timeline_percent,
            f"计划节点 {p.timeline_total} 个，已写 {p.current_chapter} 章",
            "ok" if p.timeline_percent >= 100 else "active",
        )
        self.cards[ThreadKind.MAIN.value].set_data(
            p.main_done, p.main_total, p.main_percent,
            "已完结主线节点" if p.main_total else "规划里还没有主线节点",
            "ok" if p.main_total and p.main_done == p.main_total else "active",
        )
        self.cards[ThreadKind.SUBPLOT.value].set_data(
            p.subplot_done, p.subplot_total, p.subplot_percent,
            "已收束支线" if p.subplot_total else "规划里还没有支线",
            "ok" if p.subplot_total and p.subplot_done == p.subplot_total else "active",
        )
        self.cards[ThreadKind.FORESHADOW.value].set_data(
            p.foreshadow_done, p.foreshadow_total, p.foreshadow_percent,
            (f"超期未回收 {p.foreshadow_overdue} 条" if p.foreshadow_overdue
             else ("全部伏笔已回收" if p.foreshadow_total and
                   p.foreshadow_done == p.foreshadow_total else "无超期（≥15章未回收）")), 
            "danger" if p.foreshadow_overdue else ("ok" if p.foreshadow_total and
                                                   p.foreshadow_done == p.foreshadow_total else "active"),
        )

    # ── 树 ──

    def reload_tree(self):
        if not self.ledger:
            self.tree.clear()
            return
        filter_key = self.filter_combo.currentData() if self.filter_combo.count() else "all"
        query = self.search.text().strip().lower()
        current_chapter = self._current_chapter or self.ledger.clock()[0]

        self.tree.blockSignals(True)
        self.tree.clear()
        for kind in _KIND_ORDER:
            items = self.ledger.all(kind, include_inactive=(filter_key == "inactive"))
            if filter_key != "inactive":
                items = [t for t in items if t.active]
            items = [t for t in items if self._match_filter(t, filter_key, current_chapter)
                     and self._match_query(t, query)]
            if not items:
                continue
            progress = self.ledger.progress()
            summary = self._kind_summary(kind, items, progress)
            # 摘要放进第一列并跨列：否则「已发生 41/60 · 当前第 10 章」会把状态列撑到 200px
            group = QTreeWidgetItem([
                f"{_KIND_ICONS.get(kind, '')} {KIND_LABELS[kind]} · {summary}", "", "", ""
            ])
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            group.setFirstColumnSpanned(True)
            if kind == ThreadKind.TIMELINE.value:
                group.setToolTip(0, "时间线：已发生 = 对应章节已经写了；计划中 = 还没写到")
            group.setData(0, Qt.ItemDataRole.UserRole, {"kind": kind, "group": True})
            self.tree.addTopLevelItem(group)

            # 按卷/分组做二级分组（时间线也按卷收拢，500 章平铺会找不到）
            buckets: dict[str, list] = {}
            for thread in items:
                buckets.setdefault(thread.group or "", []).append(thread)
            def bucket_key(item):
                name = item[0]
                ordinal = volume_ordinal(name)
                return (0, ordinal, name) if ordinal else (1, 0, name)

            for bucket, rows in sorted(buckets.items(), key=bucket_key):
                parent = group
                if bucket:
                    parent = QTreeWidgetItem([bucket, "", "", ""])
                    parent.setFirstColumnSpanned(True)
                    parent.setData(0, Qt.ItemDataRole.UserRole, {"kind": kind, "group": True})
                    group.addChild(parent)
                for thread in rows:
                    self._append_thread_row(parent, thread, current_chapter)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        # 重建后恢复选中（否则详情面板被清空）
        if self._current_id and not self._select_thread(self._current_id):
            self._current_id = None
            self._clear_detail()

    def _append_thread_row(self, parent, thread, current_chapter: int):
        status_text, tag = thread.ui_status(current_chapter)
        notes = thread.owner or thread.notes or ""
        row = QTreeWidgetItem([thread.title, status_text, thread.chapter_text(), notes])
        row.setData(0, Qt.ItemDataRole.UserRole,
                    {"kind": thread.kind, "id": thread.id, "group": False})
        colors = get_status_colors(tag)
        row.setForeground(1, QColor(colors["fg"]))
        font = row.font(1)
        font.setBold(True)
        row.setFont(1, font)
        row.setToolTip(0, thread.detail or thread.title)
        if not thread.active:
            row.setForeground(0, QColor(palette().color(QPalette.ColorRole.PlaceholderText)))
        if thread.owner:
            row.setToolTip(3, thread.owner)
        hint = f"{thread.kind_label} · {thread.status_label}"
        if thread.group:
            hint += f"\n卷/组：{thread.group}"
        if thread.owner:
            hint += f"\n人物：{thread.owner}"
        if thread.detail:
            hint += f"\n{thread.detail}"
        if thread.notes:
            hint += f"\n备注：{thread.notes}"
        row.setToolTip(0, hint)
        if isinstance(parent, QTreeWidgetItem):
            parent.addChild(row)
        else:  # QTreeWidget
            parent.addTopLevelItem(row)

    def _kind_summary(self, kind: str, items: list, progress) -> str:
        if kind == ThreadKind.FORESHADOW.value:
            return f"已回收 {progress.foreshadow_done}/{progress.foreshadow_total}（超期 {progress.foreshadow_overdue}）"
        if kind == ThreadKind.MAIN.value:
            return f"已完成 {progress.main_done}/{progress.main_total}"
        if kind == ThreadKind.SUBPLOT.value:
            return f"已完成 {progress.subplot_done}/{progress.subplot_total}"
        return f"已发生 {progress.timeline_happened}/{progress.timeline_total}"

    def _match_filter(self, thread, filter_key: str, current_chapter: int) -> bool:
        if filter_key == "open":
            return thread.active and thread.status != ThreadStatus.DONE.value \
                and thread.status != ThreadStatus.ABANDONED.value \
                and not (thread.kind == ThreadKind.TIMELINE.value and thread.happened)
        if filter_key == "done":
            return thread.status == ThreadStatus.DONE.value or (
                thread.kind == ThreadKind.TIMELINE.value and thread.happened)
        if filter_key == "overdue":
            return thread.is_overdue(current_chapter)
        if filter_key == "planned":
            return thread.status == ThreadStatus.PLANNED.value \
                and not (thread.kind == ThreadKind.TIMELINE.value and thread.happened)
        if filter_key == "inactive":
            return not thread.active
        return True

    @staticmethod
    def _match_query(thread, query: str) -> bool:
        if not query:
            return True
        haystack = " ".join((thread.title, thread.detail, thread.owner,
                             thread.group, thread.notes)).lower()
        return query in haystack

    # ── 选择 / 编辑 ──

    def _selected(self) -> dict | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        data = item.data(0, Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) and not data.get("group") else None

    def _on_tree_selected(self, current, _previous):
        if current is None:
            self._current_id = None
            self._clear_detail()
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or data.get("group"):
            self._current_id = None
            self._clear_detail()
            return
        self._current_id = data.get("id")
        self._load_detail()

    def _on_tree_activated(self, item, _column):
        if not isinstance(item, QTreeWidgetItem):
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or data.get("group"):
            return
        thread = (self.ledger.get(data.get("id", "")) if self.ledger else None)
        if thread is None:
            return
        target = thread.resolve_chapter or thread.planted_chapter
        if target:
            self.chapter_activate_requested.emit(target)

    def _focus_kind(self, kind: str):
        """点 KPI 卡：定位到该类型（展开分组 + 选中第一条）。"""
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            data = group.data(0, Qt.ItemDataRole.UserRole) or {}
            if data.get("kind") != kind:
                continue
            group.setExpanded(True)
            first = self._first_thread_item(group)
            if first is not None:
                self.tree.setCurrentItem(first)
                self.tree.scrollToItem(first)
            return

    def _first_thread_item(self, item: QTreeWidgetItem) -> QTreeWidgetItem | None:
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if not data.get("group"):
            return item
        for i in range(item.childCount()):
            found = self._first_thread_item(item.child(i))
            if found is not None:
                return found
        return None

    def _load_detail(self):
        thread = self.ledger.get(self._current_id) if (self.ledger and self._current_id) else None
        if thread is None:
            self._clear_detail()
            return
        self._set_detail_enabled(True)
        self.detail_title.setText(f"{_KIND_ICONS.get(thread.kind, '')} {thread.title}")
        self.detail_meta.setText(
            f"{thread.kind_label} · 来源：{self._source_label(thread.source)} · "
            f"更新于 {thread.updated_at or '—'}"
        )
        self.edit_title.setText(thread.title)
        self.edit_title.setCursorPosition(0)  # 长标题不要停在末尾，开头才是有效信息
        index = self.edit_kind.findData(thread.kind)
        if index >= 0:
            self.edit_kind.setCurrentIndex(index)
        index = self.edit_status.findData(thread.status)
        if index >= 0:
            self.edit_status.setCurrentIndex(index)
        self.edit_plant.setText(str(thread.planted_chapter or ""))
        self.edit_resolve.setText(str(thread.resolve_chapter or ""))
        self._sync_chapter_hints()

        # 空字段按正文/规划推断，先显示出来，保存时落盘（不改动已手工编辑的值）
        owner = thread.owner
        inferred_owner = False
        if not owner and "owner" not in (thread.user_edited or []):
            owner = self._infer_owner(thread)
            inferred_owner = bool(owner)
        self.edit_owner.setText(owner)

        group = thread.group
        inferred_group = False
        if not group and "group" not in (thread.user_edited or []):
            group = self._infer_group(thread.planted_chapter or thread.resolve_chapter)
            inferred_group = bool(group)
        self.edit_group.setCurrentText(group)

        self.edit_notes.setPlainText(thread.notes)
        self.edit_detail.setPlainText(thread.detail)
        self.btn_restore.setVisible(not thread.active)
        if inferred_owner or inferred_group:
            self.detail_status.setText("人物/卷组已按正文补全，点「保存修改」写入台账")
        elif thread.user_edited:
            self.detail_status.setText("手工改过的字段会被保护，后续自动对账不会覆盖")
        else:
            self.detail_status.setText("")

    def _sync_chapter_hints(self):
        """章号输入标题随类型变：伏笔是埋/收，时间线是发生/结束。"""
        kind = self.edit_kind.currentData()
        if kind == ThreadKind.TIMELINE.value:
            self.edit_plant.setPlaceholderText("发生章")
            self.edit_resolve.setPlaceholderText("结束章")
            self.edit_resolve.setToolTip("时间线一般只填发生章；跨章事件可填结束章")
        elif kind == ThreadKind.FORESHADOW.value:
            self.edit_plant.setPlaceholderText("埋设章")
            self.edit_resolve.setPlaceholderText("回收章")
            self.edit_resolve.setToolTip("伏笔回收章；标记完成时自动填当前章")
        else:
            self.edit_plant.setPlaceholderText("起始章")
            self.edit_resolve.setPlaceholderText("完成章")
            self.edit_resolve.setToolTip("节点完成章；标记完成时自动填当前章")

    def _autofill_group_from_chapter(self):
        """卷/组为空时，按刚填的章号补上，不让字段悬空。"""
        if self.edit_group.currentText().strip():
            return
        chapter = self._parse_chapter(self.edit_plant.text()) or self._parse_chapter(self.edit_resolve.text())
        group = self._infer_group(chapter)
        if group:
            self.edit_group.setCurrentText(group)

    def _stamp_current_chapter(self):
        chapter = self._current_chapter
        if not chapter and self.ledger:
            chapter = self.ledger.clock()[0]
        if not chapter:
            self.detail_status.setText("还没有已写章节，无法取当前章")
            return
        self.edit_resolve.setText(str(chapter))
        self.detail_status.setText(f"已填入当前章：第 {chapter} 章")

    def _pick_owners(self):
        if not self._owner_names:
            self.detail_status.setText("还没识别到角色（先写人物设定，或跑一章正文）")
            return
        selected = [s.strip() for s in re.split(r"[、,，/\s]+", self.edit_owner.text()) if s.strip()]
        dialog = _OwnerPicker(self._owner_names, selected, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.edit_owner.setText("、".join(dialog.selected_names()))

    def _refresh_detail_from_ledger(self):
        if self._current_id and self.ledger and self.ledger.get(self._current_id):
            self._load_detail()
        elif self._current_id:
            self._current_id = None
            self._clear_detail()

    def _clear_detail(self):
        self.detail_title.setText("选择左侧一条线程查看/编辑")
        self.detail_meta.setText("")
        self.detail_status.setText("")
        for w in (self.edit_title, self.edit_owner,
                  self.edit_plant, self.edit_resolve):
            w.clear()
        self.edit_group.setCurrentText("")
        self.edit_notes.clear()
        self.edit_detail.clear()
        self._set_detail_enabled(False)

    @staticmethod
    def _source_label(source: str) -> str:
        return {
            "planning": "规划文档", "world_state": "世界状态",
            "memory": "写后记忆", "manual": "手工",
        }.get(source or "", source or "—")

    def _save_detail(self):
        if not self.ledger or not self._current_id:
            return
        kind = self.edit_kind.currentData()
        status = self.edit_status.currentData()
        plant = self._parse_chapter(self.edit_plant.text())
        resolve = self._parse_chapter(self.edit_resolve.text())
        stamped = False

        # 标完成但没填回收/完成章：自动取当前写作章（时间线取发生章）
        if status == ThreadStatus.DONE.value and not resolve:
            current = self._current_chapter
            if not current:
                current = self.ledger.clock()[0]
            resolve = plant if kind == ThreadKind.TIMELINE.value and plant else (current or plant)
            if resolve:
                stamped = True
                self.edit_resolve.setText(str(resolve))

        chapters = {"plant": plant, "resolve": resolve}
        if kind == ThreadKind.TIMELINE.value and not plant and resolve:
            chapters["plant"] = resolve

        fields = {
            "title": self.edit_title.text().strip(),
            "kind": kind,
            "status": status,
            "owner": self.edit_owner.text().strip(),
            "group": self.edit_group.currentText().strip(),
            "notes": self.edit_notes.toPlainText().strip(),
            "detail": self.edit_detail.toPlainText().strip(),
            "chapters": chapters,
        }
        # 时间线的「已完成」要同时落 happened，否则树上仍显示「计划中」
        if kind == ThreadKind.TIMELINE.value:
            if status == ThreadStatus.DONE.value:
                fields["happened"] = True
            elif status == ThreadStatus.PLANNED.value:
                fields["happened"] = False

        if not fields["title"]:
            self.detail_status.setText("标题不能为空")
            return
        thread = self.ledger.get(self._current_id)
        if thread is None:
            return
        if kind and kind != thread.kind:
            thread.kind = kind
            thread.user_edited.append("kind")
        self.ledger.update(self._current_id, fields)
        self.ledger.save()
        # 先定好选中项再重建树：筛选条件可能让刚改的条目离开列表，重建后会清空详情
        self._current_id = thread.id
        self.reload_tree()
        self._refresh_headline()
        self._refresh_cards()
        if self._select_thread(thread.id):
            self._load_detail()
            self.detail_status.setText(
                f"已保存（回收/完成章自动填为第 {resolve} 章）" if stamped else "已保存"
            )
        else:
            self._clear_detail()
            self.detail_status.setText("已保存（当前筛选下不可见）")
        self.ledger_changed.emit()

    @staticmethod
    def _parse_chapter(text: str) -> int:
        from ...core.story_threads import extract_chapters
        refs = extract_chapters(text or "")
        if refs:
            return refs[0]
        try:
            return max(0, int((text or "").strip()))
        except ValueError:
            return 0

    def _select_thread(self, thread_id: str) -> bool:
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop(0)
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if not data.get("group") and data.get("id") == thread_id:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                return True
            stack.extend(item.child(i) for i in range(item.childCount()))
        return False

    # ── 新增 / 删除 / 恢复 / 跳章 ──

    def _add(self):
        if not self.ledger:
            return
        kind_labels = [KIND_LABELS[k] for k in _KIND_ORDER]
        label, ok = QInputDialog.getItem(self, "新增故事线", "类型", kind_labels, 0, False)
        if not ok:
            return
        kind = _KIND_ORDER[kind_labels.index(label)] if label in kind_labels else ThreadKind.SUBPLOT.value
        title, ok = QInputDialog.getText(self, f"新增{KIND_LABELS[kind]}", "标题")
        if not ok or not title.strip():
            return
        detail, ok = QInputDialog.getText(self, "详情（可留空）", "一句话描述")
        if not ok:
            detail = ""
        thread = self.ledger.add(kind, title.strip(), detail=detail.strip())
        if thread.kind in (ThreadKind.MAIN.value, ThreadKind.SUBPLOT.value, ThreadKind.FORESHADOW.value):
            thread.status = ThreadStatus.ACTIVE.value
        self.ledger.save()
        self.reload_tree()
        self._refresh_headline()
        self._refresh_cards()
        self._current_id = thread.id
        self._select_thread(thread.id)
        self._load_detail()
        self.ledger_changed.emit()

    def _delete(self):
        data = self._selected()
        if not data or not self.ledger:
            QMessageBox.information(self, "删除", "先在左侧选中一条线程")
            return
        thread = self.ledger.get(data.get("id", ""))
        if thread is None:
            return
        ret = QMessageBox.question(self, "删除", f"删除「{thread.title}」？")
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.ledger.remove(thread.id)
        self.ledger.save()
        self._current_id = None
        self._clear_detail()
        self.reload_tree()
        self._refresh_headline()
        self._refresh_cards()
        self.ledger_changed.emit()

    def _restore_selected(self):
        thread = self.ledger.get(self._current_id) if (self.ledger and self._current_id) else None
        if thread is None:
            return
        thread.active = True
        thread.updated_at = __import__("datetime").datetime.now().isoformat(timespec="seconds")
        self.ledger.save()
        self.reload_tree()
        self._refresh_headline()
        self._refresh_cards()
        self._select_thread(thread.id)
        self._load_detail()

    def _jump_selected_chapter(self):
        thread = self.ledger.get(self._current_id) if (self.ledger and self._current_id) else None
        if thread is None:
            return
        target = thread.resolve_chapter or thread.planted_chapter
        if target:
            self.chapter_activate_requested.emit(target)

    def _jump_current(self):
        if self._current_chapter:
            self.chapter_activate_requested.emit(self._current_chapter)

    def _on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get("group"):
            return
        thread = self.ledger.get(data.get("id", "")) if self.ledger else None
        if thread is None:
            return
        menu = QMenu(self)
        act_jump = menu.addAction("跳到该章")
        act_done = menu.addAction("标记已完成")
        act_active = menu.addAction("标记推进中")
        act_remove = menu.addAction("从台账删除")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_jump:
            self.tree.setCurrentItem(item)
            self._jump_selected_chapter()
        elif chosen in (act_done, act_active):
            status = ThreadStatus.DONE.value if chosen is act_done else ThreadStatus.ACTIVE.value
            self.ledger.update(thread.id, {"status": status})
            if status == ThreadStatus.DONE.value:
                self._stamp_resolve(thread)
            self.ledger.save()
            self.reload_tree()
            self._refresh_headline()
            self._refresh_cards()
            self._select_thread(thread.id)
            self._load_detail()
            self.ledger_changed.emit()
        elif chosen is act_remove:
            self._current_id = thread.id
            self._select_thread(thread.id)
            self._delete()

    def _stamp_resolve(self, thread):
        """标记完成时补上完成章（当前章），避免「已完成但没章号」。"""
        if thread.resolve_chapter:
            return
        current = self._current_chapter or self.ledger.clock()[0]
        if current:
            thread.chapters["resolve"] = current


__all__ = ["StorylinePage"]
