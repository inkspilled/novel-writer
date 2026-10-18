"""章节感知抽取 — 把正文转成角色可感知的 CanonEvent。

原则：
- observable 只写“旁人能看到/听到的”客观行为；
- hidden 只写本人知道的意图/秘密；
- known_by 明确列出在场/被告知的人，不在场的人不能凭空知道；
- 每章最多 12 条，避免把整章拆成流水账。
"""
from __future__ import annotations

import json
from typing import Any

from ..characters.registry import CharacterRecord, CharacterRegistry
from ..json_extract import extract_json_object
from ..logger import get_logger
from .events import CanonEvent, CanonLog

logger = get_logger(__name__)

PERCEPT_MAX_EVENTS = 12
_ALLOWED_TYPES = {"action", "dialogue", "reveal", "death", "state_change", "belief"}
_ALLOWED_VIS = {"private", "participants", "public", "world"}


def build_percept_prompt(chapter_text: str, names: list[str], chapter: int) -> str:
    roster = "、".join(names[:40]) if names else "（未登记）"
    return f"""你是小说场记。请从第{chapter}章正文中抽取“角色可感知的事件”，只输出 JSON。

【角色名单】
{roster}

【输出格式】
{{
  "scene": "地点/时间（一句话）",
  "events": [
    {{
      "actor": "角色名（必须在名单中）",
      "targets": ["相关角色名"],
      "type": "action|dialogue|reveal|death|state_change|belief",
      "observable": "旁人能看到/听到的客观行为或对话（1-2 句）",
      "hidden": "只有 actor 自己知道的真实意图/秘密；没有就空字符串",
      "visibility": "private|participants|public|world",
      "known_by": ["在场或被告知、且确实能知道这件事的角色名"]
    }}
  ]
}}

【硬规则】
1. observable 不写旁白解释、作者评论、上帝视角真相；只写角色能感知到的内容。
2. hidden 不是“秘密设定说明”，而是 actor 此刻的真实意图；别人看不到。
3. known_by 只能列名单中的角色；不在场的人不要写进去。
4. 死亡、重伤、身份揭露、关键承诺/威胁，单独成条。
5. 每章最多 {PERCEPT_MAX_EVENTS} 条；没有可抽取事件时输出空 events 数组。
6. 不要输出 Markdown 代码块以外的解释。

【正文】
{chapter_text[:12000]}
"""


def _canon_name(registry: CharacterRegistry, raw: Any) -> str:
    name = str(raw or "").strip()
    if not name:
        return ""
    rec = registry.get(name)
    return rec.name if rec else ""


def _canon_names(registry: CharacterRegistry, raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        name = _canon_name(registry, item)
        if name and name not in out:
            out.append(name)
    return out


def apply_percept_payload(
    project_dir,
    chapter: int,
    payload: dict[str, Any],
    registry: CharacterRegistry | None = None,
) -> list[CanonEvent]:
    """把 LLM 返回的感知 JSON 校验后写入正典（不可变）。"""
    if not isinstance(payload, dict):
        return []
    reg = registry or CharacterRegistry(project_dir)
    scene = str(payload.get("scene") or "").strip()[:80]
    raw_events = payload.get("events") or []
    if not isinstance(raw_events, list):
        return []

    canon = CanonLog(project_dir)
    written: list[CanonEvent] = []
    for raw in raw_events[:PERCEPT_MAX_EVENTS]:
        if not isinstance(raw, dict):
            continue
        actor = _canon_name(reg, raw.get("actor"))
        if not actor:
            continue
        observable = str(raw.get("observable") or "").strip()[:500]
        hidden = str(raw.get("hidden") or "").strip()[:300]
        if not observable and not hidden:
            continue
        known_by = _canon_names(reg, raw.get("known_by"))
        if actor not in known_by:
            known_by.insert(0, actor)
        visibility = str(raw.get("visibility") or "participants")
        if visibility not in _ALLOWED_VIS:
            visibility = "participants"
        if hidden and not observable:
            visibility = "private"
            known_by = [actor]
        event_type = str(raw.get("type") or "action")
        if event_type not in _ALLOWED_TYPES:
            event_type = "action"
        written.append(canon.append(CanonEvent(
            chapter=chapter,
            scene=scene,
            type=event_type,
            actor=actor,
            targets=_canon_names(reg, raw.get("targets")),
            observable=observable,
            hidden=hidden,
            visibility=visibility,
            known_by=known_by,
            payload={"source": "percept", "chapter": chapter},
        )))
    if written:
        logger.info("第%d章感知抽取写入 %d 条正典事件", chapter, len(written))
    return written


async def extract_percepts(
    project_dir,
    chapter: int,
    chapter_text: str,
    agent,
    registry: CharacterRegistry | None = None,
) -> list[CanonEvent]:
    """调用 LLM 抽事件并落正典；返回写入的事件。"""
    reg = registry or CharacterRegistry(project_dir)
    canon = CanonLog(project_dir)
    if canon.has_percepts(chapter):
        logger.debug("第%d章已有感知事件，跳过", chapter)
        return []
    names = [c.name for c in reg.all()]
    if not names:
        logger.debug("第%d章感知抽取跳过：角色注册表为空", chapter)
        return []
    prompt = build_percept_prompt(chapter_text, names, chapter)
    resp = await agent.run(prompt, thinking="off", keep_history=False, max_tokens=4096)
    payload = extract_json_object(resp.content or "")
    if not payload:
        logger.warning("第%d章感知抽取返回非 JSON，跳过", chapter)
        return []
    return apply_percept_payload(project_dir, chapter, payload, registry=reg)
