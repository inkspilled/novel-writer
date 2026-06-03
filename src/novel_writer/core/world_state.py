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
        """保存大世界状态到文件。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug("大世界状态已保存: %s", self._path)

    def _default_state(self) -> dict:
        return {
            "version": 1,
            "world": {"name": "", "current_date": "", "locations": {}, "power_system": {}},
            "characters": {},
            "items_catalog": {},
            "timeline": [],
        }

    # ── 世界 ──

    @property
    def world(self) -> dict:
        return self._data.setdefault("world", {})

    def add_location(self, name: str, info: dict) -> None:
        self.world.setdefault("locations", {})[name] = info

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
        self.items_catalog[name] = info

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
            logger.info("移除 %s 物品: %s", char_name, item_name)
        return removed

    # ── 时间线 ──

    @property
    def timeline(self) -> list:
        return self._data.setdefault("timeline", [])

    def add_timeline_event(self, chapter: int, event: str, location: str = "") -> None:
        self.timeline.append({
            "chapter": chapter,
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

    def apply_llm_update(self, update_json: dict) -> list[str]:
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
        for name, updates in update_json.get("characters", {}).items():
            if name in self.characters:
                self.update_character(name, updates)
                logs.append(f"更新角色 {name}")
            else:
                self.add_character(name, updates)
                logs.append(f"新增角色 {name}")

        # 物品操作
        items = update_json.get("items", {})
        for item in items.get("new", []):
            self.add_item(item["name"], item)
            logs.append(f"新物品: {item['name']}")
        for op in items.get("give", []):
            item_info = self.get_item(op["item"])
            self.give_item(op["character"], op["item"], item_info)
            logs.append(f"给予 {op['character']}: {op['item']}")
        for op in items.get("remove", []):
            if self.remove_item(op["character"], op["item"]):
                logs.append(f"移除 {op['character']}: {op['item']}")

        # 新地点
        for loc_name, loc_info in update_json.get("locations", {}).items():
            self.add_location(loc_name, loc_info)
            logs.append(f"新地点: {loc_name}")

        # 时间线
        for event in update_json.get("timeline", []):
            self.add_timeline_event(
                event.get("chapter", 0),
                event["event"],
                event.get("location", ""),
            )
            logs.append(f"时间线: {event['event']}")

        # 日期
        if update_json.get("date"):
            self.set_date(update_json["date"])
            logs.append(f"日期: {update_json['date']}")

        return logs
