"""角色选角 — 每场只让 3–5 个角色进入 POV，其余留在离场池。"""
from __future__ import annotations

from .registry import CharacterRecord, CharacterRegistry, CharacterStatus, CharacterTier


def select_scene_cast(
    registry: CharacterRegistry,
    chapter: int,
    participants: list[str] | tuple[str, ...] = (),
    limit: int = 4,
) -> list[CharacterRecord]:
    """在场优先；其次 auto_sim/最近出场；同一角色不重复。"""
    limit = max(1, limit)
    picked: list[CharacterRecord] = []
    for name in participants:
        rec = registry.get(name)
        if rec and rec.can_act and rec not in picked:
            picked.append(rec)
    for rec in registry.simulatable(limit=64):
        if len(picked) >= limit:
            break
        if rec not in picked:
            picked.append(rec)
    if len(picked) < limit:
        order = {CharacterTier.CORE: 0, CharacterTier.RECURRING: 1, CharacterTier.EXTRA: 2}
        rest = [
            c for c in registry.all()
            if c.can_act and c not in picked
            and c.status != CharacterStatus.DORMANT
            and c.last_seen and chapter - c.last_seen <= 10
        ]
        rest.sort(key=lambda c: (order.get(c.tier, 9), -c.last_seen))
        picked.extend(rest[: limit - len(picked)])
    return picked[:limit]


def offscreen_candidates(
    registry: CharacterRegistry,
    chapter: int,
    exclude: list[str] | tuple[str, ...] = (),
    limit: int = 8,
) -> list[CharacterRecord]:
    """离场但有近期活跃痕迹的角色；供世界 tick 排队，不直接发 LLM。"""
    excluded = {registry.get(n).id for n in exclude if registry.get(n)}
    recs = [
        c for c in registry.all()
        if c.can_act and c.id not in excluded
        and c.tier in (CharacterTier.CORE, CharacterTier.RECURRING)
        and c.last_seen and chapter - c.last_seen <= 15
    ]
    recs.sort(key=lambda c: (c.last_seen, c.name), reverse=True)
    return recs[: max(1, limit)]
