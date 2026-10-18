"""角色 panel：多 POV 提案 → 知识校验 → 导演裁决。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.novel_writer.core.agents.base import AgentConfig, BaseAgent
from src.novel_writer.core.canon.events import CanonLog, seed_event
from src.novel_writer.core.characters.panel import run_character_panel
from src.novel_writer.core.characters.registry import CharacterRegistry, CharacterTier
from src.novel_writer.core.characters.selection import offscreen_candidates, select_scene_cast
from src.novel_writer.core.llm.base import BaseLLM, LLMResponse


class FakeLLM(BaseLLM):
    def __init__(self, responses):
        super().__init__(model="fake")
        self.responses = list(responses)
        self.calls = []

    async def chat(self, messages, temperature=0.7, max_tokens=4096, thinking="auto", tools=None):
        self.calls.append(messages)
        return self.responses.pop(0)

    async def stream_chat(self, messages, temperature=0.7, max_tokens=4096, thinking="auto", tools=None):
        if False:  # pragma: no cover
            yield "", ""


def _setup(tmp_path: Path):
    reg = CharacterRegistry(tmp_path)
    reg.create("林牧", chapter=1, tier=CharacterTier.CORE, auto_sim=True, location="药圃")
    reg.create("楚夜", chapter=1, tier=CharacterTier.CORE, auto_sim=True, location="山门")
    reg.create("路人", chapter=1, tier=CharacterTier.EXTRA)
    event = CanonLog(tmp_path).append(seed_event(
        1, "药圃", "楚夜", "楚夜从药圃离开", ["林牧", "楚夜"],
    ))
    return reg, event


def test_panel_fan_out_and_director_fan_in(tmp_path: Path):
    reg, event = _setup(tmp_path)
    proposal = {
        "intent": "查清符文", "action": "去药圃查看", "dialogue": "",
        "expected_effects": ["发现线索"], "knowledge_used": [event.id], "confidence": 0.7,
    }
    director = {
        "choice": "林牧", "selected_beats": ["去药圃查看"],
        "scores": {"drama": 8, "consistency": 9, "surprise": 6},
        "reason": "主动查证能推进主线",
    }
    llm = FakeLLM([
        LLMResponse(content=json.dumps(proposal, ensure_ascii=False)),
        LLMResponse(content=json.dumps(proposal, ensure_ascii=False)),
        LLMResponse(content=json.dumps(director, ensure_ascii=False)),
    ])
    base = BaseAgent(AgentConfig(name="director", role="d", title="导演", system_prompt="s"), llm)

    panel = asyncio.run(run_character_panel(tmp_path, 1, base, registry=reg))

    assert panel and panel["director"]["choice"] == "林牧"
    assert len(panel["proposals"]) == 2
    assert (tmp_path / "sim_cache" / "pov" / f"panel_1.json").exists()
    assert len(list((tmp_path / "sim_cache" / "pov").glob("*/sim_1.json"))) == 2
    # 只对 auto_sim 角色提案；路人没进入
    assert {p["_character_name"] for p in panel["proposals"]} == {"林牧", "楚夜"}


def test_selection_and_offscreen_queue(tmp_path: Path):
    reg, _ = _setup(tmp_path)
    cast = select_scene_cast(reg, chapter=3, limit=2)
    assert {c.name for c in cast} == {"林牧", "楚夜"}
    off = offscreen_candidates(reg, chapter=3, exclude=[c.name for c in cast])
    assert all(c.name not in {"林牧", "楚夜"} for c in off)


def test_panel_skips_without_auto_sim(tmp_path: Path):
    reg = CharacterRegistry(tmp_path)
    reg.create("林牧", chapter=1)
    llm = FakeLLM([])
    base = BaseAgent(AgentConfig(name="d", role="d", title="d", system_prompt="s"), llm)
    assert asyncio.run(run_character_panel(tmp_path, 1, base, registry=reg)) is None
    assert llm.calls == []
