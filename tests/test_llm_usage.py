"""缓存字段观测回归：usage 归一化、流式 usage 透传、UI 命中率展示。

对应 docs/上下文缓存与1M预算.md 的 P0：命中率在数据源头必须可得，
正文流式调用的 token 必须能进统计。
"""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.novel_writer.core.agents.base import AgentConfig, BaseAgent
from src.novel_writer.core.llm.base import BaseLLM, LLMMessage, LLMResponse
from src.novel_writer.core.llm.client import LLMClient
from src.novel_writer.core.llm.usage import (
    normalize_claude_usage,
    normalize_openai_usage,
    parse_stream_usage,
)
from src.novel_writer.core.workflow.definition import WorkflowDef, WorkflowStep
from src.novel_writer.core.workflow.runner import WorkflowRunner
from src.novel_writer.core.workflow.usage_ledger import UsageLedger


# ── 归一化 ──

def test_normalize_openai_plain_has_no_cache_meta():
    usage = normalize_openai_usage(SimpleNamespace(prompt_tokens=100, completion_tokens=20))
    assert usage == {
        "prompt_tokens": 100, "completion_tokens": 20,
        "cached_tokens": 0, "cache_write_tokens": 0, "cache_meta": 0,
    }


def test_normalize_openai_details_caches():
    usage = normalize_openai_usage(SimpleNamespace(
        prompt_tokens=100, completion_tokens=20,
        prompt_tokens_details=SimpleNamespace(cached_tokens=64),
    ))
    assert usage["cached_tokens"] == 64 and usage["cache_meta"] == 1
    # OpenAI 的 prompt_tokens 已含命中部分，不再叠加


def test_normalize_openai_deepseek_named_fields():
    usage = normalize_openai_usage(SimpleNamespace(
        prompt_tokens=100, completion_tokens=20, prompt_cache_hit_tokens=88,
    ))
    assert usage["cached_tokens"] == 88 and usage["cache_meta"] == 1


def test_normalize_claude_merges_cache_into_total_input():
    usage = normalize_claude_usage(SimpleNamespace(
        input_tokens=100, output_tokens=20,
        cache_read_input_tokens=300, cache_creation_input_tokens=50,
    ))
    assert usage["prompt_tokens"] == 450  # input + read + creation
    assert usage["completion_tokens"] == 20
    assert usage["cached_tokens"] == 300
    assert usage["cache_write_tokens"] == 50
    assert usage["cache_meta"] == 1


def test_normalize_none_returns_empty():
    assert normalize_openai_usage(None) == {}
    assert normalize_claude_usage(None) == {}


def test_parse_stream_usage_bad_payload():
    assert parse_stream_usage("not json") is None
    assert parse_stream_usage('{"prompt_tokens": 1}') == {"prompt_tokens": 1}


# ── 驱动透传 ──

def _stub_client_llm() -> LLMClient:
    return LLMClient(model="stub", api_key="k", base_url="http://127.0.0.1:1/v1")


def test_chat_passes_cache_fields_through():
    llm = _stub_client_llm()

    class StubCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="ok"), finish_reason="stop")],
                model="stub",
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=5,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=8)),
            )

    class StubClient:
        chat = SimpleNamespace(completions=StubCompletions())

    async def run():
        llm._bound_loop = asyncio.get_running_loop()
        llm.client = StubClient()
        return await llm.chat([LLMMessage(role="user", content="hi")], max_retries=0)

    resp = asyncio.run(run())
    assert resp.usage["cached_tokens"] == 8
    assert resp.usage["cache_meta"] == 1
    assert resp.usage["prompt_tokens"] == 10


class _FakeStream:
    """choices 为空的 usage chunk 模拟 OpenAI stream_options 末包。"""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def close(self):
        return None


def _usage_chunk():
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=12, completion_tokens=3,
            prompt_tokens_details=SimpleNamespace(cached_tokens=8),
        ),
    )


def _text_chunk(text="正文"):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


def test_stream_requests_and_yields_usage():
    llm = _stub_client_llm()

    class StubCompletions:
        def __init__(self):
            self.kwargs_list = []

        async def create(self, **kwargs):
            self.kwargs_list.append(kwargs)
            return _FakeStream([_text_chunk(), _usage_chunk()])

    completions = StubCompletions()

    class StubClient:
        chat = SimpleNamespace(completions=completions)

    async def run():
        llm._bound_loop = asyncio.get_running_loop()
        llm.client = StubClient()
        kinds = []
        async for kind, chunk in llm.stream_chat([LLMMessage(role="user", content="hi")]):
            kinds.append((kind, chunk))
        return kinds

    kinds = asyncio.run(run())
    assert [k for k, _ in kinds] == ["text", "usage"]
    payload = json.loads(kinds[-1][1])
    assert payload["cached_tokens"] == 8 and payload["cache_meta"] == 1
    # 请求里带了 include_usage，供应商才会回 usage 包
    assert completions.kwargs_list[0]["stream_options"] == {"include_usage": True}


def test_stream_downgrades_when_gateway_rejects_stream_options():
    llm = _stub_client_llm()

    class BadRequestError(Exception):
        pass

    class StubCompletions:
        def __init__(self):
            self.kwargs_list = []

        async def create(self, **kwargs):
            self.kwargs_list.append(kwargs)
            if kwargs.get("stream_options"):
                raise BadRequestError("Unrecognized request argument supplied: stream_options")
            return _FakeStream([_text_chunk(), _usage_chunk()])

    completions = StubCompletions()

    class StubClient:
        chat = SimpleNamespace(completions=completions)

    async def run():
        llm._bound_loop = asyncio.get_running_loop()
        llm.client = StubClient()
        kinds = []
        async for kind, chunk in llm.stream_chat([LLMMessage(role="user", content="hi")]):
            kinds.append(kind)
        return kinds

    kinds = asyncio.run(run())
    assert kinds == ["text", "usage"]  # 丢 usage 参数不丢正文
    assert "stream_options" not in completions.kwargs_list[-1]


# ── BaseAgent.stream_run：usage 落袋不外发 ──

class _UsageStreamLLM(BaseLLM):
    async def chat(self, messages, temperature=0.7, max_tokens=4096, thinking="auto",
                   tools=None, model=None):
        raise AssertionError("不应走非流式")

    async def stream_chat(self, messages, temperature=0.7, max_tokens=4096,
                          thinking="auto", tools=None, model=None):
        yield "text", "林牧醒来。" * 300  # 章节校验有最小长度要求
        yield "usage", json.dumps({
            "prompt_tokens": 100, "completion_tokens": 10,
            "cached_tokens": 80, "cache_write_tokens": 0, "cache_meta": 1,
        })


def test_stream_run_stashes_usage_without_emitting():
    agent = BaseAgent(
        AgentConfig(name="w", role="w", title="写手", system_prompt="sys"),
        _UsageStreamLLM(model="fake"),
    )
    kinds = []

    async def run():
        async for kind, chunk in agent.stream_run("写", keep_history=False):
            kinds.append(kind)

    asyncio.run(run())
    assert kinds == ["text"]  # usage 不进协议，聊天窗不会收到 JSON
    assert agent.last_stream_usage["cached_tokens"] == 80


# ── 工作流：流式分支记账 + est_tokens ──

def test_runner_streaming_step_records_usage(tmp_path):
    agent = BaseAgent(
        AgentConfig(name="w", role="w", title="写手", system_prompt="系统提示词",
                    skills=["正文写作"]),
        _UsageStreamLLM(model="fake"),
    )
    runner = WorkflowRunner({"a": agent}, tmp_path, {}, enable_tools=False)
    chunks: list[tuple] = []
    runner.on_chunk = lambda step, n, kind, c: chunks.append((kind, c))
    wf = WorkflowDef(name="t", project={}, steps=[
        WorkflowStep(id="chapter", needs="正文写作", prompt="写",
                     output="chapters/{n}_章节.txt", repeat=1),
    ])
    asyncio.run(runner.run(wf, {}))

    # 正文此前是统计盲区：流式分支必须把 usage 记进 total_usage
    assert runner.total_usage["prompt_tokens"] == 100
    assert runner.total_usage["completion_tokens"] == 10
    assert runner.total_usage["cached_tokens"] == 80
    assert chunks and chunks[0][0] == "text"

    # H2 校准数据：step_end 同时带估算与真实 token
    journal_files = sorted((tmp_path / "runs").glob("*.jsonl"))
    records = [json.loads(line)
               for line in journal_files[0].read_text(encoding="utf-8").splitlines()]
    step_end = next(r for r in records if r["event"] == "step_end")
    assert step_end["est_tokens"] > 0
    assert step_end["usage"]["prompt_tokens"] == 100


# ── UI：命中率展示 + 总量不重复计数 ──

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.novel_writer.ui.workflow.workflow_bar import WorkflowMiniBar  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _bar_with(usage: dict) -> WorkflowMiniBar:
    bar = WorkflowMiniBar()
    bar.set_usage(usage)
    return bar


def test_usage_label_shows_hit_rate(qapp):
    bar = _bar_with({"prompt_tokens": 1000, "completion_tokens": 100,
                     "cached_tokens": 620, "cache_meta": 1})
    # 1.1k tok：cached_tokens 绝不能被求和进总量（否则显示 1.72k）
    assert bar._usage_label.text() == "1.1k tok · 缓存 62%"
    assert "缓存命中 620" in bar._usage_label.toolTip()


def test_usage_label_shows_dash_without_cache_meta(qapp):
    bar = _bar_with({"prompt_tokens": 1000, "completion_tokens": 100})
    assert bar._usage_label.text() == "1.1k tok · 缓存 —"
    assert "未返回缓存字段" in bar._usage_label.toolTip()


def test_usage_label_shows_real_zero(qapp):
    bar = _bar_with({"prompt_tokens": 1000, "completion_tokens": 100,
                     "cached_tokens": 0, "cache_meta": 1})
    assert bar._usage_label.text() == "1.1k tok · 缓存 0%"


def test_usage_label_hidden_when_empty(qapp):
    bar = _bar_with({})
    assert not bar._usage_label.isVisibleTo(bar)
    assert bar._usage_label.text() == ""


# ── UsageLedger：指纹比对 + JSONL 落盘 ──

def test_ledger_compare_prefix_baseline_and_changes(tmp_path):
    ledger = UsageLedger(tmp_path, run="t")
    base = {"agent": "w", "model": "m", "fp_system": "aaa", "fp_tools": ""}
    assert ledger.compare_prefix(base) == (0, 0)      # 首次出现是基线，不算改写
    assert ledger.compare_prefix(base) == (0, 0)      # 指纹没变
    assert ledger.compare_prefix({**base, "fp_system": "bbb"}) == (1, 0)
    assert ledger.compare_prefix({**base, "fp_system": "bbb",
                                  "fp_tools": "ttt"}) == (0, 1)
    # 不同 agent 是独立缓存域
    other = {"agent": "p", "model": "m", "fp_system": "zzz", "fp_tools": ""}
    assert ledger.compare_prefix(other) == (0, 0)


def test_ledger_record_writes_jsonl(tmp_path):
    ledger = UsageLedger(tmp_path, run="新书全流程")
    ledger.record({"agent": "w", "prompt_tokens": 10, "cached_tokens": 8})
    line = (tmp_path / "runs" / "usage.jsonl").read_text(encoding="utf-8").strip()
    data = json.loads(line)
    assert data["event"] == "llm_call" and data["run"] == "新书全流程"
    assert data["prompt_tokens"] == 10 and "ts" in data


# ── runner ↔ ledger 集成：调用级落盘 + 前缀稳定性统计 ──

def test_runner_emits_per_call_ledger_with_meta(tmp_path):
    agent = BaseAgent(
        AgentConfig(name="w", role="w", title="写手", system_prompt="系统提示词",
                    skills=["正文写作"]),
        _UsageStreamLLM(model="fake"),
    )
    runner = WorkflowRunner({"a": agent}, tmp_path, {}, enable_tools=False)
    runner.on_chunk = lambda *a: None
    wf = WorkflowDef(name="t", project={}, steps=[
        WorkflowStep(id="chapter", needs="正文写作", prompt="写",
                     output="chapters/{n}_章节.txt", repeat=2),
    ])
    asyncio.run(runner.run(wf, {}))

    # 调用级流水：每次调用一行，带步骤/章号/指纹/估算。
    # （沉淀走 _UsageStreamLLM.chat 会抛异常 → 静默回退正则，不产生流水行）
    records = [json.loads(line)
               for line in (tmp_path / "runs" / "usage.jsonl")
               .read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    first, second = records
    assert first["agent"] == "w" and first["step"] == "chapter" and first["n"] == 1
    assert first["fp_system"] and first["est_tokens"] > 0
    assert first["prompt_tokens"] == 100 and first["cached_tokens"] == 80
    assert second["n"] == 2
    # 最小项目里 context 逐章不变 → 指纹稳定 → 不算改写（指标按设计工作）
    assert second["fp_system"] == first["fp_system"]

    # total_usage 里的稳定性统计（经 progress["_usage"] 到 UI tooltip）
    assert runner.total_usage["call_count"] == 2
    assert runner.total_usage["prefix_changed"] == 0


def test_runner_plain_run_records_ledger(tmp_path):
    class PlainLLM(BaseLLM):
        async def chat(self, messages, temperature=0.7, max_tokens=4096,
                       thinking="auto", tools=None, model=None):
            return LLMResponse(content="林牧醒来。" * 100, usage={
                "prompt_tokens": 50, "completion_tokens": 5,
                "cached_tokens": 0, "cache_write_tokens": 0, "cache_meta": 0,
            })

        async def stream_chat(self, *a, **kw):
            yield "text", ""

    agent = BaseAgent(
        AgentConfig(name="w", role="w", title="写手", system_prompt="系统提示词",
                    skills=["正文写作"]),
        PlainLLM(model="fake"),
    )
    runner = WorkflowRunner({"a": agent}, tmp_path, {}, enable_tools=False)
    wf = WorkflowDef(name="t", project={}, steps=[
        WorkflowStep(id="chapter", needs="正文写作", prompt="写",
                     output="chapters/{n}_章节.txt", repeat=1),
    ])
    asyncio.run(runner.run(wf, {}))

    records = [json.loads(line)
               for line in (tmp_path / "runs" / "usage.jsonl")
               .read_text(encoding="utf-8").splitlines()]
    # 章节正文 1 次 + 写后沉淀（章节循环内置）1 次
    assert len(records) == 2
    chapter_row, sediment_row = records
    assert chapter_row["step"] == "chapter" and chapter_row["n"] == 1
    assert sediment_row["step"] == "sediment" and sediment_row["n"] == 1
    # 沉淀的 system 内容与正文不同 → 劈点标记为 1（真改写）
    assert runner.total_usage["prefix_changed"] == 1
    assert runner.total_usage["call_count"] == 2
    assert "duration_s" in chapter_row
    # 章号是元数据，不得混进用量累计
    assert "n" not in runner.total_usage


def test_tooltip_shows_prefix_stability(qapp):
    bar = _bar_with({"prompt_tokens": 1000, "completion_tokens": 100,
                     "cached_tokens": 0, "cache_meta": 0,
                     "call_count": 40, "prefix_changed": 37})
    assert "前缀稳定 3/40" in bar._usage_label.toolTip()
