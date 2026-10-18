"""离场角色 tick — 每 5 章一次批量推进，不逐角色发 LLM。"""
from __future__ import annotations

import json
from pathlib import Path

from .. import project_io
from ..agents.base import BaseAgent
from ..json_extract import extract_json_object
from ..logger import get_logger
from .registry import CharacterRegistry
from .selection import offscreen_candidates

logger = get_logger(__name__)


def _prompt(chapter: int, candidates) -> str:
    roster = "\n".join(
        f"- {c.name}（{c.tier.value}；目标：{'、'.join(c.goals[:3]) or '未设定'}；"
        f"上次出场：第{c.last_seen or '?'}章）"
        for c in candidates
    )
    return f"""你是小说场记。第{chapter}章主线之外，这些角色最近在做什么？
只写“离场期间的低频进展”，不要抢主线、不要制造新设定、不要写正文。

【离场角色】
{roster}

只输出 JSON：
{{"ticks":[{{"character":"角色名","summary":"1-2句近况","goal_update":"目标变化，没有就空"}}]}}
角色名必须在上面名单里；每人最多 1 条；没有进展就输出空数组。"""


async def run_offscreen_tick(
    project_dir: Path,
    chapter: int,
    base_agent: BaseAgent,
    registry: CharacterRegistry | None = None,
    limit: int = 8,
) -> dict | None:
    reg = registry or CharacterRegistry(project_dir)
    candidates = offscreen_candidates(reg, chapter, limit=limit)
    if not candidates:
        return None
    try:
        resp = await base_agent.run(
            _prompt(chapter, candidates), thinking="off",
            keep_history=False, max_tokens=1600,
        )
    except Exception as e:
        logger.warning("离场 tick 调用失败: %s", e)
        return None
    data = extract_json_object(resp.content or "")
    if not data:
        return None
    allowed = {c.name for c in candidates}
    ticks = []
    for raw in data.get("ticks") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("character") or "").strip()
        if name not in allowed:
            continue
        summary = str(raw.get("summary") or "").strip()[:300]
        if not summary:
            continue
        ticks.append({
            "character": name,
            "summary": summary,
            "goal_update": str(raw.get("goal_update") or "").strip()[:200],
        })
    if not ticks:
        return None
    out = {"chapter": chapter, "ticks": ticks}
    project_io.write_json(project_dir / "sim_cache" / "offscreen" / f"tick_{chapter}.json", out)
    logger.info("离场 tick 第%d章：%d 人", chapter, len(ticks))
    return out
