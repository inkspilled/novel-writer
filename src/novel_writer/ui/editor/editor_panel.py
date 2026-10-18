from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLabel, QPushButton,
    QTabWidget, QListWidget, QListWidgetItem,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import (
    QColor, QFont, QIcon, QPainter, QPen, QPixmap,
    QTextOption, QTextBlockFormat, QTextCursor,
)

from ...locales import t
from ...core import project_io
from ...core.logger import get_logger

logger = get_logger(__name__)


# planning/ 文档（按依赖顺序；含全书章节细纲）
PLANNING_DOCS = [
    ("立意", "planning/立意.md"),
    ("大纲", "planning/大纲.md"),
    ("章节细纲", "planning/章节细纲.md"),
    ("人物设定", "planning/人物设定.md"),
    ("世界观", "planning/世界观.md"),
    ("时间线", "planning/时间线.md"),
    ("主线", "planning/主线.md"),
    ("支线", "planning/支线.md"),
    ("伏笔", "planning/伏笔.md"),
]

# 规划文档静默自动保存间隔（防抖）
PLANNING_AUTOSAVE_MS = 1500


def _make_editor(placeholder: str = "") -> QTextEdit:
    """创建统一风格的编辑器（纸面阅读：侧边留白 + 舒适行距）。"""
    edit = QTextEdit()
    edit.setPlaceholderText(placeholder)
    edit.setAcceptRichText(False)
    edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    # 用窗口宽度换行，避免窄窗口出现横向滚动条；最大宽度限制阅读行长
    edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
    edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    import sys
    _font = "Microsoft YaHei" if sys.platform == "win32" else "PingFang SC"
    font = QFont(_font, 16)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    edit.setFont(font)
    edit.document().setDocumentMargin(28)  # 纸面内边距
    # 行距：QFont 无 setLineSpacing，用段落块格式（ProportionalHeight=150%）
    # 空文档直接 setDefaultCharFormat/不 select 全文，避免 QTextCursor::setPosition out of range
    block_fmt = QTextBlockFormat()
    block_fmt.setLineHeight(150.0, QTextBlockFormat.ProportionalHeight.value)
    cursor = QTextCursor(edit.document())
    cursor.mergeBlockFormat(block_fmt)  # 只作用于当前块，不 select Document
    edit.setTextCursor(cursor)
    return edit


def _planning_status_icon(exists: bool) -> QIcon:
    """列表状态图标：实心 ● = 已生成，空心 ○ = 未生成。"""
    pix = QPixmap(12, 12)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if exists:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2e9e5b"))
        painter.drawEllipse(2, 2, 8, 8)
    else:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#8e8e9a"), 1.4))
        painter.drawEllipse(2.5, 2.5, 7, 7)
    painter.end()
    return QIcon(pix)


class EditorPanel(QWidget):

    content_changed = Signal()
    save_requested = Signal()
    planning_save_requested = Signal(str)  # 发送 planning 文档名称

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("editorPanel")
        self._current_chapter = None  # 当前编辑的 Chapter 对象（勿用 number-1 当列表下标）
        self._project_dir: Path | None = None

        # 规划文档：单编辑器 + 内存缓存。
        # _planning_edits 仅为向后兼容保留（旧代码把它当 {label: edit} 用）。
        self._planning_edits: dict[str, QTextEdit] = {}
        self._planning_cache: dict[str, str] = {}
        self._planning_loaded: set[str] = set()
        self._planning_dirty: set[str] = set()
        self._planning_exists: dict[str, bool] = {label: False for label, _ in PLANNING_DOCS}
        self._planning_current: str | None = None
        self._planning_switch_guard = False

        self._planning_autosave = QTimer(self)
        self._planning_autosave.setSingleShot(True)
        self._planning_autosave.setInterval(PLANNING_AUTOSAVE_MS)
        self._planning_autosave.timeout.connect(self.flush_planning_autosave)
        self._setup_ui()

    # ── UI ──────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 两级导航：一级只有「正文 / 规划」，规划文档收进规划页左侧列表，
        # 避免 10 个标签横向溢出出现滚动箭头。
        self.tab_bar = QTabWidget()
        self.tab_bar.setDocumentMode(True)

        # ── 正文标签 ──
        chapter_page = QWidget()
        chapter_layout = QVBoxLayout(chapter_page)
        chapter_layout.setContentsMargins(0, 0, 0, 0)
        chapter_layout.setSpacing(0)

        # 正文工具栏
        toolbar = QWidget()
        toolbar.setObjectName("editorToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(20, 10, 20, 10)

        self.chapter_title = QLabel(t("editor_select_chapter"))
        self.chapter_title.setWordWrap(True)
        self.chapter_title.setStyleSheet("font-size: 18px; font-weight: 700; letter-spacing: -0.3px;")
        toolbar_layout.addWidget(self.chapter_title, 1)

        self.word_count_label = QLabel(f"0 {t('editor_words')}")
        self.word_count_label.setStyleSheet("font-size: 12px; font-weight: 500;")
        self.word_count_label.setMinimumWidth(60)
        toolbar_layout.addWidget(self.word_count_label)

        self.save_btn = QPushButton(t("editor_save"))
        self.save_btn.setObjectName("primary")
        self.save_btn.setFixedHeight(32)
        self.save_btn.setFixedWidth(72)
        self.save_btn.setStyleSheet("font-size: 12px; padding: 4px 12px;")
        self.save_btn.clicked.connect(self.save_requested.emit)
        toolbar_layout.addWidget(self.save_btn)

        chapter_layout.addWidget(toolbar)

        self.content_edit = _make_editor(t("editor_ph_content"))
        self.content_edit.textChanged.connect(self._on_text_changed)
        chapter_layout.addWidget(self.content_edit, 1)

        self.tab_bar.addTab(chapter_page, t("editor_tab_content"))

        # ── 规划标签：左列表 + 右共享编辑器 ──
        planning_page = QWidget()
        planning_layout = QHBoxLayout(planning_page)
        planning_layout.setContentsMargins(0, 0, 0, 0)
        planning_layout.setSpacing(0)

        self.planning_list = QListWidget()
        self.planning_list.setObjectName("planningList")
        self.planning_list.setFixedWidth(152)
        self.planning_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        planning_layout.addWidget(self.planning_list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # 规划页共享工具栏：当前文档名 + 状态 + 保存
        ptoolbar = QWidget()
        ptoolbar.setObjectName("editorToolbar")
        ptoolbar_layout = QHBoxLayout(ptoolbar)
        ptoolbar_layout.setContentsMargins(20, 10, 20, 10)

        self.planning_status_label = QLabel()
        self.planning_status_label.setStyleSheet("font-size: 12px; color: palette(placeholderText);")
        ptoolbar_layout.addWidget(self.planning_status_label, 1)

        self.planning_save_btn = QPushButton(t("editor_save"))
        self.planning_save_btn.setObjectName("primary")
        self.planning_save_btn.setFixedHeight(32)
        self.planning_save_btn.setFixedWidth(72)
        self.planning_save_btn.setStyleSheet("font-size: 12px; padding: 4px 12px;")
        self.planning_save_btn.clicked.connect(self._emit_planning_save)
        ptoolbar_layout.addWidget(self.planning_save_btn)

        right_layout.addWidget(ptoolbar)

        self.planning_edit = _make_editor()
        self.planning_edit.textChanged.connect(self._on_planning_edited)
        right_layout.addWidget(self.planning_edit, 1)

        planning_layout.addWidget(right, 1)
        self.tab_bar.addTab(planning_page, "规划")

        layout.addWidget(self.tab_bar)

        # 列表项只放文档名 + 状态点；以后新增规划文档只需扩 PLANNING_DOCS。
        for label, _rel_path in PLANNING_DOCS:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, label)
            self.planning_list.addItem(item)
            self._planning_edits[label] = self.planning_edit

        # 先填充/刷新再接线，避免构造期间的 currentChanged 回调读到半成品控件。
        self._refresh_planning_states()
        self._select_planning_row(0)
        self.planning_list.currentRowChanged.connect(self._on_planning_row_changed)
        self.tab_bar.currentChanged.connect(self._on_tab_changed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_editor_margins()

    def _apply_editor_margins(self):
        """宽窗口用左右 viewport margin 把正文夹到 ~920px，窄窗口自动贴边。"""
        for edit in (self.content_edit, self.planning_edit):
            try:
                side = max(0, (edit.width() - 920) // 2)
                edit.setViewportMargins(side, 0, side, 0)
            except Exception:
                pass

    # ── 规划文档状态 ────────────────────────────────────────────────────

    def _rel_path(self, label: str) -> str | None:
        return next((p for n, p in PLANNING_DOCS if n == label), None)

    def _refresh_planning_states(self):
        """按磁盘文件是否存在，刷新列表状态点和当前文档的只读/按钮状态。"""
        for index, (label, rel_path) in enumerate(PLANNING_DOCS):
            exists = False
            if self._project_dir:
                fpath = self._project_dir / rel_path
                try:
                    exists = fpath.exists() and fpath.stat().st_size > 0
                except OSError:
                    exists = False
            self._planning_exists[label] = exists

            item = self.planning_list.item(index)
            if item is not None:
                # 文档名保持纯文本（便于按名字检索），状态用 ●/○ 图标表达。
                item.setText(label)
                item.setIcon(_planning_status_icon(exists))
                item.setToolTip(f"{label} · {'已生成' if exists else '未生成'}")

        if self._planning_current:
            self._apply_planning_doc_state(self._planning_current)
            # 文件在外部被删/被清空：不能继续展示过期正文。
            if not self._planning_exists.get(self._planning_current, False):
                self._planning_cache.pop(self._planning_current, None)
                self._planning_loaded.discard(self._planning_current)
                self.planning_edit.blockSignals(True)
                self.planning_edit.clear()
                self.planning_edit.blockSignals(False)
        else:
            # 没有选中项：清空共享编辑器，等用户从左侧选择。
            self._set_planning_read_only(True)
            self.planning_status_label.setText("")
            self.planning_save_btn.setEnabled(False)
            self.planning_edit.setPlaceholderText("从左侧选择规划文档")
            self.planning_edit.blockSignals(True)
            self.planning_edit.clear()
            self.planning_edit.blockSignals(False)

    def _set_planning_read_only(self, read_only: bool):
        self.planning_edit.setReadOnly(read_only)
        if read_only:
            self.planning_edit.setStyleSheet("QTextEdit { color: palette(placeholderText); }")
        else:
            self.planning_edit.setStyleSheet("")

    def _apply_planning_doc_state(self, label: str):
        """把当前文档的生成状态同步到工具栏与共享编辑器。"""
        exists = self._planning_exists.get(label, False)
        if exists:
            self.planning_status_label.setText(f"{label} · 已生成")
            self.planning_status_label.setStyleSheet("font-size: 12px; color: green;")
            self.planning_save_btn.setEnabled(True)
            self.planning_edit.setPlaceholderText(f"编辑 {label}")
            self._set_planning_read_only(False)
        else:
            self.planning_status_label.setText(f"{label} · 未生成")
            self.planning_status_label.setStyleSheet(
                "font-size: 12px; color: palette(placeholderText);"
            )
            self.planning_save_btn.setEnabled(False)
            self.planning_edit.setPlaceholderText(f"{label} — 尚未生成")
            self._set_planning_read_only(True)

    # ── 规划文档选择 / 加载 ─────────────────────────────────────────────

    def _select_planning_row(self, row: int):
        """程序化选中列表项，不触发切换逻辑（由调用方显式加载）。"""
        self._planning_switch_guard = True
        try:
            self.planning_list.setCurrentRow(row)
        finally:
            self._planning_switch_guard = False

    def _on_planning_row_changed(self, row: int):
        """用户在左侧列表切换文档：先写回/保存旧文档，再加载新文档。"""
        if self._planning_switch_guard:
            return
        if row < 0 or row >= len(PLANNING_DOCS):
            return
        label = PLANNING_DOCS[row][0]
        if label == self._planning_current:
            return

        previous = self._planning_current
        if previous is not None and previous in self._planning_loaded:
            # 先记住当前编辑器文本，再落盘脏文档，最后切换。
            self._planning_cache[previous] = self.planning_edit.toPlainText()
            if previous in self._planning_dirty:
                self.flush_planning_autosave()

        self._show_planning_doc(label)

    def _show_planning_doc(self, label: str, force: bool = False):
        """把指定规划文档加载进共享编辑器。"""
        if not force and label == self._planning_current and label in self._planning_loaded:
            self._apply_planning_doc_state(label)
            return

        self._planning_current = label
        rel_path = self._rel_path(label)
        exists = self._planning_exists.get(label, False)
        self.planning_save_btn.setEnabled(exists)

        if not exists or not rel_path:
            # 未生成：只读 + 占位提示，不写盘。
            self._planning_cache.pop(label, None)
            self._planning_loaded.discard(label)
            self._planning_dirty.discard(label)
            self.planning_edit.blockSignals(True)
            self.planning_edit.clear()
            self.planning_edit.blockSignals(False)
            self._apply_planning_doc_state(label)
            return

        content = None if force else self._planning_cache.get(label)
        if content is None:
            try:
                content = project_io.read_md(self._project_dir / rel_path)
            except Exception as e:
                logger.error("规划文档读取失败: %s - %s", label, e)
                content = ""
            self._planning_cache[label] = content
            self._planning_loaded.add(label)

        # blockSignals：加载内容不能被误标成“用户编辑”。
        if self.planning_edit.toPlainText() != content:
            self.planning_edit.blockSignals(True)
            self.planning_edit.setPlainText(content)
            self.planning_edit.blockSignals(False)

        self._apply_planning_doc_state(label)

    # ── 自动保存 ────────────────────────────────────────────────────────

    def _on_planning_edited(self):
        """规划文档编辑 → 写内存缓存、标脏并启动防抖自动保存。"""
        label = self._planning_current
        if not label or label not in self._planning_loaded:
            return
        if not self._planning_exists.get(label, False):
            return
        self._planning_cache[label] = self.planning_edit.toPlainText()
        self._planning_dirty.add(label)
        self._planning_autosave.start()

    def flush_planning_autosave(self) -> list[str]:
        """立刻落盘脏的规划文档。返回本次保存的文档名。"""
        self._planning_autosave.stop()
        if not self._project_dir or not self._planning_dirty:
            return []

        saved: list[str] = []
        current = self._planning_current
        for label in sorted(self._planning_dirty):
            rel = self._rel_path(label)
            if not rel:
                continue
            try:
                if label == current and label in self._planning_loaded:
                    content = self.planning_edit.toPlainText()
                else:
                    content = self._planning_cache.get(label, "")
                project_io.write_md(self._project_dir / rel, content)
                self._planning_cache[label] = content
                saved.append(label)
                logger.debug("规划文档自动保存: %s", rel)
            except Exception as e:
                logger.error("规划文档自动保存失败: %s - %s", label, e)
        self._planning_dirty.clear()
        return saved

    def _emit_planning_save(self):
        if self._planning_current:
            self.planning_save_requested.emit(self._planning_current)

    # ── 外部 API ────────────────────────────────────────────────────────

    def set_project_dir(self, project_dir: Path | None):
        """设置项目目录，刷新规划文档状态。"""
        self.flush_planning_autosave()
        self._project_dir = project_dir
        self._planning_loaded.clear()
        self._planning_dirty.clear()
        self._planning_cache.clear()
        self._planning_current = None
        self._refresh_planning_states()

        # 默认选中第一份规划文档（内容按需从磁盘读取）。
        if PLANNING_DOCS:
            self._select_planning_row(0)
            self._show_planning_doc(PLANNING_DOCS[0][0], force=True)

    def refresh_planning(self, doc_name: str | None = None):
        """刷新指定规划文档（或全部）的内容和状态。"""
        # 先落盘用户未保存的编辑，避免被工作流写盘后 reload 冲掉
        self.flush_planning_autosave()
        if doc_name:
            self._planning_loaded.discard(doc_name)
            self._planning_dirty.discard(doc_name)
            self._planning_cache.pop(doc_name, None)
        else:
            self._planning_loaded.clear()
            self._planning_dirty.clear()
            self._planning_cache.clear()
        self._refresh_planning_states()

        # 如果当前在规划页，重新加载选中项（只刷新被点名的文档时，
        # 若它不是当前文档则保留当前编辑器内容，不打断用户）。
        if self.tab_bar.currentIndex() > 0:
            row = self.planning_list.currentRow()
            if row < 0 or row >= len(PLANNING_DOCS):
                row = 0
                self._select_planning_row(row)
            label = PLANNING_DOCS[row][0]
            if doc_name is None or doc_name == label:
                self._show_planning_doc(label, force=True)

    def get_planning_content(self, doc_name: str) -> str:
        """获取指定规划文档内容：当前编辑中的文档取编辑器，其余取缓存/磁盘。"""
        if doc_name == self._planning_current:
            return self.planning_edit.toPlainText()
        if doc_name in self._planning_cache:
            return self._planning_cache[doc_name]
        rel_path = self._rel_path(doc_name)
        if rel_path and self._project_dir:
            fpath = self._project_dir / rel_path
            try:
                if fpath.exists() and fpath.stat().st_size > 0:
                    content = project_io.read_md(fpath)
                    self._planning_cache[doc_name] = content
                    return content
            except Exception as e:
                logger.error("规划文档读取失败: %s - %s", doc_name, e)
        return ""

    # ── 正文 ────────────────────────────────────────────────────────────

    def _on_tab_changed(self, index: int):
        """一级标签切换：离开规划页先落盘；进入规划页按需加载。"""
        if index <= 0:
            if self._planning_dirty:
                self.flush_planning_autosave()
            return
        row = self.planning_list.currentRow()
        if row < 0 or row >= len(PLANNING_DOCS):
            if not PLANNING_DOCS:
                return
            self._select_planning_row(0)
            row = 0
        self._show_planning_doc(PLANNING_DOCS[row][0])

    def load_chapter(self, chapter):
        logger.info("编辑器加载章节: 第%s章 %s", getattr(chapter, "number", "?"), getattr(chapter, "title", ""))
        self._current_chapter = chapter
        self.chapter_title.setText(f"第{chapter.number}章 {chapter.title}" if hasattr(chapter, 'number') else "")
        self.content_edit.blockSignals(True)
        self.content_edit.setPlainText(chapter.content or "")
        self.content_edit.blockSignals(False)
        self._update_word_count()
        # 切换到正文标签
        self.tab_bar.setCurrentIndex(0)
        self.content_edit.update()

    def get_content(self) -> str:
        return self.content_edit.toPlainText()

    def retranslate(self):
        self.chapter_title.setText(t("editor_select_chapter"))
        self.content_edit.setPlaceholderText(t("editor_ph_content"))
        self.save_btn.setText(t("editor_save"))
        self.tab_bar.setTabText(0, t("editor_tab_content"))
        self.tab_bar.setTabText(1, "规划")
        self.planning_save_btn.setText(t("editor_save"))
        if self._planning_current:
            self._apply_planning_doc_state(self._planning_current)

    def detach_chapter(self):
        """卸下当前章节并清空正文区，不触发 content_changed。

        切项目/新建项目时必须在替换 Project 之前调用：否则 content_edit.clear()
        会触发 textChanged，_on_content_changed 仍拿到旧项目的 Chapter，把旧章
        写成空文件（数据丢失）。
        """
        self._current_chapter = None
        self.content_edit.blockSignals(True)
        self.content_edit.clear()
        self.content_edit.blockSignals(False)
        self._update_word_count()

    def clear(self):
        self.flush_planning_autosave()
        self.detach_chapter()
        self.chapter_title.setText(t("editor_select_chapter"))
        self.word_count_label.setText(f"0 {t('editor_words')}")
        self._planning_autosave.stop()
        self._planning_loaded.clear()
        self._planning_dirty.clear()
        self._planning_cache.clear()
        self._planning_current = None
        self._select_planning_row(-1)
        self._refresh_planning_states()

    def _on_text_changed(self):
        self._update_word_count()
        self.content_changed.emit()

    def _update_word_count(self):
        text = self.content_edit.toPlainText()
        count = len(text.replace(" ", "").replace("\n", ""))
        self.word_count_label.setText(f"{count:,} {t('editor_words')}")
