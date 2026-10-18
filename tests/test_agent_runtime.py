"""Agent 运行时增强回归：技能包、只读工具、工具循环、重试、日志与会审。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.novel_writer.core.agents.base import AgentConfig, BaseAgent
from src.novel_writer.core.agents.loop import AgentLoop
from src.novel_writer.core.agents.tools import NovelToolkit, Tool, ToolRegistry
from src.novel_writer.core.llm.base import BaseLLM, LLMMessage, LLMResponse
from src.novel_writer.core.skills import SkillRegistry
from src.novel_writer.core.workflow import builtin_steps as bs
from src.novel_writer.core.workflow.definition import WorkflowStep
from src.novel_writer.core.workflow.runner import WorkflowRunner


# ── 技能包 ──

class TestSkills:
    def test_builtin_skills_discovered(self):
        reg = SkillRegistry(None)
        names = {s.name for s in reg.list()}
        assert {"chapter-craft", "dialogue", "battle", "emotion", "suspense", "continuity"} <= names
        skill = reg.get("chapter-craft")
        assert skill and "开篇" in skill.body
        assert "chapter-craft" in reg.catalog_text()

    def test_project_skill_overrides_builtin(self, tmp_path: Path):
        d = tmp_path / "skills"
        d.mkdir()
        (d / "chapter-craft.md").write_text(
            "---\nname: chapter-craft\ndescription: 项目定制\n---\n自定义技能正文",
            encoding="utf-8",
        )
        reg = SkillRegistry(tmp_path)
        assert reg.get("chapter-craft").body == "自定义技能正文"
        # 其它内置技能仍可见
        assert reg.get("dialogue") is not None


# ── 只读工具 ──

class TestNovelTools:
    def _toolkit(self, tmp_path: Path):
        (tmp_path / "planning").mkdir(parents=True)
        (tmp_path / "chapters").mkdir()
        (tmp_path / "planning" / "人物设定.md").write_text("# 人物\n- 林牧：主角", encoding="utf-8")
        (tmp_path / "chapters" / "1_开端.txt").write_text("# 第1章\n林牧醒来。", encoding="utf-8")
        return NovelToolkit(tmp_path, {"target_chapters": 20}, chapter_n=1)

    def test_read_planning_and_chapter(self, tmp_path):
        reg = self._toolkit(tmp_path).build(["read_planning", "read_chapter"])
        assert "林牧" in asyncio.run(reg.execute("read_planning", {"doc": "人物设定"}))
        assert "第1章" in asyncio.run(reg.execute("read_chapter", {"number": 1}))

    def test_unknown_doc_and_tool_return_error_text(self, tmp_path):
        reg = self._toolkit(tmp_path).build()
        assert asyncio.run(reg.execute("read_planning", {"doc": "../../etc/passwd"})).startswith("ERROR")
        assert asyncio.run(reg.execute("nope", {})).startswith("ERROR")

    def test_use_skill(self, tmp_path):
        reg = self._toolkit(tmp_path).build(["use_skill", "list_skills"])
        assert "开篇" in asyncio.run(reg.execute("use_skill", {"name": "chapter-craft"}))
        assert "chapter-craft" in asyncio.run(reg.execute("list_skills", {}))

    def test_registry_truncates_result(self):
        reg = ToolRegistry(max_result_chars=20)
        reg.register(Tool("big", "d", {}, lambda args: "x" * 100))
        out = asyncio.run(reg.execute("big", {}))
        assert out.endswith("（已截断）") and len(out) <= 40


# ── 工具循环 ──

class FakeLLM(BaseLLM):
    def __init__(self, responses):
        super().__init__(model="fake")
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def chat(self, messages, temperature=0.7, max_tokens=4096, thinking="auto", tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        return self.responses.pop(0)

    async def stream_chat(self, messages, temperature=0.7, max_tokens=4096, thinking="auto", tools=None):
        if False:  # pragma: no cover
            yield "", ""


def make_agent(llm):
    return BaseAgent(AgentConfig(name="writer", role="w", title="写手", system_prompt="sys"), llm)


def test_tool_loop_executes_tool_then_answers(tmp_path):
    (tmp_path / "planning").mkdir()
    (tmp_path / "planning" / "人物设定.md").write_text("林牧是主角", encoding="utf-8")
    toolkit = NovelToolkit(tmp_path, {}, 1).build(["read_planning"])
    llm = FakeLLM([
        LLMResponse(content="", tool_calls=[{
            "id": "call_1", "type": "function",
            "function": {"name": "read_planning", "arguments": json.dumps({"doc": "人物设定"})},
        }], usage={"total_tokens": 5}),
        LLMResponse(content="最终正文", usage={"total_tokens": 7}),
    ])
    agent = make_agent(llm)
    events: list[str] = []
    loop = AgentLoop(agent, toolkit, max_rounds=2, on_event=lambda k, p: events.append(k))
    result = asyncio.run(loop.run("写第1章"))

    assert result.content == "最终正文"
    assert result.usage["total_tokens"] == 12
    assert events[:2] == ["tool_round", "tool_call"]
    # 第二次请求里带回了 tool 结果
    roles = [m.role for m in llm.calls[1]["messages"]]
    assert "tool" in roles
    assert llm.calls[0]["tools"]


def test_tool_loop_final_call_without_tools_when_budget_exhausted(tmp_path):
    toolkit = ToolRegistry()
    toolkit.register(Tool("noop", "d", {}, lambda args: "ok"))
    # 每轮都要求调工具，循环耗尽后最后一轮不再给 tools
    llm = FakeLLM([
        LLMResponse(content="", tool_calls=[{"id": "c1", "type": "function",
                                             "function": {"name": "noop", "arguments": "{}"}}]),
        LLMResponse(content="最终", tool_calls=[{"id": "c2", "type": "function",
                                                 "function": {"name": "noop", "arguments": "{}"}}]),
    ])
    agent = make_agent(llm)
    result = asyncio.run(AgentLoop(agent, toolkit, max_rounds=1).run("x"))
    assert result.content == "最终"
    assert llm.calls[-1]["tools"] is None


# ── 工作流：重试/日志/断点顺序/会审 ──

class FlakyAgent(BaseAgent):
    def __init__(self, fail_times: int = 1):
        super().__init__(AgentConfig(name="flaky", role="r", title="写手",
                                     system_prompt="s", skills=["正文写作"]), None)
        self.fail_times = fail_times
        self.calls = 0

    async def run(self, user_input, context="", thinking=None, keep_history=True, max_tokens=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("transient")
        resp = LLMResponse(content="# 第1章\n" + "正" * 200, usage={"total_tokens": 10})
        self._record_usage(resp.usage)
        return resp


def test_step_retry_and_journal(tmp_path):
    agent = FlakyAgent(fail_times=1)
    runner = WorkflowRunner({"a": agent}, tmp_path, {}, enable_tools=False)
    wf = type("WF", (), {})()
    from src.novel_writer.core.workflow.definition import WorkflowDef
    wf = WorkflowDef(name="t", project={}, steps=[
        WorkflowStep(id="chapter", needs="正文写作", prompt="写", output="chapters/{n}_章节.txt",
                     repeat=1, retry=1, retry_delay=0),
    ])
    asyncio.run(runner.run(wf, {}))
    assert agent.calls >= 2  # 1 次失败 + 1 次成功（后续沉淀可能再调用）
    files = list((tmp_path / "runs").glob("*.jsonl"))
    assert files, "应生成运行日志"
    records = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    events = [r["event"] for r in records]
    assert "run_start" in events and "step_retry" in events and "step_end" in events and "run_end" in events
    step_end = next(r for r in records if r["event"] == "step_end")
    assert step_end["usage"]["total_tokens"] == 10
    assert step_end["duration_s"] >= 0


def test_stop_during_post_step_does_not_advance_current(tmp_path):
    agent = FlakyAgent(fail_times=0)
    runner = WorkflowRunner({"a": agent}, tmp_path, {}, enable_tools=False)

    def stop_after_chapter(step_id, n, title, output):
        if step_id == "chapter":
            runner.stop()

    runner.on_step_end = stop_after_chapter
    from src.novel_writer.core.workflow.definition import WorkflowDef
    wf = WorkflowDef(name="t", project={}, steps=[
        WorkflowStep(id="chapter", needs="正文写作", prompt="写", output="chapters/{n}_章节.txt", repeat=1),
    ])
    progress = asyncio.run(runner.run(wf, {}))
    assert "chapter" not in progress, "停止在章后链路时不得把本章标成完成"


def test_review_panel_writes_fan_in_report(tmp_path):
    (tmp_path / "chapters").mkdir()
    for n in (1, 2):
        (tmp_path / "chapters" / f"{n}_第{n}章.txt").write_text(f"# 第{n}章\n正文{n}", encoding="utf-8")

    class PanelAgent:
        title = "审核"

        def __init__(self):
            self.calls = []

        def clear_history(self):
            pass

        async def run(self, prompt, **kwargs):
            self.calls.append(prompt)
            return LLMResponse(content="## 结论\n有问题", usage={"total_tokens": 1})

    agent = PanelAgent()

    class R:
        project_dir = tmp_path
        project_info = {}
        on_step_start = None
        on_step_end = None

        def find_agent(self, skill):
            return agent

    asyncio.run(bs._run_review_panel(R(), WorkflowStep(id="review_panel"), 2))
    report = tmp_path / "review" / "会审报告_第1-2章.md"
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert text.strip()
    # 3 个视角并行 + 1 次主编聚合；聚合 prompt 必须包含三份视角发现
    assert len(agent.calls) == 4
    aggregate_prompt = agent.calls[-1]
    assert "剧情逻辑" in aggregate_prompt
    assert "人物一致性" in aggregate_prompt
    assert "节奏爽点" in aggregate_prompt


def test_dag_groups_and_cycle_detection(tmp_path):
    from src.novel_writer.core.workflow.runner import WorkflowError
    runner = WorkflowRunner({}, tmp_path, {}, enable_tools=False)
    a = WorkflowStep(id="a")
    b = WorkflowStep(id="b", depends_on=["a"])
    c = WorkflowStep(id="c", depends_on=["a"])
    groups = runner._build_step_groups([a, b, c])
    assert [[s.id for s in g] for g in groups] == [["a"], ["b", "c"]]

    x = WorkflowStep(id="x", depends_on=["y"])
    y = WorkflowStep(id="y", depends_on=["x"])
    with pytest.raises(WorkflowError):
        runner._build_step_groups([x, y])


def test_workflow_step_parses_runtime_fields():
    from src.novel_writer.core.workflow.definition import WorkflowDef
    wf = WorkflowDef.from_dict({"steps": [{
        "id": "chapter", "tools": ["read_planning"], "tool_rounds": 3,
        "skills": ["dialogue"], "retry": 2, "retry_delay": 1.5,
        "timeout": 60, "max_tokens": 8192, "phase": "写作", "depends_on": ["outline"],
    }]})
    step = wf.steps[0]
    assert step.tools == ["read_planning"]
    assert step.tool_rounds == 3
    assert step.skills == ["dialogue"]
    assert step.retry == 2 and step.retry_delay == 1.5 and step.timeout == 60
    assert step.max_tokens == 8192
    assert step.phase == "写作" and step.depends_on == ["outline"]


def test_chapter_step_uses_tools_end_to_end(tmp_path):
    (tmp_path / "planning").mkdir()
    (tmp_path / "planning" / "人物设定.md").write_text("林牧是主角", encoding="utf-8")
    llm = FakeLLM([
        LLMResponse(content="", tool_calls=[{
            "id": "c1", "type": "function",
            "function": {"name": "read_planning", "arguments": '{"doc":"人物设定"}'},
        }]),
        LLMResponse(content="# 第1章 开端\n" + "正" * 200),
        LLMResponse(content="不是JSON"),  # 沉淀调用
    ])
    agent = BaseAgent(AgentConfig(name="w", role="w", title="写手",
                                  system_prompt="s", skills=["正文写作"]), llm)
    runner = WorkflowRunner({"w": agent}, tmp_path, {"target_chapters": 2}, enable_tools=True)
    from src.novel_writer.core.workflow.definition import WorkflowDef
    wf = WorkflowDef(name="t", project={}, steps=[WorkflowStep(
        id="chapter", needs="正文写作", prompt="写第{n}章", output="chapters/{n}_章节.txt",
        repeat=1, tools=["read_planning"], tool_rounds=1, retry=0,
    )])
    asyncio.run(runner.run(wf, {}))
    files = list((tmp_path / "chapters").glob("1_*.txt"))
    assert files and "正" in files[0].read_text(encoding="utf-8")
    # 第一次请求带 tools，第二次请求包含 tool 结果
    assert llm.calls[0]["tools"]
    assert any(m.role == "tool" for m in llm.calls[1]["messages"])


def test_parse_expected_chapters_without_zhang_in_parens(tmp_path):
    from src.novel_writer.core.workflow.definition import _parse_expected_chapters
    (tmp_path / "planning").mkdir()
    (tmp_path / "planning" / "立意.md").write_text(
        "【分卷】初入仙途(30)→正邪争锋(40)→独断万古(30)\n", encoding="utf-8"
    )
    assert _parse_expected_chapters(tmp_path) == 100


def test_rejected_chapter_blocks_progress_and_resumes(tmp_path):
    from src.novel_writer.core.workflow.definition import WorkflowDef

    class RejectFirstAgent(BaseAgent):
        def __init__(self):
            super().__init__(AgentConfig(name="w", role="w", title="写手",
                                         system_prompt="s", skills=["正文写作"]), None)
            self.calls = 0

        async def run(self, user_input, context="", thinking=None, keep_history=True, max_tokens=None):
            self.calls += 1
            if "第1章" in user_input:
                return LLMResponse(content="# 第1章\n短", usage={"total_tokens": 1})
            return LLMResponse(content="# 第2章\n" + "正" * 200, usage={"total_tokens": 1})

    wf = WorkflowDef(name="t", project={}, steps=[WorkflowStep(
        id="chapter", needs="正文写作", prompt="写第{n}章", output="chapters/{n}_章节.txt",
        repeat=2, retry=0,
    )])

    bad = RejectFirstAgent()
    runner = WorkflowRunner({"w": bad}, tmp_path, {}, enable_tools=False)

    async def no_sediment(n, progress):
        return True

    runner._run_sediment = no_sediment
    progress = asyncio.run(runner.run(wf, {}))

    # 第 1 章被拒：不得把 current 推过这个洞；第 2 章仍可继续写
    assert progress.get("chapter") is None
    assert not list((tmp_path / "chapters").glob("1_*.txt"))
    assert list((tmp_path / "chapters").glob("2_*.txt"))

    # 恢复：从第 1 章重试，成功后跳过已存在的第 2 章，current=2
    good = RejectFirstAgent()
    good.run = lambda *a, **k: _good_response()  # type: ignore[assignment]
    runner2 = WorkflowRunner({"w": good}, tmp_path, {}, enable_tools=False)
    runner2._run_sediment = no_sediment
    progress2 = asyncio.run(runner2.run(wf, dict(progress)))
    assert list((tmp_path / "chapters").glob("1_*.txt"))
    assert progress2["chapter"]["current"] == 2


def _good_response():
    async def _inner():
        return LLMResponse(content="# 第1章\n" + "正" * 200)
    return _inner()


def test_llm_message_and_tool_call_serialization():
    from src.novel_writer.core.llm.client import _serialize_messages, _serialize_tool_calls
    from src.novel_writer.core.llm.base import LLMMessage

    class Fn:
        name = "read_planning"
        arguments = {"doc": "大纲"}

    class TC:
        id = "call_9"
        function = Fn()

    calls = _serialize_tool_calls([TC()])
    assert calls[0]["id"] == "call_9"
    assert calls[0]["function"]["arguments"] == '{"doc": "大纲"}'

    msgs = _serialize_messages([
        LLMMessage(role="assistant", content="", tool_calls=calls),
        LLMMessage(role="tool", content="结果", tool_call_id="call_9"),
    ])
    assert msgs[0]["tool_calls"] == calls
    assert msgs[1]["tool_call_id"] == "call_9"


def test_tool_markup_in_content_retries_without_tools(tmp_path):
    from src.novel_writer.core.workflow.definition import WorkflowStep
    from src.novel_writer.core.workflow.runner import WorkflowRunner

    llm = FakeLLM([
        LLMResponse(content="<tool_call><function=read_planning><parameter=doc>主线</parameter></function></tool_call>"),
        LLMResponse(content="# 第1章\n" + "正" * 200),
    ])
    agent = BaseAgent(AgentConfig(name="w", role="w", title="写手",
                                  system_prompt="s", skills=["正文写作"]), llm)
    runner = WorkflowRunner({"w": agent}, tmp_path, {}, enable_tools=True)
    step = WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                        output="chapters/{n}_章节.txt", tools=["read_planning"], tool_rounds=1)
    asyncio.run(runner._execute_step(step, {}, 1, {}))
    files = list((tmp_path / "chapters").glob("1_*.txt"))
    assert files and "正" in files[0].read_text(encoding="utf-8")
    assert "<tool_call>" not in files[0].read_text(encoding="utf-8")
    assert len(llm.calls) == 2


def test_chapter_validator_rejects_tool_markup():
    from src.novel_writer.core.project_io import validate_chapter_content

    bad = "<tool_call><function=read_planning><parameter=doc>主线</parameter></function></tool_call>"
    ok, reason = validate_chapter_content(bad)
    assert not ok and "工具调用" in reason
