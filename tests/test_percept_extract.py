"""章节感知抽取：正文 → CanonEvent，且不泄漏他人的 hidden。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.novel_writer.core.canon.events import CanonLog
from src.novel_writer.core.characters.registry import CharacterRegistry, CharacterTier
from src.novel_writer.core.characters.view import CharacterViewBuilder
from src.novel_writer.core.llm.base import LLMResponse
from src.novel_writer.core.workflow import builtin_steps as bs
from src.novel_writer.core.workflow.definition import WorkflowStep


class FakeAgent:
    title = "审核"

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    async def run(self, prompt, **kwargs):
        self.calls += 1
        return LLMResponse(content=json.dumps(self.payload, ensure_ascii=False))


class FakeRunner:
    def __init__(self, project_dir: Path, agent):
        self.project_dir = project_dir
        self.agent = agent
        self._stop = False
        self.on_step_start = None
        self.on_step_end = None

    def find_agent(self, skill):
        return self.agent


def _project(tmp_path: Path):
    reg = CharacterRegistry(tmp_path)
    reg.create("林牧", chapter=1, tier=CharacterTier.CORE)
    reg.create("楚夜", chapter=1, tier=CharacterTier.CORE)
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    (chapters / "1_开端.txt").write_text(
        "# 第1章 开端\n\n林牧看见楚夜从药圃离开，掌心符文发烫。", encoding="utf-8"
    )
    return reg


def test_percept_extract_writes_events_and_isolates_hidden(tmp_path: Path):
    _project(tmp_path)
    payload = {
        "scene": "青玄宗药圃",
        "events": [
            {
                "actor": "楚夜", "targets": [], "type": "action",
                "observable": "楚夜从药圃离开", "hidden": "我去取魔尊传承",
                "visibility": "private", "known_by": ["楚夜"],
            },
            {
                "actor": "林牧", "targets": ["楚夜"], "type": "reveal",
                "observable": "林牧看见楚夜离开", "hidden": "",
                "visibility": "participants", "known_by": ["林牧", "楚夜"],
            },
            {
                "actor": "不存在的人", "observable": "路人走过",
                "hidden": "", "known_by": ["林牧"],
            },
        ],
    }
    agent = FakeAgent(payload)
    runner = FakeRunner(tmp_path, agent)
    step = WorkflowStep(id="percept_extract", needs="剧情审查")

    asyncio.run(bs._run_percept_extract(runner, step, 1))

    canon = CanonLog(tmp_path)
    events = canon.for_chapter(1)
    assert len(events) == 2  # 未知 actor 被丢弃
    assert canon.has_percepts(1)

    builder = CharacterViewBuilder(tmp_path)
    lin = builder.build("林牧", 1)
    assert "林牧看见楚夜离开" in lin.text
    assert "魔尊传承" not in lin.text
    chu = builder.build("楚夜", 1)
    assert "魔尊传承" in chu.text  # 自己的 hidden 自己知道

    # 幂等：第二次不再产生事件
    before = len(canon.all())
    asyncio.run(bs._run_percept_extract(runner, step, 1))
    assert len(CanonLog(tmp_path).all()) == before


def test_percept_extract_seeds_registry_from_characters_doc(tmp_path: Path):
    (tmp_path / "planning").mkdir()
    (tmp_path / "planning" / "人物设定.md").write_text(
        "## 林牧\n性格：谨慎\n动机：查清符文\n\n## 楚夜\n性格：隐忍\n", encoding="utf-8"
    )
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    (chapters / "1_开端.txt").write_text("# 第1章\n林牧醒来。", encoding="utf-8")
    agent = FakeAgent({"scene": "药圃", "events": [{
        "actor": "林牧", "observable": "林牧醒来", "known_by": ["林牧"],
    }]})
    asyncio.run(bs._run_percept_extract(FakeRunner(tmp_path, agent), WorkflowStep(
        id="percept_extract", needs="剧情审查"), 1))
    assert CharacterRegistry(tmp_path).get("林牧") is not None
    assert len(CanonLog(tmp_path).for_chapter(1)) == 1


def test_context_injects_pov_proposals(tmp_path: Path):
    import json
    from src.novel_writer.core import project_io
    from src.novel_writer.core.workflow.context import ContextBuilder

    project_io.init_project_dir(tmp_path)
    pov = tmp_path / "sim_cache" / "pov" / "char_lin"
    pov.mkdir(parents=True)
    (pov / "sim_3.json").write_text(json.dumps({
        "intent": "查清符文来历", "action": "去药圃查看", "_character_name": "林牧",
    }, ensure_ascii=False), encoding="utf-8")
    ctx = ContextBuilder(tmp_path, {"title": "t", "target_chapters": 10}).build(
        ["planning/大纲.md"], 3, step_id="chapter"
    )
    assert "角色当前意图" in ctx
    assert "查清符文来历" in ctx
