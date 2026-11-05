"""世界页：人物 / 物品 / 地点 / 故事线 四个子页，围绕同一份项目世界数据。

故事线页（时间线/主线/支线/伏笔台账）见 ``storyline_page.py``，
与 world_state 分文件存储（story_threads.json），互不覆盖。
"""
from __future__ import annotations

from pathlib import Path
import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from ...core.world_state import WorldState
from .character_page import CharacterPage
from .storyline_page import StorylinePage


class _WorldBase(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("editorPanel")
        self.gs: WorldState | None = None
        self.title = title
        self._refreshing = False
        self._form_snapshot = None
        self._base_state = {}
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QWidget(); bar.setObjectName("editorToolbar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(20, 10, 20, 10)
        label = QLabel(self.title); label.setStyleSheet("font-size: 18px; font-weight: 700;")
        bl.addWidget(label); bl.addStretch()
        self.btn_refresh = QPushButton("刷新"); self.btn_refresh.clicked.connect(lambda: self.reload(force=True))
        self.btn_add = QPushButton("＋ 新增"); self.btn_add.clicked.connect(self._add)
        self.btn_del = QPushButton("删除"); self.btn_del.setObjectName("danger"); self.btn_del.clicked.connect(self._delete)
        self.btn_save = QPushButton("保存"); self.btn_save.setObjectName("primary"); self.btn_save.clicked.connect(self.save)
        for b in (self.btn_refresh, self.btn_add, self.btn_del, self.btn_save): bl.addWidget(b)
        root.addWidget(bar)
        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(12, 12, 8, 12)
        self.list = QListWidget(); self.list.currentItemChanged.connect(self._selected); lv.addWidget(self.list, 1)
        left.setFixedWidth(230); body.addWidget(left)
        right = QWidget(); self.form_layout = QVBoxLayout(right); self.form_layout.setContentsMargins(20, 16, 20, 16); self.form_layout.setSpacing(10)
        self.name_label = QLabel("打开项目后管理"); self.name_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.form_layout.addWidget(self.name_label)
        self.form = QFormLayout(); self.form.setSpacing(10); self.form_layout.addLayout(self.form)
        self.status_label = QLabel(""); self.status_label.setStyleSheet("font-size: 12px; color: gray;")
        self.form_layout.addWidget(self.status_label); self.form_layout.addStretch()
        body.addWidget(right, 1); root.addLayout(body, 1)
        self._set_enabled(False)

    def _set_enabled(self, on: bool):
        for w in (self.list, self.btn_add, self.btn_del, self.btn_save, self.btn_refresh):
            w.setEnabled(on)

    def set_project_dir(self, project_dir: Path | None):
        self._form_snapshot = None
        if project_dir:
            self.gs = WorldState(Path(project_dir)); self.gs.load(); self._set_enabled(True)
        else:
            self.gs = None; self._set_enabled(False)
        self.reload()

    def _form_values(self):
        return tuple((id(w), w.text() if isinstance(w, QLineEdit) else w.toPlainText())
                     for w in self.findChildren(QWidget)
                     if isinstance(w, (QLineEdit, QPlainTextEdit)) and not w.isReadOnly())

    def reload(self, force=False):
        if not self.gs:
            self.list.clear(); self.name_label.setText("打开项目后管理"); return
        if not force and self._form_snapshot is not None and self._form_values() != self._form_snapshot:
            self.status_label.setText("有未保存编辑，已保留；保存后刷新后台状态")
            return
        selected = self.list.currentItem().data(Qt.ItemDataRole.UserRole) if self.list.currentItem() else ""
        self.gs.load()
        self._base_state = copy.deepcopy(self.gs._data)
        self._refreshing = True
        self.list.blockSignals(True); self.list.clear()
        for name in self._names():
            item = QListWidgetItem(self._label(name)); item.setData(Qt.ItemDataRole.UserRole, name); self.list.addItem(item)
        self.list.blockSignals(False)
        if self.list.count():
            row = next((i for i in range(self.list.count()) if self.list.item(i).data(Qt.ItemDataRole.UserRole) == selected), 0)
            self.list.setCurrentRow(row)
        else:
            self.name_label.setText("还没有记录")
            self._load(None)
        self._refreshing = False
        self._form_snapshot = self._form_values()

    def _selected(self, current, _prev):
        if current is None: self._load(None); return
        name = current.data(Qt.ItemDataRole.UserRole)
        if not self._refreshing:
            self._sync()
        self._load(name)
        self._form_snapshot = self._form_values()

    def _add(self):
        if not self.gs: return
        name, ok = QInputDialog.getText(self, f"新增{self.title}", "名称")
        if not ok or not name.strip(): return
        name = name.strip()
        self._create(name); self.gs.save(); self.reload()

    def _delete(self):
        if not self.gs or self.list.currentItem() is None: return
        name = self.list.currentItem().data(Qt.ItemDataRole.UserRole)
        if QMessageBox.question(self, "删除", f"删除「{name}」？") != QMessageBox.StandardButton.Yes: return
        self._remove(name); self.gs.save(); self.reload()

    def _names(self) -> list[str]: raise NotImplementedError
    def _label(self, name: str) -> str: return name
    def _load(self, name: str | None): raise NotImplementedError
    def _sync(self): pass
    def _create(self, name: str): raise NotImplementedError
    def _remove(self, name: str): raise NotImplementedError

    def save(self):
        if not self.gs: return
        self._sync()
        edited = copy.deepcopy(self.gs._data)
        self.gs.load()
        def apply_changes(before, after, latest):
            for key in before.keys() - after.keys():
                latest.pop(key, None)
            for key, value in after.items():
                if key not in before:
                    latest[key] = copy.deepcopy(value)
                elif before[key] != value:
                    if isinstance(before[key], dict) and isinstance(value, dict) and isinstance(latest.get(key), dict):
                        apply_changes(before[key], value, latest[key])
                    else:
                        latest[key] = copy.deepcopy(value)
        apply_changes(self._base_state, edited, self.gs._data)
        self.gs.save(); self.status_label.setText("已保存"); self.reload(force=True)


class ItemPage(_WorldBase):
    def __init__(self, parent=None): super().__init__("物品", parent)

    def _setup_ui(self):
        super()._setup_ui()
        self.type = QLineEdit(); self.form.addRow("类型", self.type)
        self.effect = QLineEdit(); self.form.addRow("效果", self.effect)
        self.location = QLineEdit(); self.location.setPlaceholderText("输入地点，如：药圃")
        self.form.addRow("位置", self.location)
        self.holder = QLineEdit(); self.holder.setPlaceholderText("输入持有者；留空=在地面")
        self.form.addRow("持有者", self.holder)
        self.desc = QPlainTextEdit(); self.desc.setMaximumHeight(100); self.form.addRow("描述", self.desc)

    def _names(self): return sorted((self.gs.items_catalog or {}).keys())
    def _label(self, name):
        it = (self.gs.items_catalog.get(name) or {})
        pos = it.get("holder") or it.get("location") or "未定位"
        return f"{name}   {it.get('type','')} / {pos}"

    def _load(self, name):
        if not self.gs: return
        if not name:
            self.name_label.setText("还没有物品")
            self.type.setText(""); self.effect.setText(""); self.location.setText("")
            self.holder.setText(""); self.desc.setPlainText("")
            return
        it = self.gs.items_catalog.get(name) or {}
        self.name_label.setText(name)
        self.type.setText(str(it.get("type") or ""))
        self.effect.setText(str(it.get("effect") or ""))
        self.location.setText(str(it.get("location") or ""))
        self.holder.setText(str(it.get("holder") or ""))
        self.desc.setPlainText(str(it.get("description") or ""))

    def _sync(self):
        if not self.gs or self.name_label.text() in ("还没有物品", "打开项目后管理"): return
        name = self.name_label.text(); it = self.gs.items_catalog.get(name)
        if not isinstance(it, dict): return
        it.update({"type": self.type.text().strip(), "effect": self.effect.text().strip(),
                   "description": self.desc.toPlainText().strip()})
        self.gs.set_item_position(name, location=self.location.text().strip(),
                                  holder=self.holder.text().strip())

    def _create(self, name): self.gs.add_item(name, {"type": "", "effect": "", "description": ""})
    def _remove(self, name): self.gs.items_catalog.pop(name, None)


class LocationPage(_WorldBase):
    def __init__(self, parent=None): super().__init__("地点", parent)

    def _setup_ui(self):
        super()._setup_ui()
        self.type = QLineEdit(); self.form.addRow("类型", self.type)
        self.desc = QPlainTextEdit(); self.desc.setMaximumHeight(110); self.form.addRow("描述", self.desc)
        self.aliases = QLineEdit(); self.aliases.setPlaceholderText("同一地点的其他称呼，用顿号分隔")
        self.form.addRow("别名", self.aliases)
        self.parent_location = QLineEdit(); self.form.addRow("所属区域", self.parent_location)
        self.coordinate_frame = QLineEdit(); self.coordinate_frame.setPlaceholderText("如：青云宗平面图；同一坐标系统一原点和方向")
        self.form.addRow("坐标系", self.coordinate_frame)
        self.coordinate_unit = QLineEdit(); self.coordinate_unit.setPlaceholderText("米 / 千米 / 格")
        self.form.addRow("坐标单位", self.coordinate_unit)
        self.coordinates = QLineEdit(); self.coordinates.setPlaceholderText("x,y 或 x,y,z；未规划时留空")
        self.form.addRow("坐标", self.coordinates)
        self.btn_merge = QPushButton("检查重复并合并到已有地点")
        self.btn_merge.clicked.connect(self._merge_location)
        self.form.addRow(self.btn_merge)
        self.occupants = QLabel(""); self.occupants.setWordWrap(True); self.form.addRow("在场角色", self.occupants)
        self.items = QLabel(""); self.items.setWordWrap(True); self.form.addRow("地面物品", self.items)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(160)
        self.form.addRow("地点流水", self.log)
        self.people_hint = QLabel("人物所在地点请在「人物」页修改"); self.people_hint.setStyleSheet("font-size: 11px; color: gray;")
        self.form_layout.addWidget(self.people_hint)

    def _names(self):
        self.gs.sync_locations()
        return self.gs.referenced_locations()
    def _label(self, name):
        loc = (self.gs.locations.get(name) or {})
        return f"{name}   {loc.get('type','')} · {len(self.gs.characters_at(name))}人"

    def _load(self, name):
        if not self.gs: return
        if not name:
            self.name_label.setText("还没有地点"); self.occupants.setText(""); self.items.setText("")
            self.log.setPlainText(""); return
        self.gs.locations.setdefault(name, {})
        loc = self.gs.locations.get(name) or {}
        self.name_label.setText(name); self.type.setText(str(loc.get("type") or "")); self.desc.setPlainText(str(loc.get("description") or ""))
        self.aliases.setText("、".join(loc.get("aliases") or []))
        self.parent_location.setText(str(loc.get("parent") or ""))
        self.coordinate_frame.setText(str(loc.get("coordinate_frame") or ""))
        self.coordinate_unit.setText(str(loc.get("coordinate_unit") or ""))
        self.coordinates.setText(", ".join(map(str, loc.get("coordinates") or [])))
        people = self.gs.characters_at(name); items = self.gs.items_at(name)
        self.occupants.setText("、".join(people) or "（无人）")
        self.items.setText("、".join(items) or "（无）")
        events = self.gs.query_location_log(location=name, limit=100)
        lines = []
        for e in reversed(events):
            action = "进入" if e.get("action") == "enter" else "离开"
            src = e.get("from") or ""
            suffix = f"（来自 {src}）" if action == "进入" and src else (f"（前往 {src}）" if action == "离开" and src else "")
            when = f"第{e.get('chapter', 0)}章"
            if e.get("date"):
                when += f" · {e['date']}"
            lines.append(f"{when}  {e.get('character', '')}  {action}{suffix}")
        self.log.setPlainText("\n".join(lines) or "（暂无进出记录）")

    def _sync(self):
        if not self.gs or self.name_label.text() in ("还没有地点", "打开项目后管理"): return
        name = self.name_label.text()
        self.gs.locations.setdefault(name, {})
        loc = self.gs.locations.get(name)
        if not isinstance(loc, dict): return
        loc.update({"type": self.type.text().strip(), "description": self.desc.toPlainText().strip()})
        from ...core.location_identity import validate_spatial_record, ensure_identity
        text = self.coordinates.text().strip()
        try:
            coords = [float(v.strip()) for v in text.replace("，", ",").split(",")] if text else None
            geo = validate_spatial_record(self.gs.locations, name, {"coordinates": coords, "coordinate_frame": self.coordinate_frame.text().strip(),
                                     "coordinate_unit": self.coordinate_unit.text().strip()})
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        loc.update(geo)
        loc["aliases"] = [a.strip() for a in self.aliases.text().replace("，", "、").split("、") if a.strip()]
        loc["parent"] = self.parent_location.text().strip()
        self.gs.locations[name] = ensure_identity(name, loc)

    def save(self):
        from ...core.location_identity import validate_spatial_record
        if not self.gs:
            return
        try:
            text = self.coordinates.text().strip()
            validate_spatial_record(self.gs.locations, self.name_label.text(), {"coordinates": [float(v.strip()) for v in text.replace("，", ",").split(",")] if text else None,
                               "coordinate_frame": self.coordinate_frame.text().strip(), "coordinate_unit": self.coordinate_unit.text().strip()})
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        super().save()

    def _merge_location(self):
        if not self.gs or self.list.currentItem() is None:
            return
        source = self.list.currentItem().data(Qt.ItemDataRole.UserRole)
        candidates = self.gs.location_candidates(source)
        choices = [n for n, score in candidates] + [n for n in self.gs.locations if n != source and n not in {c[0] for c in candidates}]
        if not choices:
            self.status_label.setText("没有其他地点可合并")
            return
        target, ok = QInputDialog.getItem(self, "合并重复地点", "选择保留的地点（相似候选排在前面，坐标接近不代表同一地点）：", choices, 0, False)
        if not ok:
            return
        from ...core.project_io import write_json
        self.gs.load()
        if source not in self.gs.locations or target not in self.gs.locations:
            self.status_label.setText("地点已在后台变化，请刷新后重试")
            return
        write_json(self.gs.project_dir / "world_state.before_location_merge.json", self.gs._data)
        self.gs.merge_locations(source, target)
        self.gs.save()
        self.reload()
        self.status_label.setText(f"已合并到「{target}」，人物/物品/流水引用已统一，原名称保留为别名")

    def _create(self, name): self.gs.add_location(name, {"type": "", "description": ""})
    def _remove(self, name):
        self.gs.locations.pop(name, None)
        for c in self.gs.characters.values():
            if isinstance(c, dict) and c.get("location") == name: c["location"] = ""
        for it in self.gs.items_catalog.values():
            if isinstance(it, dict) and it.get("location") == name: it["location"] = ""


class WorldPage(QWidget):
    """世界一级页：左侧 人物/物品/地点/故事线 菜单，右侧内容。"""

    chapter_activate_requested = Signal(int)  # 故事线双击 → 打开对应章节

    _PAGES = (("人物", "character_page"), ("物品", "item_page"),
              ("地点", "location_page"), ("故事线", "storyline_page"))

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        self.list = QListWidget()
        self.list.setObjectName("planningList")
        self.list.setFixedWidth(152)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.character_page = CharacterPage()
        self.item_page = ItemPage()
        self.location_page = LocationPage()
        self.storyline_page = StorylinePage()
        self.stack = QStackedWidget()
        for _label, attr in self._PAGES:
            self.stack.addWidget(getattr(self, attr))
        for label, _attr in self._PAGES:
            item = QListWidgetItem(label); item.setData(Qt.ItemDataRole.UserRole, label); self.list.addItem(item)
        self.list.currentRowChanged.connect(self.select)
        self.storyline_page.chapter_activate_requested.connect(self.chapter_activate_requested)
        layout.addWidget(self.list); layout.addWidget(self.stack, 1)
        self.list.setCurrentRow(0)

    def select(self, index: int):
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
            page = self.stack.widget(index)
            if page is not None:
                page.reload()

    def select_page(self, key: str):
        """按页面键（character_page/item_page/location_page/storyline_page）切换。"""
        for idx, (_label, attr) in enumerate(self._PAGES):
            if attr == key:
                self.list.setCurrentRow(idx)
                return True
        return False

    def set_project_dir(self, project_dir):
        for _label, attr in self._PAGES:
            getattr(self, attr).set_project_dir(project_dir)

    def reload(self):
        """刷新全部子页；故事线页顺带与规划/记忆/世界状态对账。"""
        self.character_page.reload(); self.item_page.reload(); self.location_page.reload()
        self.storyline_page.sync(force=False)

    def reload_storyline(self):
        """工作流落盘后只刷新故事线（轻量，不碰人物/物品/地点编辑态）。"""
        self.storyline_page.sync(force=False)
