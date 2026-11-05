"""章后抽取（chapter_digest）与章后并行的回归测试。

覆盖：
- 一次抽取产出四段，四个消费步骤不再各自调 LLM；
- 抽取失败/缺字段时消费步骤回退到原有 LLM 路径；
- `_group_parallel` 只合并相邻且标了 parallel_ok 的步骤；
- 章后并行步骤的调用时间确实重叠。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from src.novel_writer.core import project_io
from src.novel_writer.core.agents.base import AgentConfig, BaseAgent
from src.novel_writer.core.canon.events import CanonLog
from src.novel_writer.core.llm.base import LLMResponse
from src.novel_writer.core.workflow import WorkflowDef, WorkflowRunner, WorkflowStep
from src.novel_writer.core.workflow.builtin_steps import BUILTIN_STEP_HANDLERS
from src.novel_writer.core.workflow.digest import (
    digest_path,
    load_digest,
    needs_digest,
    normalize_digest,
)
from src.novel_writer.core.world_state import WorldState


# ── 测试替身 ──

class FakeAgent(BaseAgent):
    """返回固定文本，并记录调用次数。"""

    def __init__(self, config: AgentConfig, content: str = "普通回复"):
        super().__init__(config, llm=None)
        self.content = content
        self.calls: list[str] = []

    async def run(self, user_input, context="", thinking=None, keep_history=True,
                  max_tokens=None):
        self.calls.append(user_input)
        return LLMResponse(content=self.content, usage={"total_tokens": 10})


class TimingAgent(BaseAgent):
    """在 run 里 sleep 并记录起止时间，用于验证并发。"""

    def __init__(self, config: AgentConfig, content: str, delay: float = 0.12):
        super().__init__(config, llm=None)
        self.content = content
        self.delay = delay
        self.starts: list[float] = []
        self.ends: list[float] = []

    async def run(self, user_input, context="", thinking=None, keep_history=True,
                  max_tokens=None):
        self.starts.append(time.monotonic())
        await asyncio.sleep(self.delay)
        self.ends.append(time.monotonic())
        return LLMResponse(content=self.content, usage={})


class RoutedAgent(BaseAgent):
    """写正文请求返回正文，章后抽取请求返回四段 JSON。"""

    def __init__(self, config: AgentConfig, digest: dict, chapter_text: str):
        super().__init__(config, llm=None)
        self.digest = digest
        self.chapter_text = chapter_text
        self.calls: list[str] = []

    async def run(self, user_input, context="", thinking=None, keep_history=True,
                  max_tokens=None):
        self.calls.append(user_input)
        if "书记员" in user_input:
            return LLMResponse(content=json.dumps(self.digest, ensure_ascii=False), usage={})
        return LLMResponse(content=self.chapter_text, usage={})


def make_agent(content: str = "普通回复", skill: str = "剧情审查") -> FakeAgent:
    return FakeAgent(
        AgentConfig(name="a", role="r", title="助手", system_prompt="s", skills=[skill]),
        content=content,
    )


def make_runner(tmp_path: Path, agent: BaseAgent) -> WorkflowRunner:
    return WorkflowRunner(
        agents={"a": agent},
        project_dir=tmp_path,
        project_info={"title": "测试书", "target_chapters": 1},
        enable_tools=False,
    )


DIGEST = {
    "summary": "## 第1章 初章 概要\n- **地点**：山门\n- **承接点**：门后有异响",
    "world": {
        "characters": {"林乐天": {"location": "山门", "goals": ["入门"]}},
        "timeline": [{"event": "抵达山门", "location": "山门"}],
    },
    "percept": {
        "scene": "山门/清晨",
        "events": [{
            "actor": "林乐天", "targets": [], "type": "action",
            "observable": "推开山门", "hidden": "",
            "visibility": "public", "known_by": ["林乐天"],
        }],
    },
    "memory": {
        "character_state": [{"subject": "林乐天", "aspect": "位置", "value": "山门"}],
        "open_loops": [], "resolved": [], "reader_promises": [],
        "relationships": [], "world_rules": [],
    },
    "threads": {
        "main": [{"title": "拜入宗门", "status": "active"}],
        "subplot": [],
    },
}


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    for d in ("planning", "chapters", "review"):
        (tmp_path / d).mkdir()
    (tmp_path / "planning" / "人物设定.md").write_text(
        "# 人物设定\n## 林乐天\n主角，性格谨慎。\n", encoding="utf-8",
    )
    (tmp_path / "chapters" / "1_初章.txt").write_text(
        "# 初章\n" + "正" * 600, encoding="utf-8",
    )
    return tmp_path


def run_digest(runner: WorkflowRunner, n: int = 1):
    step = WorkflowStep(id="chapter_digest", needs="", every=1, optional=True)
    return asyncio.run(BUILTIN_STEP_HANDLERS["chapter_digest"](runner, step, n))


# ── digest 产出与消费 ──

class TestDigest:
    def test_digest_writes_all_sections(self, project_dir):
        agent = make_agent(json.dumps(DIGEST, ensure_ascii=False))
        runner = make_runner(project_dir, agent)

        run_digest(runner)

        assert len(agent.calls) == 1
        data = load_digest(project_dir, 1)
        assert set(data) >= {"summary", "world", "percept", "memory", "threads"}
        assert data["_source"] == "chapter_digest"

    def test_consumers_do_not_call_llm_again(self, project_dir):
        agent = make_agent(json.dumps(DIGEST, ensure_ascii=False))
        runner = make_runner(project_dir, agent)
        run_digest(runner)

        asyncio.run(BUILTIN_STEP_HANDLERS["chapter_summary"](
            runner, WorkflowStep(id="chapter_summary", every=1), 1))
        asyncio.run(BUILTIN_STEP_HANDLERS["world_state_update"](
            runner, WorkflowStep(id="world_state_update", every=1), 1))
        asyncio.run(BUILTIN_STEP_HANDLERS["percept_extract"](
            runner, WorkflowStep(id="percept_extract", needs="剧情审查", every=1), 1))
        memory = asyncio.run(runner._llm_sediment(1, "正文"))

        assert len(agent.calls) == 1, "四个消费步骤不应再各自调用 LLM"
        summary_path = project_io.chapter_summary_path(project_dir, 1, "初章")
        assert "山门" in summary_path.read_text(encoding="utf-8")

        gs = WorldState(project_dir)
        gs.load()
        assert gs.characters["林乐天"]["location"] == "山门"

        assert CanonLog(project_dir).has_percepts(1)
        assert memory == DIGEST["memory"]

    def test_runner_applies_digest_threads(self, project_dir):
        """digest 的 threads 段由 runner 写回台账，不需要人工改。"""
        from src.novel_writer.core.story_threads import (
            load_story_threads, sync_story_threads,
        )

        (project_dir / "planning" / "主线.md").write_text(
            "# 主线\n- 拜入宗门（第1章）\n", encoding="utf-8",
        )
        sync_story_threads(project_dir)
        agent = make_agent(json.dumps(DIGEST, ensure_ascii=False))
        runner = make_runner(project_dir, agent)
        run_digest(runner)

        runner._sync_story_ledger(1)
        main = load_story_threads(project_dir).find_by_title("拜入宗门", "main")
        assert main is not None
        assert main.status == "active"

    def test_second_run_skips_when_digest_complete(self, project_dir):
        agent = make_agent(json.dumps(DIGEST, ensure_ascii=False))
        runner = make_runner(project_dir, agent)
        run_digest(runner)
        assert not needs_digest(project_dir, 1)
        run_digest(runner)
        assert len(agent.calls) == 1

        # 润色重跑导致正文长度大幅变化 → digest 过期，需要重抽
        (project_dir / "chapters" / "1_初章.txt").write_text(
            "# 初章\n" + "新" * 1200, encoding="utf-8",
        )
        run_digest(runner)
        assert len(agent.calls) == 2

    def test_invalid_json_falls_back(self, project_dir):
        agent = make_agent("这不是 JSON，只是解释")
        runner = make_runner(project_dir, agent)

        run_digest(runner)
        assert not digest_path(project_dir, 1).exists()

        asyncio.run(BUILTIN_STEP_HANDLERS["chapter_summary"](
            runner, WorkflowStep(id="chapter_summary", every=1), 1))
        # 抽取 2 次 + 概要回退 1 次
        assert len(agent.calls) == 3
        summary_path = project_io.chapter_summary_path(project_dir, 1, "初章")
        assert summary_path.exists() and summary_path.stat().st_size > 0

    def test_normalize_drops_wrong_types(self):
        assert normalize_digest({"summary": 1, "world": [], "percept": {"events": "x"},
                                 "memory": {"foo": 1}}) == {}
        assert set(normalize_digest(DIGEST)) == {
            "summary", "world", "percept", "memory", "threads",
        }
        # 空 world 合法：表示本章没有世界状态变化，不能当成「缺这一段」
        assert normalize_digest({"world": {}}) == {"world": {}}

    def test_prompt_includes_threads_context(self):
        from src.novel_writer.core.workflow.prompts import build_chapter_digest_prompt

        prompt = build_chapter_digest_prompt(
            3, "正文", threads_context="- 拜入宗门｜推进中｜第1章起",
        )
        assert "拜入宗门" in prompt
        assert '"threads"' in prompt
        assert "main" in prompt and "subplot" in prompt


# ── 并行分组 ──

class TestDigestWiring:
    def test_runner_wires_digest_into_consumers(self, tmp_path):
        """整条工作流跑一遍：正文 1 次 + 抽取 1 次，消费步骤 0 次。"""
        for d in ("planning", "chapters", "review"):
            (tmp_path / d).mkdir()
        (tmp_path / "planning" / "人物设定.md").write_text(
            "# 人物设定\n## 林乐天\n主角，性格谨慎。\n", encoding="utf-8",
        )
        agent = RoutedAgent(
            AgentConfig(name="w", role="r", title="写手", system_prompt="s",
                        skills=["正文写作", "剧情审查"]),
            digest=DIGEST,
            chapter_text="# 初章\n" + "正" * 600,
        )
        runner = WorkflowRunner(
            agents={"w": agent}, project_dir=tmp_path,
            project_info={"title": "测试书", "target_chapters": 1},
            enable_tools=False,
        )
        wf = WorkflowDef(
            name="digest 接线",
            project={"title": "测试书", "target_chapters": 1},
            steps=[
                WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                             output="chapters/{n}_chapter.txt", repeat=1),
                WorkflowStep(id="chapter_digest", every=1, optional=True, parallel_ok=True),
                WorkflowStep(id="chapter_summary", every=1, optional=True,
                             parallel_ok=True, depends_on=["chapter_digest"]),
                WorkflowStep(id="world_state_update", every=1, optional=True),
                WorkflowStep(id="percept_extract", needs="剧情审查", every=1, optional=True),
            ],
        )
        asyncio.run(runner.run(wf, {}))

        assert len(agent.calls) == 2, "只有写正文与章后抽取两次调用"
        assert sum(1 for c in agent.calls if "书记员" in c) == 1
        assert digest_path(tmp_path, 1).exists()

        summary_path = project_io.chapter_summary_path(tmp_path, 1, "初章")
        assert summary_path.exists() and "山门" in summary_path.read_text(encoding="utf-8")

        gs = WorldState(tmp_path)
        gs.load()
        assert gs.characters["林乐天"]["location"] == "山门"
        assert CanonLog(tmp_path).has_percepts(1)


class TestParallelGrouping:
    def test_only_consecutive_parallel_steps_merge(self):
        a = WorkflowStep(id="a", parallel_ok=True)
        b = WorkflowStep(id="b", parallel_ok=True)
        c = WorkflowStep(id="c")
        d = WorkflowStep(id="d", parallel_ok=True)
        e = WorkflowStep(id="e", parallel_ok=True)
        groups = WorkflowRunner._group_parallel([a, b, c, d, e])
        assert [[s.id for s in g] for g in groups] == [["a", "b"], ["c"], ["d", "e"]]

    def test_dependency_splits_batch(self):
        digest = WorkflowStep(id="chapter_digest", parallel_ok=True)
        proofread = WorkflowStep(id="proofread", parallel_ok=True)
        summary = WorkflowStep(id="chapter_summary", parallel_ok=True,
                               depends_on=["chapter_digest"])
        groups = WorkflowRunner._group_parallel([digest, proofread, summary])
        assert [[s.id for s in g] for g in groups] == [
            ["chapter_digest", "proofread"], ["chapter_summary"],
        ]

    def test_post_steps_really_overlap(self, project_dir):
        writer = TimingAgent(
            AgentConfig(name="w", role="r", title="写手", system_prompt="s",
                        skills=["正文写作"]),
            content="# 初章\n" + "正" * 600,
        )
        reporter = TimingAgent(
            AgentConfig(name="p", role="r", title="报告", system_prompt="s",
                        skills=["报告"]),
            content="# 报告\n正常",
        )
        reviewer = FakeAgent(
            AgentConfig(name="v", role="r", title="审核", system_prompt="s",
                        skills=["剧情审查"]),
            content="不是 JSON，触发正则兜底",
        )
        runner = WorkflowRunner(
            agents={"w": writer, "p": reporter, "v": reviewer},
            project_dir=project_dir,
            project_info={"title": "测试书", "target_chapters": 1},
            enable_tools=False,
        )
        wf = WorkflowDef(
            name="并发测试",
            project={"title": "测试书", "target_chapters": 1},
            steps=[
                WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                             output="chapters/{n}_chapter.txt", repeat=1),
                WorkflowStep(id="report_a", needs="报告", prompt="A",
                             output="review/a.md", every=1, parallel_ok=True),
                WorkflowStep(id="report_b", needs="报告", prompt="B",
                             output="review/b.md", every=1, parallel_ok=True),
            ],
        )
        asyncio.run(runner.run(wf, {}))

        assert len(reporter.starts) == 2
        assert max(reporter.starts) < min(reporter.ends), "两个章后步骤应并发而非串行"
        assert (project_dir / "review" / "a.md").exists()
        assert (project_dir / "review" / "b.md").exists()
