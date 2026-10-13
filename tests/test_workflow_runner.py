"""工作流执行器回归测试。

覆盖：断点恢复跳过、逐章历史清理、错误容忍（定时步骤/章节失败）、
安全插值、build_workflow 章节数学、批处理校对/审核、死信接线注入、
sediment 自动回收、token 用量累计、Agent 轮换开关。
"""
import asyncio
from pathlib import Path

import pytest

from src.novel_writer.core import project_io
from src.novel_writer.core.agents.base import BaseAgent, AgentConfig
from src.novel_writer.core.llm.base import LLMMessage, LLMResponse
from src.novel_writer.core.memory import MemoryItem, MemoryScratchpad
from src.novel_writer.core.workflow import (
    WorkflowDef,
    WorkflowRunner,
    WorkflowStep,
    build_workflow,
    WorkflowMode,
)
from src.novel_writer.core.workflow.builtin_steps import _pack_batches
from src.novel_writer.core.workflow.context import ContextBuilder, extract_chapter_section
from src.novel_writer.core.workflow.definition import _parse_expected_chapters
from src.novel_writer.core.workflow.sediment import (
    _auto_close_loops,
    extract_chapter_title,
)


# ── 测试替身 ──

class MockAgent(BaseAgent):
    """记录调用并可按关键词模拟故障的 Agent。"""

    def __init__(self, config: AgentConfig, fail_keywords: tuple[str, ...] = (),
                 content: str = "# 测试响应\n" + "正" * 200):
        super().__init__(config, llm=None)
        self.fail_keywords = fail_keywords
        self.content = content
        self.calls: list[str] = []

    async def run(self, user_input, context=""):
        self.calls.append(user_input)
        if any(kw in user_input for kw in self.fail_keywords):
            raise RuntimeError(f"模拟 API 故障: {user_input[:20]}")
        response = LLMResponse(content=self.content, usage={"total_tokens": 10})
        # 与 BaseAgent.run 一致：追加对话历史（历史治理测试依赖此行为）
        self.history.append(LLMMessage(role="user", content=user_input))
        self.history.append(LLMMessage(role="assistant", content=response.content))
        return response


def make_agent(skill: str, name: str = "agent", **kwargs) -> MockAgent:
    return MockAgent(
        AgentConfig(name=name, role="r", title=name, system_prompt="s", skills=[skill]),
        **kwargs,
    )


def make_runner(tmp_path: Path, agents: dict, **kwargs) -> WorkflowRunner:
    return WorkflowRunner(
        agents=agents,
        project_dir=tmp_path,
        project_info={"title": "测试书", "target_chapters": 3},
        **kwargs,
    )


def make_wf(steps: list[WorkflowStep]) -> WorkflowDef:
    return WorkflowDef(name="测试流", project={"title": "测试书", "target_chapters": 3}, steps=steps)


CHAPTER_BODY = "# 测试响应\n" + "正" * 200


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    for d in ("planning", "chapters", "review"):
        (tmp_path / d).mkdir()
    return tmp_path


# ── 断点恢复 ──

class TestResume:
    def test_done_step_skipped(self, project_dir):
        agent = make_agent("故事结构")
        runner = make_runner(project_dir, {"a": agent})
        wf = make_wf([WorkflowStep(id="outline", needs="故事结构", prompt="写大纲",
                                   output="planning/大纲.md")])
        progress = asyncio.run(runner.run(wf, {"outline": "done"}))
        assert agent.calls == []  # 已完成步骤不再烧 LLM 调用
        assert progress["outline"] == "done"

    def test_completed_loop_skipped_on_resume(self, project_dir):
        agent = make_agent("正文写作")
        runner = make_runner(project_dir, {"a": agent})
        wf = make_wf([WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                                   output="chapters/{n}_chapter.txt", repeat=3)])
        # 模拟上次运行跑满 3 章后中断在收尾
        progress = asyncio.run(runner.run(wf, {"chapter": {"current": 3, "total": 3}}))
        assert agent.calls == []
        assert progress["chapter"]["current"] == 3


# ── 历史治理 ──

class TestHistory:
    def test_history_cleared_per_iteration(self, project_dir):
        agent = make_agent("正文写作")
        runner = make_runner(project_dir, {"a": agent})
        wf = make_wf([WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                                   output="chapters/{n}_chapter.txt", repeat=3)])
        asyncio.run(runner.run(wf, {}))
        assert len(agent.calls) == 3
        # 每轮迭代开头清空 → 结束后只剩最后一轮的 user/assistant 对
        assert len(agent.history) == 2

    def test_history_trim_budget(self):
        from src.novel_writer.core.agents.base import HISTORY_TOKEN_BUDGET, _estimate_messages_tokens
        agent = make_agent("正文写作")
        # 每条约 9K tokens，一对 18K ≤ 预算；4 对 72K 超预算 → 只保留最新一对
        big = "字" * (HISTORY_TOKEN_BUDGET // 4)
        for _ in range(4):
            agent.history.append(LLMMessage(role="user", content=big))
            agent.history.append(LLMMessage(role="assistant", content=big))
        messages = agent._build_messages("新输入")
        assert _estimate_messages_tokens(agent.history) > HISTORY_TOKEN_BUDGET
        sent_history = messages[1:-1]
        assert 0 < _estimate_messages_tokens(sent_history) <= HISTORY_TOKEN_BUDGET
        # 最新一轮保留
        assert len(sent_history) == 2
        assert sent_history[-1].content == big


# ── 错误容忍 ──

class TestErrorTolerance:
    def test_periodic_failure_degrades_and_continues(self, project_dir):
        writer = make_agent("正文写作")
        noter = make_agent("笔记", name="noter", fail_keywords=("",))  # 永远失败
        runner = make_runner(project_dir, {"w": writer, "n": noter})
        errors = []
        runner.on_error = lambda s, e: errors.append((s, e))
        wf = make_wf([
            WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                         output="chapters/{n}_chapter.txt", repeat=2),
            WorkflowStep(id="note", needs="笔记", prompt="记录第{n}章",
                         output="review/note_{n}.md", every=1),
        ])
        progress = asyncio.run(runner.run(wf, {}))
        assert progress.get("note_1") == "error"
        assert progress.get("note_2") == "error"
        assert progress["chapter"]["current"] == 2  # 章节循环未被带崩
        assert any(s == "note" for s, _ in errors)

    def test_chapter_failure_recorded_and_continues(self, project_dir):
        writer = make_agent("正文写作", fail_keywords=("第2章",))
        runner = make_runner(project_dir, {"a": writer})
        wf = make_wf([WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章正文",
                                   output="chapters/{n}_chapter.txt", repeat=3)])
        progress = asyncio.run(runner.run(wf, {}))
        assert any("第2章" in e for e in progress.get("_errors", []))
        assert progress["chapter"]["current"] == 3
        numbers = [c["number"] for c in project_io.scan_chapters(project_dir)]
        assert numbers == [1, 3]  # 失败的第 2 章跳过，后续章节继续

    def test_custom_periodic_step_defaults_to_post(self, project_dir):
        writer = make_agent("正文写作")
        noter = make_agent("笔记", name="noter")
        runner = make_runner(project_dir, {"w": writer, "n": noter})
        wf = make_wf([
            WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                         output="chapters/{n}_chapter.txt", repeat=1),
            WorkflowStep(id="custom_note", needs="笔记", prompt="记第{n}章",
                         output="review/cn_{n}.md", every=1),  # 不在 PRE/POST 名单
        ])
        progress = asyncio.run(runner.run(wf, {}))
        assert progress.get("custom_note_1") == "done"  # 不再被静默丢弃


# ── 插值安全 ──

class TestFormatPrompt:
    def test_literal_braces_preserved(self, project_dir):
        runner = make_runner(project_dir, {})
        tpl = '写第{n}章，要求 {"style": "热血"}，未知键{nope}保持原样'
        out = runner._format_prompt(tpl, {"title": "书"}, 2)
        assert out == '写第2章，要求 {"style": "热血"}，未知键{nope}保持原样'


# ── build_workflow 章节数学 ──

class TestChapterMath:
    def test_parse_expected_sums_volumes(self, tmp_path):
        (tmp_path / "planning").mkdir()
        (tmp_path / "planning" / "立意.md").write_text(
            "分卷：速成上路(30章)→虚浮翻车(35章)。全书共50章。", encoding="utf-8")
        assert _parse_expected_chapters(str(tmp_path)) == 65  # 分卷求和优先

    def test_parse_expected_total(self, tmp_path):
        (tmp_path / "planning").mkdir()
        (tmp_path / "planning" / "立意.md").write_text("全书共120章", encoding="utf-8")
        assert _parse_expected_chapters(str(tmp_path)) == 120

    def test_parse_expected_missing(self, tmp_path):
        assert _parse_expected_chapters(str(tmp_path)) is None
        assert _parse_expected_chapters("") is None

    def test_build_workflow_prefers_expected(self, tmp_path):
        (tmp_path / "planning").mkdir()
        # 分卷求和 65 章，与 end_chapter=100 差 35% > 15% 阈值 → 以规划为准
        (tmp_path / "planning" / "立意.md").write_text(
            "分卷：速成上路(30章)→虚浮翻车(35章)", encoding="utf-8")
        wf = build_workflow(WorkflowMode.NEW_BOOK,
                            {"title": "书", "_project_dir": str(tmp_path)}, 1, 100)
        chapter = next(s for s in wf.steps if s.id == "chapter")
        assert chapter.repeat == 65
        assert wf.project["target_chapters"] == 65


# ── 批处理校对/审核 ──

class TestBatchSteps:
    def test_pack_batches_respects_budget(self):
        items = [(i, "字" * 100) for i in range(1, 6)]  # 每项约 150 tokens
        batches = _pack_batches(items, budget=350)
        assert len(batches) == 3
        assert [n for b in batches for n, _ in b] == [1, 2, 3, 4, 5]  # 顺序保持

    def test_proofread_all_chapters(self, project_dir):
        (project_dir / "chapters" / "1_甲.txt").write_text("第一" * 100, encoding="utf-8")
        (project_dir / "chapters" / "2_乙.txt").write_text("第二" * 100, encoding="utf-8")
        proofer = make_agent("错别字检查", content="无错别字")
        runner = make_runner(project_dir, {"p": proofer})
        wf = make_wf([WorkflowStep(id="proofread", needs="错别字检查",
                                   output="review/校对报告.md")])
        asyncio.run(runner.run(wf, {}))
        report = project_dir / "review" / "校对报告.md"
        assert report.exists()
        assert "全书校对" in report.read_text(encoding="utf-8")

    def test_review_batches_then_aggregates(self, project_dir):
        # 每章约 15K tokens：5 章按 60K 预算分 2 批（4+1）+ 1 次聚合 = 3 次调用
        for i in range(1, 6):
            (project_dir / "chapters" / f"{i}_章{i}.txt").write_text(f"第{i}章" + "节" * 9990,
                                                                     encoding="utf-8")
        reviewer = make_agent("剧情审查", content="发现问题X")
        runner = make_runner(project_dir, {"r": reviewer})
        wf = make_wf([WorkflowStep(id="review", needs="剧情审查",
                                   output="review/审核报告.md")])
        asyncio.run(runner.run(wf, {}))
        assert len(reviewer.calls) == 3  # 2 批 + 1 聚合
        # 聚合调用收到的是各批发现而非章节原文
        assert "发现问题X" in reviewer.calls[-1]
        report = (project_dir / "review" / "审核报告.md").read_text(encoding="utf-8")
        assert "发现问题X" in report

    def test_review_existing_report_skipped(self, project_dir):
        (project_dir / "review" / "审核报告.md").write_text("旧报告", encoding="utf-8")
        reviewer = make_agent("剧情审查")
        runner = make_runner(project_dir, {"r": reviewer})
        wf = make_wf([WorkflowStep(id="review", needs="剧情审查",
                                   output="review/审核报告.md")])
        asyncio.run(runner.run(wf, {}))
        assert reviewer.calls == []
        assert (project_dir / "review" / "审核报告.md").read_text(encoding="utf-8") == "旧报告"


# ── 死信接线注入 ──

class TestContextInjection:
    def test_extract_chapter_section(self):
        outline = "# 大纲\n## 第5章 黎明突袭\n主角夜袭敌营。\n细节：火攻。\n## 第6章 溃败\n敌人败退。"
        section = extract_chapter_section(outline, 5)
        assert "黎明突袭" in section and "火攻" in section
        assert "第6章" not in section  # 截断在下一个标题前

    def test_build_injects_summary_outline_inspiration(self, project_dir):
        (project_dir / "planning" / "大纲.md").write_text(
            "# 大纲\n## 第3章 黎明突袭\n主角夜袭敌营，火攻粮仓。", encoding="utf-8")
        (project_dir / "summary").mkdir()
        (project_dir / "summary" / "第1-3章摘要.md").write_text(
            "## 剧情摘要\n前两章主角崛起。", encoding="utf-8")
        (project_dir / "inspiration").mkdir()
        (project_dir / "inspiration" / "3_灵感.md").write_text(
            "转折方向：敌营有内应。", encoding="utf-8")
        builder = ContextBuilder(project_dir, {"title": "测试书"})
        result = builder.build(["planning/大纲.md"], 3, step_id="chapter")
        assert "=== 剧情摘要 ===" in result          # 滚动摘要回流
        assert "=== 本章大纲 ===" in result          # 大纲直注入
        assert "黎明突袭" in result
        assert "=== 灵感 ===" in result              # 灵感回流
        assert "内应" in result

    def test_narrative_tier_injects_summary(self, project_dir):
        (project_dir / "summary").mkdir()
        (project_dir / "summary" / "第1-5章摘要.md").write_text("## 剧情摘要\n关键转折。", encoding="utf-8")
        builder = ContextBuilder(project_dir, {"title": "测试书"})
        result = builder.build(["planning/大纲.md"], 6, step_id="update_outline")
        assert "=== 剧情摘要 ===" in result


# ── sediment ──

class TestSediment:
    def test_extract_chapter_title(self):
        assert extract_chapter_title("# 黎明前夜\n正文", 3) == "黎明前夜"
        assert extract_chapter_title("没有标题的正文", 3) == "第3章"

    def test_auto_close_loops_matches_keyword(self, project_dir):
        mem = MemoryScratchpad(project_dir)
        mem.upsert(MemoryItem(
            category="open_loops",
            subject="第1章伏笔1·玄铁令",
            aspect="悬念",
            value="玄铁令下落成谜",
            source_chapter=1,
        ))
        _auto_close_loops(mem, 2, "他恍然大悟。原来如此，玄铁令下落终于水落石出。")
        assert mem.get_active("open_loops") == []

    def test_auto_close_loops_no_false_positive(self, project_dir):
        mem = MemoryScratchpad(project_dir)
        mem.upsert(MemoryItem(
            category="open_loops",
            subject="第1章伏笔1·玄铁令",
            aspect="悬念",
            value="玄铁令下落成谜",
            source_chapter=1,
        ))
        _auto_close_loops(mem, 2, "原来如此，他终于明白了一切。")
        assert len(mem.get_active("open_loops")) == 1  # 关键词不相关，不误收


# ── token 用量与 Agent 轮换 ──

class TestUsageAndRotation:
    def test_usage_accumulated(self, project_dir):
        agent = make_agent("正文写作")
        runner = make_runner(project_dir, {"a": agent})
        wf = make_wf([WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                                   output="chapters/{n}_chapter.txt", repeat=2)])
        progress = asyncio.run(runner.run(wf, {}))
        assert progress["_usage"]["total_tokens"] == 20
        assert runner.total_usage["total_tokens"] == 20

    def test_find_agent_rotation_opt_in(self, project_dir):
        a1 = make_agent("正文写作", name="a1")
        a2 = make_agent("正文写作", name="a2")
        default_runner = make_runner(project_dir, {"1": a1, "2": a2})
        assert default_runner.find_agent("正文写作") is a1
        assert default_runner.find_agent("正文写作") is a1  # 默认固定第一个

        rotating = make_runner(project_dir, {"1": a1, "2": a2}, rotate_agents=True)
        picks = [rotating.find_agent("正文写作") for _ in range(4)]
        assert picks == [a1, a2, a1, a2]  # 轮换交替（cycle 从第一个开始）
