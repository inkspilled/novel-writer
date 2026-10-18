"""角色 POV 视图 — 只包含“这个角色知道的东西”。

硬约束：
- 不读 planning/大纲/人物设定/伏笔，不读 world_state/memory 全局表；
- 只从 CanonLog 取 `known_by` 包含该角色的事件（本人事件含自己的 hidden）；
- 别人事件的 `hidden` 一律剥离；
- 死亡角色按 `knowledge_cutoff` 冻结；
- 不输出“你不知道 X”这类反向泄漏，只给“你知道的”。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..canon.events import CanonEvent, CanonLog
from .registry import CharacterRecord, CharacterRegistry


@dataclass
class CharacterView:
    character_id: str
    name: str
    chapter: int
    identity: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    goals: list[str] = field(default_factory=list)
    relationships: dict[str, Any] = field(default_factory=dict)
    percepts: list[dict[str, Any]] = field(default_factory=list)
    beliefs: list[str] = field(default_factory=list)
    event_ids: set[str] = field(default_factory=set)
    scene_brief: str = ""
    text: str = ""

    def contains(self, term: str) -> bool:
        return bool(term) and term in self.text

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "name": self.name,
            "chapter": self.chapter,
            "identity": self.identity,
            "state": self.state,
            "goals": self.goals,
            "relationships": self.relationships,
            "percepts": self.percepts,
            "beliefs": self.beliefs,
            "event_ids": sorted(self.event_ids),
        }


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def render_view(view: CharacterView) -> str:
    """渲染为角色可读的第一人称上下文（不含任何作者视角标签）。"""
    parts = [f"你是{view.name}。"]
    if view.identity:
        parts.append("【你的身份】")
        for k, v in view.identity.items():
            parts.append(f"- {k}：{_json_text(v)}")
    if view.state:
        parts.append("【你此刻的状态】")
        for k, v in view.state.items():
            parts.append(f"- {k}：{_json_text(v)}")
    if view.goals:
        parts.append("【你想要的】")
        parts.extend(f"- {g}" for g in view.goals if g)
    if view.relationships:
        parts.append("【你与他人的关系】")
        for name, rel in view.relationships.items():
            parts.append(f"- {name}：{_json_text(rel)}")
    if view.percepts:
        parts.append("【你记得的事】")
        for p in view.percepts:
            where = p.get("scene") or "此前"
            tag = f"[{p['event_id']}] " if p.get("event_id") else ""
            parts.append(f"- {tag}在{where}：{p.get('observed', '')}")
    if view.beliefs:
        parts.append("【你的判断】")
        parts.extend(f"- {b}" for b in view.beliefs if b)
    if view.scene_brief:
        parts.append("【此刻】")
        parts.append(view.scene_brief)
    return "\n".join(parts).strip()


class CharacterViewBuilder:
    """从注册表 + 正典事件构建某个角色的隔离视图。"""

    def __init__(self, project_dir: Path, registry: CharacterRegistry | None = None,
                 canon: CanonLog | None = None):
        self.project_dir = Path(project_dir)
        self.registry = registry or CharacterRegistry(self.project_dir)
        self.canon = canon or CanonLog(self.project_dir)

    def build(
        self,
        character: str | CharacterRecord,
        chapter: int,
        *,
        scene_brief: str = "",
    ) -> CharacterView:
        rec = character if isinstance(character, CharacterRecord) else self.registry.get(character)
        if rec is None:
            raise KeyError(f"角色不存在: {character}")
        limit = rec.knowledge_limit(chapter)
        events = self.canon.known_to(rec.id, limit, names=[rec.name, *rec.aliases])
        percepts: list[dict[str, Any]] = []
        event_ids: set[str] = set()
        beliefs: list[str] = []

        for e in events:
            # 新角色不能“自带”入场前的目击；backstory 事件（本人过去）例外
            if e.chapter < rec.first_seen and e.type != "backstory" and e.actor != rec.id:
                continue
            is_self = e.actor in (rec.id, rec.name) or e.actor in rec.aliases
            if is_self:
                observed = e.observable
                if e.hidden:
                    observed = f"{observed}（你心里的真实想法：{e.hidden}）"
            else:
                observed = e.observable  # 别人的 hidden 永远不进来
            if not observed:
                continue
            event_ids.add(e.id)
            percepts.append({
                "event_id": e.id,
                "scene": e.scene,
                "observed": observed,
                "channel": e.payload.get("channel", "sight"),
            })
            if e.type == "belief" and observed not in beliefs:
                beliefs.append(observed)

        # 角色自己的判断（允许是错的，和正典不一致也没关系）
        for b in rec.private_profile.get("beliefs", []) if isinstance(rec.private_profile, dict) else []:
            if b and b not in beliefs:
                beliefs.append(str(b))

        identity = dict(rec.public_profile or {})
        for key in ("personality", "appearance", "background"):
            val = rec.private_profile.get(key) if isinstance(rec.private_profile, dict) else None
            if val and key not in identity:
                identity[key] = val

        view = CharacterView(
            character_id=rec.id,
            name=rec.name,
            chapter=chapter,
            identity=identity,
            state=dict(rec.state or {}),
            goals=list(rec.goals),
            relationships=dict(rec.relationships),
            percepts=percepts,
            beliefs=beliefs,
            event_ids=event_ids,
            scene_brief=scene_brief,
        )
        view.text = render_view(view)
        return view

    def canary_leak(self, character: str, term: str, chapter: int) -> bool:
        """测试辅助：返回 True 表示泄漏（term 出现在视图中）。"""
        return self.build(character, chapter).contains(term)


def build_character_system_prompt(rec: CharacterRecord) -> str:
    """角色 POV Agent 的系统提示：只描述“你是谁”，不包含作者信息。"""
    identity = rec.public_profile.get("identity") or rec.public_profile.get("background") or ""
    base = f"你是{rec.name}。"
    if identity:
        base += f"{identity}。"
    return (
        base
        + "你只能依据上下文【你记得的事】【你的判断】行动，不得使用任何未出现过的信息。"
        + "不要提及章节、大纲、作者、读者或“剧情”。"
        + "以第一人称思考，只输出 JSON 对象。"
    )


def validate_knowledge_used(proposal: dict[str, Any], view: CharacterView) -> tuple[bool, str]:
    """裁判轻量校验：提案引用的 knowledge_used 必须都在视图内。"""
    if not isinstance(proposal, dict):
        return False, "提案不是 JSON 对象"
    used = proposal.get("knowledge_used") or []
    if not isinstance(used, list):
        return False, "knowledge_used 必须是数组"
    unknown = [eid for eid in used if eid not in view.event_ids]
    if unknown:
        return False, f"引用了视图外的事件: {unknown[:5]}"
    if not (proposal.get("action") or proposal.get("intent")):
        return False, "缺少 action/intent"
    return True, ""


__all__ = [
    "CharacterView", "CharacterViewBuilder", "render_view",
    "build_character_system_prompt", "validate_knowledge_used",
]
