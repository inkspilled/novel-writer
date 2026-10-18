"""大世界状态管理 — 结构化的世界/人物/物品追踪。

用法：
    from novel_writer.core.world_state import WorldState
    gs = WorldState(project_dir)
    gs.load()
    char = gs.get_character("凌尘")
    gs.update_character("凌尘", {"gold": 10, "cultivation": {"level": "炼气", "sub_level": "三层"}})
    gs.add_timeline_event(1, "凌尘发现修炼坪异常", "修炼坪")
    gs.save()
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from .logger import get_logger

logger = get_logger(__name__)

WORLD_STATE_FILE = "world_state.json"


class WorldState:
    """项目级大世界状态管理器。"""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self._path = project_dir / WORLD_STATE_FILE
        self._data: dict = {}

    def load(self) -> dict:
        """加载大世界状态，不存在则返回空结构。"""
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
                logger.debug("大世界状态已加载: %s", self._path)
            except (json.JSONDecodeError, IOError) as e:
                logger.error("大世界状态加载失败: %s", e)
                self._data = self._default_state()
        else:
            self._data = self._default_state()
        return self._data

    def save(self) -> None:
        """保存大世界状态到文件（原子写）。"""
        from .project_io import write_json
        write_json(self._path, self._data)
        logger.debug("大世界状态已保存: %s", self._path)

    def _default_state(self) -> dict:
        return {
            "version": 1,
            "world": {"name": "", "current_date": "", "locations": {}, "power_system": {}},
            "characters": {},
            "items_catalog": {},
            "timeline": [],
            "location_log": [],
        }

    # ── 世界 ──

    @property
    def world(self) -> dict:
        return self._data.setdefault("world", {})

    @property
    def locations(self) -> dict:
        return self.world.setdefault("locations", {})

    @property
    def location_log(self) -> list:
        return self._data.setdefault("location_log", [])

    def record_location_event(self, chapter: int, character: str, action: str,
                              location: str, from_location: str = "") -> None:
        """记录地点流水：谁、什么时候、进入/离开哪个地点。"""
        if not character or not location:
            return
        self.location_log.append({
            "chapter": int(chapter or 0),
            "date": str(self.world.get("current_date") or ""),
            "character": character,
            "action": action,  # enter / leave
            "location": location,
            "from": from_location,
        })
        if len(self.location_log) > 2000:
            del self.location_log[:len(self.location_log) - 2000]

    def query_location_log(self, character: str = "", location: str = "",
                           start_chapter: int = 0, end_chapter: int = 0,
                           limit: int = 200) -> list[dict]:
        """按人物/地点/章号范围查地点流水（JSON 内线性过滤，2000 条上限内足够快）。

        Args:
            character: 角色名，空 = 不过滤
            location: 地点名，空 = 不过滤
            start_chapter: 起始章（含），0 = 不限
            end_chapter: 结束章（含），0 = 不限
            limit: 最多返回条数（按时间正序，取最近的 limit 条）
        """
        rows = []
        for ev in self.location_log:
            if not isinstance(ev, dict):
                continue
            if character and ev.get("character") != character:
                continue
            if location and ev.get("location") != location:
                continue
            ch = int(ev.get("chapter") or 0)
            if start_chapter and ch < start_chapter:
                continue
            if end_chapter and ch > end_chapter:
                continue
            rows.append(ev)
        return rows[-limit:] if limit > 0 else rows

    def add_location(self, name: str, info: dict) -> None:
        """地点是唯一实体：同名合并描述，不覆盖旧字段。"""
        loc = self.locations.setdefault(name, {})
        if isinstance(info, dict):
            loc.update(info)

    def referenced_locations(self) -> list[str]:
        """人物所在地 + 物品所在地/持有者所在地，去重排序。"""
        names: set[str] = set(self.locations.keys())
        for c in self.characters.values():
            if isinstance(c, dict) and c.get("location"):
                names.add(str(c["location"]))
        for ev in self.location_log:
            if isinstance(ev, dict) and ev.get("location"):
                names.add(str(ev["location"]))
        for item in self.items_catalog.values():
            if not isinstance(item, dict):
                continue
            if item.get("location"):
                names.add(str(item["location"]))
            holder = item.get("holder")
            if holder:
                loc = (self.characters.get(holder) or {}).get("location")
                if loc:
                    names.add(str(loc))
        return sorted(n for n in names if n)

    def sync_locations(self) -> None:
        """把人物/物品引用到、但 locations 里缺的地点补成空记录。"""
        for name in self.referenced_locations():
            self.locations.setdefault(name, {})

    def characters_at(self, location: str) -> list[str]:
        """地点当前有哪些人（从角色 location 反查，多对多只存一边）。"""
        return [n for n, c in self.characters.items()
                if isinstance(c, dict) and (c.get("location") or "") == location]

    def items_at(self, location: str) -> list[str]:
        """地点当前有哪些物品（未被角色持有、且 location 匹配）。"""
        return [n for n, item in self.items_catalog.items()
                if isinstance(item, dict)
                and not item.get("holder")
                and (item.get("location") or "") == location]

    def item_holder(self, item_name: str) -> str:
        item = self.items_catalog.get(item_name) or {}
        return str(item.get("holder") or "")

    def set_item_position(self, item_name: str, location: str = "", holder: str = "") -> bool:
        item = self.items_catalog.get(item_name)
        if not isinstance(item, dict):
            return False
        if holder and not location:
            location = (self.characters.get(holder) or {}).get("location", "")
        item["location"] = location
        item["holder"] = holder
        return True

    def set_date(self, date: str) -> None:
        self.world["current_date"] = date

    # ── 人物 ──

    @property
    def characters(self) -> dict:
        return self._data.setdefault("characters", {})

    def get_character(self, name: str) -> dict | None:
        return self.characters.get(name)

    def add_character(self, name: str, info: dict) -> None:
        """添加新角色。"""
        self.characters[name] = info
        logger.info("新增角色: %s", name)

    def update_character(self, name: str, updates: dict) -> None:
        """更新角色属性（深度合并）。"""
        char = self.characters.get(name)
        if not char:
            logger.warning("角色不存在: %s", name)
            return
        self._deep_merge(char, updates)
        logger.info("更新角色 %s: %s", name, list(updates.keys()))

    def _deep_merge(self, base: dict, update: dict) -> None:
        for k, v in update.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._deep_merge(base[k], v)
            else:
                base[k] = v

    # ── 物品 ──

    @property
    def items_catalog(self) -> dict:
        return self._data.setdefault("items_catalog", {})

    def add_item(self, name: str, info: dict) -> None:
        """物品是唯一实体：同名合并，不丢失已有 location/holder。"""
        item = self.items_catalog.setdefault(name, {})
        if isinstance(info, dict):
            item.update(info)
        item["name"] = name

    def get_item(self, name: str) -> dict | None:
        return self.items_catalog.get(name)

    def give_item(self, char_name: str, item_name: str, item_info: dict | None = None) -> None:
        """给角色一件物品。"""
        char = self.characters.get(char_name)
        if not char:
            return
        inv = char.setdefault("inventory", [])
        # 避免重复
        if not any(i.get("name") == item_name for i in inv):
            inv.append({"name": item_name, **(item_info or {})})
            self.set_item_position(item_name, holder=char_name)
            logger.info("给予 %s 物品: %s", char_name, item_name)

    def remove_item(self, char_name: str, item_name: str) -> bool:
        """从角色背包移除物品。"""
        char = self.characters.get(char_name)
        if not char:
            return False
        inv = char.get("inventory", [])
        before = len(inv)
        char["inventory"] = [i for i in inv if i.get("name") != item_name]
        removed = len(char["inventory"]) < before
        if removed:
            self.set_item_position(item_name, location=char.get("location", ""), holder="")
            logger.info("移除 %s 物品: %s", char_name, item_name)
        return removed

    # ── 时间线 ──

    @property
    def timeline(self) -> list:
        return self._data.setdefault("timeline", [])

    def add_timeline_event(self, chapter: int, event: str, location: str = "") -> None:
        """追加时间线事件。

        chapter 必须由调用方显式传入：``apply_llm_update`` 传入本章章号，
        否则所有 LLM 提取的事件都会落到 0 章（故事线台账会按章号对账，脏章号即失真）。
        """
        self.timeline.append({
            "chapter": int(chapter or 0),
            "event": event,
            "location": location,
        })

    # ── 上下文输出 ──

    def build_context_text(self) -> str:
        """生成大世界状态的文本摘要，用于注入 LLM 上下文。"""
        parts = []

        # 世界信息
        w = self.world
        if w.get("name"):
            parts.append(f"=== 世界设定 ===\n世界: {w['name']}")
            if w.get("current_date"):
                parts.append(f"当前时间: {w['current_date']}")
            if w.get("power_system", {}).get("levels"):
                levels = " → ".join(w["power_system"]["levels"])
                parts.append(f"修炼等级: {levels}")

        if self.location_log:
            lines = []
            for ev in self.location_log[-8:]:
                action = "进入" if ev.get("action") == "enter" else "离开"
                lines.append(f"- 第{ev.get('chapter', 0)}章 {ev.get('character', '')} {action} {ev.get('location', '')}")
            parts.append("=== 地点流水（近期） ===\n" + "\n".join(lines))

        # 角色状态
        for name, char in self.characters.items():
            lines = [f"【{name}】"]
            cult = char.get("cultivation", {})
            if cult:
                lines.append(f"  境界: {cult.get('level', '?')}{cult.get('sub_level', '')}")
            if char.get("hp") is not None:
                lines.append(f"  生命: {char['hp']}  灵力: {char.get('sp', 0)}")
            if char.get("gold") is not None:
                lines.append(f"  灵石: {char['gold']}")
            if char.get("location"):
                lines.append(f"  位置: {char['location']}")
            goals = char.get("goals")
            if isinstance(goals, list) and goals:
                lines.append(f"  目标: {'；'.join(str(g) for g in goals[:4])}")
            inv = char.get("inventory", [])
            if inv:
                items = ", ".join(i.get("name", "?") for i in inv)
                lines.append(f"  背包: {items}")
            equip = char.get("equipment", {})
            if equip:
                for slot, item in equip.items():
                    if item:
                        lines.append(f"  {slot}: {item}")
            skills = char.get("skills", [])
            if skills:
                lines.append(f"  技能: {', '.join(skills)}")
            status = char.get("status", [])
            if status:
                lines.append(f"  状态: {', '.join(status)}")
            parts.append("\n".join(lines))

        # 物品图鉴
        catalog = self.items_catalog
        if catalog:
            item_lines = ["=== 物品图鉴 ==="]
            for name, info in catalog.items():
                desc = info.get("effect", info.get("description", ""))
                life = " [保命]" if info.get("life_save") else ""
                uses = f" (剩余{info['uses']}次)" if info.get("uses", -1) > 0 else ""
                item_lines.append(f"- {name}: {desc}{life}{uses}")
            parts.append("\n".join(item_lines))

        return "\n\n".join(parts)

    # ── LLM 结构化更新 ──

    def compact_update(self, update_json: dict, chapter: int = 0,
                       max_timeline: int = 3) -> dict:
        """把 LLM 的“全量快照”压成最小 diff，写正典前调用。

        - 角色：只保留变化字段；inventory 改成 inventory_add/remove
        - 物品：已存在的 new 丢弃；give/remove/move 只保留实际变化
        - 地点：与当前一致的丢弃
        - timeline：去重 + 截断，避免与感知事件逐段复述
        """
        if not isinstance(update_json, dict):
            return {}
        u = copy.deepcopy(update_json)
        u["_chapter"] = int(chapter or u.get("_chapter") or 0)

        chars = u.get("characters")
        if isinstance(chars, dict):
            for name in list(chars):
                delta = chars[name]
                if not isinstance(delta, dict):
                    chars.pop(name); continue
                cur = self.characters.get(name)
                if not isinstance(cur, dict):
                    continue  # 新角色保留完整档案
                for field_name in list(delta):
                    value = delta[field_name]
                    if field_name == "inventory":
                        old = cur.get("inventory") or []
                        new = value if isinstance(value, list) else []
                        if old == new:
                            delta.pop(field_name)
                            continue
                        old_names = {str(i.get("name")) for i in old if isinstance(i, dict)}
                        new_names = {str(i.get("name")) for i in new if isinstance(i, dict)}
                        added = [i for i in new if isinstance(i, dict) and str(i.get("name")) not in old_names]
                        removed = [n for n in old_names if n and n not in new_names]
                        if added:
                            delta["inventory_add"] = added
                        if removed:
                            delta["inventory_remove"] = removed
                        delta.pop(field_name)
                    elif cur.get(field_name) == value:
                        delta.pop(field_name)
                if not delta:
                    chars.pop(name)
            if not chars:
                u.pop("characters")

        items = u.get("items")
        if isinstance(items, dict):
            for key in ("new", "give", "remove", "move"):
                ops = items.get(key)
                if not isinstance(ops, list):
                    continue
                kept = []
                for op in ops:
                    if not isinstance(op, dict):
                        continue
                    if key == "new":
                        name = str(op.get("name") or "")
                        if not name or name in self.items_catalog:
                            continue
                    elif key == "give":
                        ch = self.characters.get(str(op.get("character") or "")) or {}
                        inv = ch.get("inventory") or []
                        if any(isinstance(i, dict) and i.get("name") == op.get("item") for i in inv):
                            continue
                    elif key == "remove":
                        ch = self.characters.get(str(op.get("character") or "")) or {}
                        inv = ch.get("inventory") or []
                        if not any(isinstance(i, dict) and i.get("name") == op.get("item") for i in inv):
                            continue
                    elif key == "move":
                        item = self.items_catalog.get(str(op.get("item") or "")) or {}
                        if item.get("location") == op.get("location") and item.get("holder") == op.get("holder"):
                            continue
                    kept.append(op)
                if kept:
                    items[key] = kept
                else:
                    items.pop(key, None)
            if not items:
                u.pop("items")

        locs = u.get("locations")
        if isinstance(locs, dict):
            for name in list(locs):
                if self.locations.get(name) == locs[name]:
                    locs.pop(name)
            if not locs:
                u.pop("locations")

        timeline = u.get("timeline")
        if isinstance(timeline, list):
            existing = {str(e.get("event")) for e in self.timeline if isinstance(e, dict)}
            seen = set()
            kept = []
            for ev in timeline:
                if not isinstance(ev, dict) or not ev.get("event"):
                    continue
                text = str(ev["event"])
                if text in seen or text in existing:
                    continue
                seen.add(text); kept.append(ev)
                if len(kept) >= max_timeline:
                    break
            if kept:
                u["timeline"] = kept
            else:
                u.pop("timeline", None)

        if u.get("date") == self.world.get("current_date"):
            u.pop("date", None)
        if set(u.keys()) <= {"_chapter"}:
            return {}
        return u

    def apply_llm_update(self, update_json: dict, chapter: int = 0) -> list[str]:
        """应用 LLM 返回的状态更新 JSON。

        格式示例:
        {
          "characters": {
            "凌尘": {"gold": 10, "cultivation": {"sub_level": "三层"}},
            "新角色": {"age": 20, "cultivation": {"level": "筑基", "sub_level": "初期"}, "location": "演武场"}
          },
          "items": {
            "new": [{"name": "灵石矿", "type": "材料", "effect": "修炼辅助"}],
            "give": [{"character": "凌尘", "item": "灵石矿"}],
            "remove": [{"character": "凌尘", "item": "枯叶（异常）"}]
          },
          "locations": {
            "禁地": {"type": "秘境", "description": "青云宗后山禁地"}
          },
          "timeline": [
            {"event": "凌尘发现训练场痕迹已被清理", "location": "训练场"}
          ],
          "date": "宗历1247年 秋 第2天"
        }
        """
        logs = []

        # 更新角色
        chapter = int(chapter or update_json.get("_chapter") or 0)
        characters = update_json.get("characters") or {}
        if isinstance(characters, dict):
            for name, updates in characters.items():
                if not isinstance(updates, dict):
                    continue
                old_loc = str((self.characters.get(name) or {}).get("location") or "")
                if name in self.characters:
                    delta = dict(updates)
                    add = delta.pop("inventory_add", None) or []
                    rem = delta.pop("inventory_remove", None) or []
                    if add or rem:
                        inv = self.characters[name].setdefault("inventory", [])
                        rem_names = {str(x) for x in rem}
                        inv[:] = [i for i in inv if not (isinstance(i, dict) and str(i.get("name")) in rem_names)]
                        have = {str(i.get("name")) for i in inv if isinstance(i, dict)}
                        for item in add:
                            if isinstance(item, dict) and str(item.get("name")) not in have:
                                inv.append(item)
                        updates = delta
                    self.update_character(name, updates)
                    logs.append(f"更新角色 {name}")
                else:
                    self.add_character(name, updates)
                    logs.append(f"新增角色 {name}")
                new_loc = str((self.characters.get(name) or {}).get("location") or "")
                if old_loc != new_loc:
                    if old_loc:
                        self.record_location_event(chapter, name, "leave", old_loc, new_loc)
                    if new_loc:
                        self.record_location_event(chapter, name, "enter", new_loc, old_loc)

        # 物品操作（LLM 输出不可信，逐键取缺省）
        items = update_json.get("items") or {}
        for item in items.get("new") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            self.add_item(item["name"], item)
            logs.append(f"新物品: {item['name']}")
        for op in items.get("move") or []:
            if not isinstance(op, dict) or not op.get("item"):
                continue
            if self.set_item_position(op["item"], location=op.get("location", ""), holder=op.get("holder", "")):
                logs.append(f"移动物品: {op['item']}")
        for op in items.get("give") or []:
            if not isinstance(op, dict) or not op.get("character") or not op.get("item"):
                continue
            item_info = self.get_item(op["item"])
            self.give_item(op["character"], op["item"], item_info)
            logs.append(f"给予 {op['character']}: {op['item']}")
        for op in items.get("remove") or []:
            if not isinstance(op, dict) or not op.get("character") or not op.get("item"):
                continue
            if self.remove_item(op["character"], op["item"]):
                logs.append(f"移除 {op['character']}: {op['item']}")

        # 地点：同名合并，保留已有描述
        for loc_name, loc_info in (update_json.get("locations") or {}).items():
            existed = loc_name in self.locations
            self.add_location(loc_name, loc_info if isinstance(loc_info, dict) else {})
            logs.append(("更新地点: " if existed else "新地点: ") + loc_name)

        # 时间线（章号以本次更新的 chapter 为准，LLM 漏传/传错时不落 0 章脏数据）
        for event in update_json.get("timeline") or []:
            if not isinstance(event, dict) or not event.get("event"):
                continue
            try:
                event_chapter = int(event.get("chapter") or chapter or 0)
            except (TypeError, ValueError):
                event_chapter = int(chapter or 0)
            self.add_timeline_event(
                event_chapter,
                event["event"],
                event.get("location", ""),
            )
            logs.append(f"时间线: {event['event']}")

        # 日期
        if update_json.get("date"):
            self.set_date(update_json["date"])
            logs.append(f"日期: {update_json['date']}")

        return logs
