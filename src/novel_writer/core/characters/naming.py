"""角色名规范化与分档推断（人物设定里的标题常带角色说明）。"""
from __future__ import annotations

import re

_PAREN_RE = re.compile(r"[（(].*?[）)]")


def canonical_name(raw: str) -> str:
    """`宗主·沈玄微（亦师亦敌）` → `沈玄微`；保留傀儡/心魔等独立实体。"""
    name = _PAREN_RE.sub("", raw or "").strip()
    if "·" in name:
        prefix, last = name.rsplit("·", 1)
        prefix, last = prefix.strip(), last.strip()
        if any(k in prefix for k in ("傀儡", "心魔", "幻境", "镜像")):
            return f"{prefix}·{last}"
        return last or prefix
    return name


def infer_tier(raw: str) -> str:
    """按人物设定标题里的角色说明推断 core / recurring / extra。"""
    text = raw or ""
    if "主角" in text or "男主" in text or "女主" in text:
        return "core"
    if any(k in text for k in ("宗主", "旧天道", "终极存在", "大反派")):
        return "core"
    if any(k in text for k in ("傀儡", "心魔", "幻境", "镜像", "群演", "路人")):
        return "extra"
    if any(k in text for k in (
        "反派", "挚友", "伙伴", "执事", "使者", "丹师", "道主",
        "大弟子", "妻", "夫", "亦师亦敌", "卷三", "卷四", "卷二",
    )):
        return "recurring"
    return "extra"
