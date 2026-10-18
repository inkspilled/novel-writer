"""人物页 — 主界面内嵌的角色管理（分档/状态/auto_sim/目标）。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from ...core.characters.registry import (
    CharacterRecord, CharacterRegistry, CharacterStatus, CharacterTier,
)
from ...locales import t

_TIER_LABELS = {"core": "核心", "recurring": "常驻", "extra": "群演"}
_STATUS_LABELS = {
    "alive": "存活", "dead": "死亡", "resurrected": "复活",
    "ghost": "亡灵", "missing": "失踪", "dormant": "休眠",
}


class CharacterPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("editorPanel")
        self._project_dir: Path | None = None
        self.registry: CharacterRegistry | None = None
        self._current: CharacterRecord | None = None
        self._setup_ui()
        self._set_enabled(False)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QWidget()
        toolbar.setObjectName("editorToolbar")
        tl = QHBoxLayout(toolbar)
        tl.setContentsMargins(20, 10, 20, 10)
        self.title_label = QLabel("人物")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        tl.addWidget(self.title_label)
        self.hint_label = QLabel("分档 / 状态 / auto_sim / 目标")
        self.hint_label.setStyleSheet("font-size: 12px; color: gray;")
        tl.addWidget(self.hint_label)
        tl.addStretch()
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self.reload)
        tl.addWidget(self.btn_refresh)
        self.btn_add = QPushButton("＋ 新增")
        self.btn_add.clicked.connect(self._add)
        tl.addWidget(self.btn_add)
        self.btn_save = QPushButton("保存")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(self.save)
        tl.addWidget(self.btn_save)
        layout.addWidget(toolbar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(12, 12, 8, 12)
        lv.setSpacing(8)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_selected)
        lv.addWidget(self.list, 1)
        self.btn_del = QPushButton("删除")
        self.btn_del.setObjectName("danger")
        self.btn_del.clicked.connect(self._delete)
        lv.addWidget(self.btn_del)
        left.setFixedWidth(230)
        body.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(20, 16, 20, 16)
        rv.setSpacing(10)
        self.name_label = QLabel("打开项目后管理角色")
        self.name_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        rv.addWidget(self.name_label)

        form = QFormLayout()
        form.setSpacing(10)
        self.tier_combo = QComboBox()
        for tier in CharacterTier:
            self.tier_combo.addItem(_TIER_LABELS[tier.value], tier.value)
        form.addRow("分档", self.tier_combo)
        self.status_combo = QComboBox()
        for st in CharacterStatus:
            self.status_combo.addItem(_STATUS_LABELS[st.value], st.value)
        form.addRow("状态", self.status_combo)
        self.auto_sim = QCheckBox("允许自动 POV 推演（character_panel）")
        form.addRow("推演", self.auto_sim)
        self.location = QLineEdit()
        self.location.setPlaceholderText("当前所在地点")
        form.addRow("位置", self.location)
        self.goals = QPlainTextEdit()
        self.goals.setPlaceholderText("每行一个目标")
        self.goals.setMaximumHeight(100)
        form.addRow("目标", self.goals)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("作者备注 / 秘密只给作者看")
        self.notes.setMaximumHeight(80)
        form.addRow("备注", self.notes)
        rv.addLayout(form)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-size: 12px; color: gray;")
        rv.addWidget(self.status_label)
        rv.addStretch()
        body.addWidget(right, 1)
        layout.addLayout(body, 1)

    # ── 项目切换 ──

    def set_project_dir(self, project_dir: Path | None):
        self._project_dir = Path(project_dir) if project_dir else None
        self.registry = CharacterRegistry(self._project_dir) if self._project_dir else None
        self.reload()

    def reload(self):
        if not self.registry:
            self._set_enabled(False)
            self.list.clear()
            self._current = None
            self.name_label.setText("打开项目后管理角色")
            return
        self._set_enabled(True)
        current_id = self._current.id if self._current else ""
        self.list.blockSignals(True)
        self.list.clear()
        for rec in self.registry.all():
            item = QListWidgetItem(
                f"{rec.name}   {_TIER_LABELS.get(rec.tier.value, rec.tier.value)} / "
                f"{_STATUS_LABELS.get(rec.status.value, rec.status.value)}"
                + ("   POV" if rec.auto_sim else "")
            )
            item.setData(Qt.ItemDataRole.UserRole, rec.id)
            self.list.addItem(item)
            if rec.id == current_id:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if self.list.count() and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        if self.list.count():
            self._on_selected(self.list.currentItem(), None)
        else:
            self._current = None
            self.name_label.setText("还没有角色")
            self.status_label.setText("工作流会从人物设定自动建卡，也可以手动新增")

    def _set_enabled(self, on: bool):
        for w in (self.list, self.btn_add, self.btn_save, self.btn_del,
                  self.tier_combo, self.status_combo, self.auto_sim,
                  self.location, self.goals, self.notes, self.btn_refresh):
            w.setEnabled(on)

    # ── 编辑 ──

    def _on_selected(self, current, _prev):
        self._sync_current()
        if not current or not self.registry:
            return
        rec = self.registry.get(current.data(Qt.ItemDataRole.UserRole))
        if not rec:
            return
        self._current = rec
        self.name_label.setText(rec.name)
        self.tier_combo.setCurrentIndex(max(0, self.tier_combo.findData(rec.tier.value)))
        self.status_combo.setCurrentIndex(max(0, self.status_combo.findData(rec.status.value)))
        self.auto_sim.setChecked(rec.auto_sim)
        self.location.setText(rec.location)
        self.goals.setPlainText("\n".join(rec.goals))
        self.notes.setPlainText(rec.notes)
        self.status_label.setText(f"{rec.id} · 首次第{rec.first_seen}章 · 最近第{rec.last_seen}章")

    def _sync_current(self):
        rec = self._current
        if not rec:
            return
        rec.tier = CharacterTier(self.tier_combo.currentData())
        rec.status = CharacterStatus(self.status_combo.currentData())
        rec.auto_sim = self.auto_sim.isChecked()
        rec.location = self.location.text().strip()
        rec.goals = [g.strip() for g in self.goals.toPlainText().splitlines() if g.strip()]
        rec.notes = self.notes.toPlainText().strip()

    def save(self):
        if not self.registry:
            return
        self._sync_current()
        self.registry.save()
        self.status_label.setText("已保存")
        self.reload()

    def _add(self):
        if not self.registry:
            return
        name, ok = QInputDialog.getText(self, "新增角色", "角色名")
        if not ok or not name.strip():
            return
        if self.registry.get(name.strip()):
            QMessageBox.information(self, "人物", "已存在同名角色")
            return
        rec = self.registry.create(name.strip(), chapter=0, tier=CharacterTier.EXTRA)
        self._current = rec
        self.reload()
        self.list.setCurrentRow(max(0, self.list.count() - 1))

    def _delete(self):
        if not self.registry or not self._current:
            return
        rec = self._current
        if QMessageBox.question(
            self, "删除角色", f"删除「{rec.name}」的档案？正典事件不会被删除。"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.registry.remove(rec.name)
        self._current = None
        self.reload()
