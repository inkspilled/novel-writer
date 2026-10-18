"""章节细纲：按窗口修订 + 拼接，禁止整书覆盖。

回归背景：update_chapter_outline 曾要求 LLM 重写整本细纲再覆盖文件；
100 章细纲约 1.3 万字，远超 4096 输出上限，结果越修越短。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.novel_writer.core.workflow import builtin_steps as bs
from src.novel_writer.core.workflow.definition import WorkflowStep


def make_outline(nums: range) -> str:
    parts = ["# 全书章节细纲"]
    for i in nums:
        parts.append(
            f"## 第{i}章 标题{i}\n"
            f"- 事件：第{i}章事件\n"
            f"- 转折/冲突：第{i}章冲突\n"
            f"- 章末钩子：第{i}章钩子"
        )
    return "\n\n".join(parts) + "\n"


def revision(nums: range) -> str:
    return "\n\n".join(
        f"## 第{i}章 新标题{i}\n- 事件：新事件{i}\n- 转折/冲突：新冲突{i}\n- 章末钩子：新钩子{i}"
        for i in nums
    )


def test_splice_replaces_only_window():
    existing = make_outline(range(1, 21))
    merged = bs.splice_outline(existing, 6, 15, revision(range(6, 16)))
    pre, sections = bs.parse_outline_sections(merged)

    assert pre == "# 全书章节细纲"
    assert set(sections) == set(range(1, 21))
    assert "新事件6" in sections[6] and "新事件15" in sections[15]
    # 窗口外原样保留
    assert "第5章事件" in sections[5]
    assert "第16章事件" in sections[16]


def test_splice_rejects_incomplete_revision():
    existing = make_outline(range(1, 21))
    with pytest.raises(ValueError):
        bs.splice_outline(existing, 6, 15, revision(range(6, 9)))


def test_splice_rejects_unparseable_existing():
    with pytest.raises(ValueError):
        bs.splice_outline("没有二级标题的细纲", 1, 10, revision(range(1, 11)))


class FakeAgent:
    def __init__(self, content: str):
        self.content = content
        self.calls: list[dict] = []
        self.title = "策划"

    def clear_history(self):
        pass

    async def run(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})

        class R:
            pass

        r = R()
        r.content = self.content
        r.usage = {"total_tokens": 10}
        return r


class FakeRunner:
    def __init__(self, project_dir: Path, agent: FakeAgent):
        self.project_dir = project_dir
        self.project_info = {"target_chapters": 20}
        self._stop = False
        self.agent = agent
        self.on_step_start = None
        self.on_step_end = None

    def find_agent(self, skill):
        return self.agent


def _setup(tmp_path: Path, content: str):
    (tmp_path / "planning").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chapters").mkdir(exist_ok=True)
    (tmp_path / "planning" / "章节细纲.md").write_text(content, encoding="utf-8")
    agent = FakeAgent(revision(range(6, 16)))
    return FakeRunner(tmp_path, agent), agent


def test_update_keeps_outside_window_and_requests_window_only(tmp_path):
    runner, agent = _setup(tmp_path, make_outline(range(1, 21)))
    step = WorkflowStep(id="update_chapter_outline", needs="故事结构", output="planning/章节细纲.md")

    asyncio.run(bs._run_update_chapter_outline(runner, step, 5))

    text = (tmp_path / "planning" / "章节细纲.md").read_text(encoding="utf-8")
    _, sections = bs.parse_outline_sections(text)
    assert "新事件6" in sections[6]
    assert "第16章事件" in sections[16]
    # prompt 明确要求只输出 6-15，且输出上限被放宽
    assert "只输出第 6–15 章" in agent.calls[0]["prompt"]
    assert agent.calls[0]["max_tokens"] == 8192


def test_update_rejects_short_revision_and_keeps_file(tmp_path):
    original = make_outline(range(1, 21))
    runner, agent = _setup(tmp_path, original)
    agent.content = revision(range(6, 9))  # 只返回 3/10 章
    step = WorkflowStep(id="update_chapter_outline", needs="故事结构", output="planning/章节细纲.md")

    asyncio.run(bs._run_update_chapter_outline(runner, step, 5))

    assert (tmp_path / "planning" / "章节细纲.md").read_text(encoding="utf-8") == original


def test_clean_outline_discards_model_reasoning():
    raw = (
        "Now I need to plan chapters 1-2. Let me think...\n\n"
        "## 第1章 开端\n- 事件：A\n\n"
        "Some intermediate note\n\n"
        "## 第2章 承接\n- 事件：B\n"
    )
    cleaned = bs.clean_outline_text(raw)
    assert cleaned.startswith("## 第1章")
    assert "Now I need" not in cleaned and "intermediate note" not in cleaned
    _, secs = bs.parse_outline_sections(cleaned)
    assert set(secs) == {1, 2}


def test_generate_outline_writes_only_clean_entries(tmp_path):
    (tmp_path / "planning").mkdir()
    agent = FakeAgent(
        "Now let me plan...\n\n"
        "## 第1章 开端\n- 事件：A\n- 转折：B\n- 章末钩子：C\n\n"
        "## 第2章 承接\n- 事件：D\n- 转折：E\n- 章末钩子：F\n"
    )
    runner = FakeRunner(tmp_path, agent)
    runner.project_info = {"target_chapters": 2}
    step = WorkflowStep(id="chapter_outline", needs="故事结构", output="planning/章节细纲.md")

    asyncio.run(bs._run_chapter_outline(runner, step, 1))

    text = (tmp_path / "planning" / "章节细纲.md").read_text(encoding="utf-8")
    assert text.startswith("## 第1章")
    assert "Now let me plan" not in text
    assert not (tmp_path / "planning" / "章节细纲.partial.md").exists()


def test_generate_outline_respects_initial_window(tmp_path):
    parts = ["# 全书章节细纲"]
    for i in range(1, 11):
        parts.append(f"## 第{i}章 标题{i}\n- 事件：A{i}\n- 转折：B{i}\n- 章末钩子：C{i}")
    (tmp_path / "planning").mkdir()
    agent = FakeAgent("\n\n".join(parts))
    runner = FakeRunner(tmp_path, agent)
    runner.project_info = {"target_chapters": 500}
    step = WorkflowStep(id="chapter_outline", needs="故事结构",
                       output="planning/章节细纲.md", max_chapters=10)

    asyncio.run(bs._run_chapter_outline(runner, step, 1))

    _, sections = bs.parse_outline_sections(
        (tmp_path / "planning" / "章节细纲.md").read_text(encoding="utf-8"))
    assert len(sections) == 10
    assert "第 1–10 章" in agent.calls[0]["prompt"]
