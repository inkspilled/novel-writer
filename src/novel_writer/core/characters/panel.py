"""角色 panel — 多角色 POV 提案 + 导演裁决。

流程（对齐 DSH 的 fan-out/fan-in）：
1. 选角（每场最多 4 人）；
2. 每个角色用隔离 CharacterView 写自己的提案；
3. 裁判校验 knowledge_used 必须来自视图；
4. 导演按戏剧性/一致性/意外性评分，选/合并一个走向；
5. 落盘 sim_cache/pov/{id}/sim_{n}.json 与 panel_{n}.json。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .. import project_io
from ..agents.base import AgentConfig, BaseAgent
from ..json_extract import extract_json_object
from ..llm.base import LLMResponse
from ..logger import get_logger
from .registry import CharacterRecord, CharacterRegistry
from .view import (
    CharacterViewBuilder, build_character_system_prompt, validate_knowledge_used,
)

logger = get_logger(__name__)

PANEL_MAX_CHARS = 4
_PROPOSAL_TOKENS = 1200


def _proposal_prompt(location: str, chapter: int) -> str:
    return (
        "现在是此刻。你在" + (location or "当前场景") + "。\n"
        "根据你已知的信息，决定你此刻想做什么。只输出 JSON：\n"
        "{\n"
        '  "intent": "你的真实目的（一句话）",\n'
        '  "action": "你准备做的具体动作",\n'
        '  "dialogue": "你要说的话（没有就空字符串）",\n'
        '  "expected_effects": ["你预期会造成的变化"],\n'
        '  "knowledge_used": ["你依据的【你记得的事】方括号里的 ID"],\n'
        '  "confidence": 0.0\n'
        "}"
    )


def _director_prompt(chapter: int, proposals: list[dict], scene_brief: str) -> str:
    return (
        f"你是小说导演。以下是第{chapter}章同一场戏的角色提案。\n"
        "请选出或合并一个**最有戏剧性、且不违反人设/已知信息**的走向。\n"
        "评分维度：drama（冲突/代价）、consistency（人设/知识边界）、surprise（意外但不突兀）。\n"
        "只输出 JSON：\n"
        '{"choice":"角色名或 merge","selected_beats":["选中的行动/台词"],'
        '"scores":{"drama":1,"consistency":1,"surprise":1},"reason":"一句话"}\n\n'
        + (f"【公开场景】{scene_brief}\n\n" if scene_brief else "")
        + "【角色提案】\n"
        + json.dumps(proposals, ensure_ascii=False, indent=2)
    )


async def _propose(
    project_dir: Path,
    chapter: int,
    rec: CharacterRecord,
    base_agent: BaseAgent,
    builder: CharacterViewBuilder,
) -> dict | None:
    view = builder.build(rec, chapter)
    cfg = AgentConfig(
        name=rec.id, role="character", title=rec.name,
        system_prompt=build_character_system_prompt(rec),
        skills=[], temperature=0.8, max_tokens=_PROPOSAL_TOKENS,
    )
    char_agent = BaseAgent(cfg, base_agent.llm)
    try:
        resp = await char_agent.run(
            _proposal_prompt(rec.location, chapter),
            context=view.text, thinking="off", keep_history=False,
        )
    except Exception as e:
        logger.warning("角色 %s 提案失败: %s", rec.name, e)
        return None
    data = extract_json_object(resp.content or "")
    if not data:
        return None
    ok, reason = validate_knowledge_used(data, view)
    if not ok:
        logger.warning("角色 %s 提案未通过知识校验: %s", rec.name, reason)
        return None
    data["_character"] = rec.id
    data["_character_name"] = rec.name
    data["_chapter"] = chapter
    data["_view_event_ids"] = sorted(view.event_ids)
    return data


async def run_character_panel(
    project_dir: Path,
    chapter: int,
    base_agent: BaseAgent,
    registry: CharacterRegistry | None = None,
    *,
    scene_brief: str = "",
    limit: int = PANEL_MAX_CHARS,
) -> dict | None:
    reg = registry or CharacterRegistry(project_dir)
    cast = reg.simulatable(limit=limit)  # 只对显式 auto_sim 的角色发起调用
    if not cast:
        return None
    builder = CharacterViewBuilder(project_dir, registry=reg)
    # 多角色提案彼此独立：并行 fan-out，导演再串行 fan-in
    results = await asyncio.gather(
        *[_propose(project_dir, chapter, rec, base_agent, builder) for rec in cast],
        return_exceptions=True,
    )
    proposals: list[dict] = []
    for rec, data in zip(cast, results):
        if isinstance(data, Exception):
            logger.warning("角色 %s 提案异常: %s", rec.name, data)
            continue
        if data:
            proposals.append(data)
            out = project_dir / "sim_cache" / "pov" / rec.id / f"sim_{chapter}.json"
            project_io.write_json(out, data)
    if not proposals:
        return None

    decision: dict[str, Any] = {}
    try:
        resp: LLMResponse = await base_agent.run(
            _director_prompt(chapter, proposals, scene_brief),
            thinking="auto", keep_history=False, max_tokens=2048,
        )
        decision = extract_json_object(resp.content or "") or {}
    except Exception as e:
        logger.warning("导演裁决失败，退回最高 confidence: %s", e)
    if not decision.get("choice"):
        best = max(proposals, key=lambda p: float(p.get("confidence") or 0))
        decision = {
            "choice": best["_character_name"],
            "selected_beats": [best.get("action") or best.get("intent") or ""],
            "scores": {}, "reason": "导演输出不可用，按 confidence 兜底",
        }
    panel = {
        "chapter": chapter, "scene_brief": scene_brief,
        "proposals": proposals, "director": decision,
    }
    project_io.write_json(project_dir / "sim_cache" / "pov" / f"panel_{chapter}.json", panel)
    logger.info("[PANEL] 第%d章 %d 份提案，导演选择 %s",
                chapter, len(proposals), decision.get("choice"))
    return panel
