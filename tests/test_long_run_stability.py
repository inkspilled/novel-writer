"""连续多章感知/panel 稳定性：30 章 fake LLM，检查正典链与 panel 落盘。"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from src.novel_writer.core.agents.base import AgentConfig, BaseAgent
from src.novel_writer.core.canon.events import CanonLog
from src.novel_writer.core.canon.extract import extract_percepts
from src.novel_writer.core.characters.panel import run_character_panel
from src.novel_writer.core.characters.registry import CharacterRegistry, CharacterTier
from src.novel_writer.core.llm.base import BaseLLM, LLMResponse


class LongRunLLM(BaseLLM):
    def __init__(self):
        super().__init__(model="fake")

    async def chat(self, messages, temperature=0.7, max_tokens=4096, thinking="auto", tools=None):
        text = "\n".join(str(m.content or "") for m in messages)
        if "你是小说场记" in text:
            m = re.search(r"第(\d+)章", text)
            n = int(m.group(1)) if m else 1
            return LLMResponse(content=json.dumps({
                "scene": "药圃",
                "events": [{
                    "actor": "林牧", "targets": [], "type": "action",
                    "observable": f"第{n}章林牧在药圃查看符文", "hidden": "",
                    "visibility": "participants", "known_by": ["林牧"],
                }],
            }, ensure_ascii=False))
        if "你是小说导演" in text:
            return LLMResponse(content=json.dumps({
                "choice": "林牧", "selected_beats": ["查看符文"],
                "scores": {"drama": 7, "consistency": 9, "surprise": 5},
                "reason": "推进主线",
            }, ensure_ascii=False))
        return LLMResponse(content=json.dumps({
            "intent": "查清符文", "action": "查看符文", "dialogue": "",
            "knowledge_used": [], "confidence": 0.7,
        }, ensure_ascii=False))

    async def stream_chat(self, messages, temperature=0.7, max_tokens=4096, thinking="auto", tools=None):
        if False:  # pragma: no cover
            yield "", ""


def test_30_chapters_percept_and_panel(tmp_path: Path):
    llm = LongRunLLM()
    agent = BaseAgent(AgentConfig(name="d", role="d", title="导演", system_prompt="s"), llm)
    reg = CharacterRegistry(tmp_path)
    reg.create("林牧", chapter=1, tier=CharacterTier.CORE, auto_sim=True)

    for n in range(1, 31):
        text = f"# 第{n}章\n林牧在药圃查看符文。"
        events = asyncio.run(extract_percepts(tmp_path, n, text, agent, registry=reg))
        assert len(events) == 1
        panel = asyncio.run(run_character_panel(tmp_path, n, agent, registry=reg))
        assert panel and panel["director"]["choice"] == "林牧"

    canon = CanonLog(tmp_path)
    assert len(canon.all()) == 30
    assert canon.verify_chain()
    assert len(list((tmp_path / "sim_cache" / "pov").glob("panel_*.json"))) == 30
