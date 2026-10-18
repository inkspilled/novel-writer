"""角色管理 — 分档/状态/auto_sim/目标，避免只能手改 characters.json。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
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


class CharacterDialog(QDialog):
    def __init__(self, project_dir: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("menu_character_manage"))
        self.resize(720, 560)
        self.registry = CharacterRegistry(Path(project_dir))
        self._current: CharacterRecord | None = None
        self._setup_ui()
        self._refresh()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

        body = QHBoxLayout()
        body.setSpacing(16)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        lv.addWidget(QLabel("角色列表"))
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_selected)
        lv.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.btn_add = QPushButton("＋ 新增")
        self.btn_add.clicked.connect(self._add)
        self.btn_del = QPushButton("删除")
        self.btn_del.setObjectName("danger")
        self.btn_del.clicked.connect(self._delete)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_del)
        lv.addLayout(row)
        body.addWidget(left, 2)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(10)
        self.name_label = QLabel("未选择")
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
        self.goals.setMinimumHeight(72)
        self.goals.setMaximumHeight(110)
        form.addRow("目标", self.goals)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("作者备注 / 秘密只给作者看")
        self.notes.setMinimumHeight(60)
        self.notes.setMaximumHeight(90)
        form.addRow("备注", self.notes)
        rv.addLayout(form)
        rv.addStretch()
        body.addWidget(right, 3)

        root.addLayout(body, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        cancel = QPushButton(t("settings_cancel"))
        cancel.clicked.connect(self.reject)
        save = QPushButton(t("settings_save_all"))
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        bottom.addWidget(cancel)
        bottom.addWidget(save)
        root.addLayout(bottom)

    def _refresh(self, select_id: str | None = None):
        current_id = select_id or (self._current.id if self._current else "")
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
            self.name_label.setText("没有角色")

    def _on_selected(self, current, _prev):
        self._sync_current()
        if not current:
            return
        rec = self.registry.get(current.data(Qt.ItemDataRole.UserRole))
        if not rec:
            return
        self._current = rec
        self.name_label.setText(rec.name)
        self.tier_combo.setCurrentIndex(
            max(0, self.tier_combo.findData(rec.tier.value)))
        self.status_combo.setCurrentIndex(
            max(0, self.status_combo.findData(rec.status.value)))
        self.auto_sim.setChecked(rec.auto_sim)
        self.location.setText(rec.location)
        self.goals.setPlainText("\n".join(rec.goals))
        self.notes.setPlainText(rec.notes)

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

    def _add(self):
        name, ok = QInputDialog.getText(self, "新增角色", "角色名")
        if not ok or not name.strip():
            return
        if self.registry.get(name.strip()):
            QMessageBox.information(self, "角色管理", "已存在同名角色")
            return
        rec = self.registry.create(name.strip(), chapter=0, tier=CharacterTier.EXTRA)
        self._refresh(rec.id)

    def _delete(self):
        rec = self._current
        if not rec:
            return
        if QMessageBox.question(
            self, "删除角色", f"删除「{rec.name}」的档案？正典事件不会被删除。"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.registry.remove(rec.name)
        self._current = None
        self._refresh()

    def _save(self):
        self._sync_current()
        self.registry.save()
        self.accept()
