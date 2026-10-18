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
from src.novel_writer.core.memory import MemoryItem, MemoryScratchpad, loop_urgency
from src.novel_writer.core.world_state import WorldState
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

    async def run(self, user_input, context="", thinking=None, keep_history=True):
        self.calls.append(user_input)
        if any(kw in user_input for kw in self.fail_keywords):
            raise RuntimeError(f"模拟 API 故障: {user_input[:20]}")
        response = LLMResponse(content=self.content, usage={"total_tokens": 10})
        self._record_usage(response.usage)
        # 与 BaseAgent.run 一致：仅 keep_history 时追加对话历史
        if keep_history:
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
        # 产出存在时，done 才算完成；文件缺失会自动重跑（见 TestStepCompletedOutputMissing）
        out = project_dir / "planning" / "大纲.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# 大纲", encoding="utf-8")
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
        # 独立的沉淀 Agent：避免 _fallback_agent 把写章 Agent 拉去跑 LLM 沉淀
        sediment_agent = make_agent("剧情审查", name="sedimenter")
        runner = make_runner(project_dir, {"a": agent, "s": sediment_agent})
        wf = make_wf([WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                                   output="chapters/{n}_chapter.txt", repeat=3)])
        asyncio.run(runner.run(wf, {}))
        assert len(agent.calls) == 3  # 写章 Agent 只被章节步骤调用
        assert len(sediment_agent.calls) == 3  # 每章一次 LLM 沉淀
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

    def test_sediment_runs_after_polish_on_final_text(self, project_dir):
        """沉淀必须等全部章后步骤（含润色）完成后，从最终正文提取。"""
        writer = make_agent("正文写作")
        polisher = make_agent("润色", content="# 测试响应\n" + "润" * 200)
        runner = make_runner(project_dir, {"w": writer, "p": polisher})
        wf = make_wf([
            WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                         output="chapters/{n}_chapter.txt", repeat=1),
            WorkflowStep(id="polish", needs="润色", prompt="润色第{n}章",
                         input_files=["chapters/{n}_chapter.txt"], output="chapters/{n}_chapter.txt",
                         every=1, optional=True),
        ])
        progress = asyncio.run(runner.run(wf, {}))
        assert progress.get("sediment_1") == "done"
        mem = MemoryScratchpad(project_dir)
        facts = mem.get_active("story_facts")
        assert facts and "润" in facts[0]["value"]  # 记忆来自润色后文本，不是初稿

    def test_sediment_resume_key_skips_rerun(self, project_dir):
        (project_dir / "chapters" / "1_甲.txt").write_text("# 甲\n" + "正" * 200, encoding="utf-8")
        agent = make_agent("正文写作")
        runner = make_runner(project_dir, {"a": agent})
        wf = make_wf([WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                                   output="chapters/{n}_chapter.txt", repeat=1)])
        progress = asyncio.run(runner.run(wf, {"sediment_1": "done"}))
        assert agent.calls == []                          # 已有内容不重写
        assert not (project_dir / "memory.json").exists()  # 已沉淀过，不重复提取

    def test_sediment_failure_blocks_progress(self, project_dir, monkeypatch):
        import src.novel_writer.core.workflow.runner as runner_mod

        def boom(*args, **kwargs):
            raise RuntimeError("沉淀炸了")

        monkeypatch.setattr(runner_mod, "sediment_chapter", boom)
        errors = []
        writer = make_agent("正文写作")
        runner = make_runner(project_dir, {"a": writer})
        runner.on_error = lambda s, e: errors.append((s, e))
        wf = make_wf([WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                                   output="chapters/{n}_chapter.txt", repeat=2)])
        progress = asyncio.run(runner.run(wf, {}))
        # 章节循环继续，但沉淀失败的那一章不能被标成完成
        assert progress.get("chapter") is None
        assert "sediment_1" not in progress                # 失败不标记，恢复时重试
        assert any(s == "sediment" for s, _ in errors)

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


# ── 伏笔动态紧急度 ──

class TestLoopUrgency:
    def test_fresh_loop_low_urgency(self):
        item = {"source_chapter": 10, "payload": {"urgency": 0.6}}
        assert loop_urgency(item, current_chapter=11, total_chapters=100) < 0.5

    def test_old_loop_rises_with_age(self):
        item = {"source_chapter": 1, "payload": {"urgency": 0.6}}
        u15 = loop_urgency(item, current_chapter=16, total_chapters=100)
        u40 = loop_urgency(item, current_chapter=41, total_chapters=100)
        assert u15 >= 0.75      # 15 章未收到达超期线
        assert u40 > u15        # 年龄饱和前持续上涨

    def test_endgame_boost(self):
        item = {"source_chapter": 1, "payload": {"urgency": 0.6}}
        mid = loop_urgency(item, current_chapter=50, total_chapters=100)
        late = loop_urgency(item, current_chapter=95, total_chapters=100)
        assert late > mid
        assert late >= 0.9      # 终章阶段逼近上限

    def test_static_fallback_without_chapter(self):
        assert loop_urgency({"source_chapter": 1, "payload": {"urgency": 0.6}}) == 0.6
        # 脏数据兜底：payload 非法不抛异常
        assert loop_urgency({"payload": {"urgency": "x"}}) == 0.6

    def test_get_open_loops_orders_by_dynamic_urgency(self, project_dir):
        mem = MemoryScratchpad(project_dir)
        mem.upsert(MemoryItem(category="open_loops", subject="新坑", aspect="悬念",
                              value="新埋的伏笔", source_chapter=18))
        mem.upsert(MemoryItem(category="open_loops", subject="旧坑", aspect="悬念",
                              value="十八章前的伏笔", source_chapter=1))
        loops = mem.get_open_loops(current_chapter=20, total_chapters=100)
        assert [i["subject"] for i in loops] == ["旧坑", "新坑"]


# ── 注意力尾部：超期标记 + 已回收清单 ──

class TestAttentionTail:
    def test_tail_marks_overdue_loop_and_closed_list(self, project_dir):
        mem = MemoryScratchpad(project_dir)
        mem.upsert(MemoryItem(category="open_loops", subject="第1章伏笔1·玄铁令",
                              aspect="悬念", value="玄铁令下落成谜", source_chapter=1))
        mem.upsert(MemoryItem(category="story_facts", subject="第15章伏笔1·旧坑",
                              aspect="伏笔回收", value="已回收", source_chapter=15))
        builder = ContextBuilder(project_dir, {"title": "书", "target_chapters": 100})
        gs = WorldState(project_dir)
        gs.load()
        tail = builder._build_attention_tail(mem, gs, 20)
        text = "\n".join(tail)
        assert "已超期" in text                       # 19 章未收 → 超期标记
        assert "已经过19章" in text
        assert "近期已回收伏笔" in text               # story_facts 回收记录接线
        assert "第15章已回收" in text

    def test_tail_skips_old_closed_loops(self, project_dir):
        mem = MemoryScratchpad(project_dir)
        mem.upsert(MemoryItem(category="story_facts", subject="第2章伏笔1·旧账",
                              aspect="伏笔回收", value="已回收", source_chapter=2))
        builder = ContextBuilder(project_dir, {"title": "书", "target_chapters": 100})
        gs = WorldState(project_dir)
        gs.load()
        tail = builder._build_attention_tail(mem, gs, 20)
        text = "\n".join(tail)
        assert "近期已回收伏笔" not in text           # 8 章前的回收不再占用尾部


# ── 内置步骤提示词：全文注入 ──

class TestPromptFullText:
    def test_world_state_prompt_gets_full_chapter(self):
        from src.novel_writer.core.workflow.prompts import build_world_state_prompt
        content = "正" * 5000
        prompt = build_world_state_prompt(3, "当前状态", content)
        assert content in prompt  # 不再截断到 2000 字，后半章事件不漏采

    def test_chapter_summary_prompt_gets_full_chapter(self):
        from src.novel_writer.core.workflow.prompts import build_chapter_summary_prompt
        content = "节" * 5000
        prompt = build_chapter_summary_prompt(3, "标题", content)
        assert content in prompt  # 不再截断到 3000 字


# ── 备份文件隔离 ──

class TestBackupIsolation:
    def test_find_chapter_file_ignores_bak(self, project_dir):
        (project_dir / "chapters" / "1_甲.bak.txt").write_text("备份内容", encoding="utf-8")
        (project_dir / "chapters" / "1_甲.txt").write_text("正" * 200, encoding="utf-8")
        assert project_io.find_chapter_file(project_dir, 1) == "1_甲.txt"
        chapters = project_io.scan_chapters(project_dir)
        assert [c["title"] for c in chapters] == ["甲"]  # 目录/导出/校对也不收备份

    def test_sediment_reads_final_text_not_backup(self, project_dir):
        """润色后 .bak 与正文并存，沉淀必须读到润色后的正文。"""
        writer = make_agent("正文写作")
        polisher = make_agent("润色", content="# 测试响应\n" + "润" * 200)
        runner = make_runner(project_dir, {"w": writer, "p": polisher})
        wf = make_wf([
            WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                         output="chapters/{n}_chapter.txt", repeat=1),
            WorkflowStep(id="polish", needs="润色", prompt="润色第{n}章",
                         input_files=["chapters/{n}_chapter.txt"], output="chapters/{n}_chapter.txt",
                         every=1, optional=True),
        ])
        asyncio.run(runner.run(wf, {}))
        mem = MemoryScratchpad(project_dir)
        facts = mem.get_active("story_facts")
        assert facts and "润" in facts[0]["value"] and "正" * 10 not in facts[0]["value"]


# ── LLM 沉淀（主通道）与正则兜底 ──

import json as _json

_SEDIMENT_JSON = _json.dumps({
    "character_state": [{"subject": "凌尘", "aspect": "实力", "value": "凌尘突破至炼气九层"}],
    "open_loops": [{"value": "藏经阁暗格里的神秘残页来历不明"}],
    "resolved": ["第1章伏笔1·玄铁令"],
    "reader_promises": [{"subject": "复仇", "value": "凌尘立誓为师门报仇"}],
    "relationships": [{"subject": "凌尘↔苏婉", "value": "结成同盟"}],
    "world_rules": [{"subject": "灵脉规则", "value": "灵脉夜间会自行枯竭"}],
}, ensure_ascii=False)


def _seed_loop(project_dir, subject="第1章伏笔1·玄铁令", value="玄铁令下落成谜"):
    mem = MemoryScratchpad(project_dir)
    mem.upsert(MemoryItem(category="open_loops", subject=subject,
                          aspect="悬念", value=value, source_chapter=1))
    mem.save()


class TestLLMSediment:
    def test_llm_path_writes_dead_buckets_and_closes_loops(self, project_dir):
        _seed_loop(project_dir)
        writer = make_agent("正文写作")
        reviewer = make_agent("剧情审查", content=_SEDIMENT_JSON)
        runner = make_runner(project_dir, {"w": writer, "r": reviewer})
        wf = make_wf([WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                                   output="chapters/{n}_chapter.txt", repeat=1)])
        progress = asyncio.run(runner.run(wf, {}))
        assert progress.get("sediment_1") == "done"
        # 沉淀 prompt 带上了未解伏笔列表（供 LLM 精确回收）
        assert "玄铁令" in reviewer.calls[0]

        mem = MemoryScratchpad(project_dir)
        assert [i["subject"] for i in mem.get_active("reader_promises")] == ["复仇"]
        assert mem.get_active("relationships")[0]["value"] == "结成同盟"
        assert mem.get_active("world_rules")[0]["subject"] == "灵脉规则"
        assert mem.get_active("character_state")[0]["subject"] == "凌尘"
        # resolved 按 subject 精确关闭 + story_facts 留回收记录
        assert all("玄铁令" not in i["subject"] for i in mem.get_active("open_loops"))
        assert any("玄铁令" in i["subject"] and i["aspect"] == "伏笔回收"
                   for i in mem.get_active("story_facts"))
        # 新伏笔入账
        assert any("残页" in i["value"] for i in mem.get_active("open_loops"))

    def test_llm_path_skips_regex_noise(self, project_dir):
        """LLM 通道不跑正则：假设句「如果他敢动我」不得混进伏笔/状态。"""
        (project_dir / "chapters" / "1_甲.txt").write_text(
            "# 甲\n凌尘冷笑：如果他敢动我，我就杀了他。", encoding="utf-8")
        writer = make_agent("正文写作")
        reviewer = make_agent("剧情审查", content=_SEDIMENT_JSON)
        runner = make_runner(project_dir, {"w": writer, "r": reviewer})
        wf = make_wf([WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                                   output="chapters/{n}_chapter.txt", repeat=1)])
        asyncio.run(runner.run(wf, {}))
        mem = MemoryScratchpad(project_dir)
        assert not any("如果" in i["value"] for i in mem.get_active("open_loops"))
        assert not any("如果" in i["subject"] for i in mem.get_active("character_state"))

    def test_non_json_response_falls_back_to_regex(self, project_dir):
        (project_dir / "chapters" / "1_甲.txt").write_text(
            "# 甲\n凌尘突破至炼气九层。他竟然发现了玄铁令的下落。", encoding="utf-8")
        writer = make_agent("正文写作")
        reviewer = make_agent("剧情审查", content="我不是 JSON")  # 返回垃圾 → 兜底
        runner = make_runner(project_dir, {"w": writer, "r": reviewer})
        wf = make_wf([WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                                   output="chapters/{n}_chapter.txt", repeat=1)])
        progress = asyncio.run(runner.run(wf, {}))
        assert progress.get("sediment_1") == "done"  # 兜底也算沉淀成功
        mem = MemoryScratchpad(project_dir)
        assert any(i["subject"] == "凌尘" for i in mem.get_active("character_state"))
        assert mem.get_active("open_loops")  # 正则兜底提取到伏笔

    def test_sediment_prompt_contains_loops_and_full_text(self, project_dir):
        from src.novel_writer.core.workflow.prompts import build_memory_sediment_prompt
        loops = [{"subject": "第1章伏笔1·玄铁令", "value": "玄铁令下落成谜"}]
        prompt = build_memory_sediment_prompt(3, loops, "正" * 5000)
        assert "第1章伏笔1·玄铁令" in prompt   # 伏笔列表供精确回收
        assert "正" * 5000 in prompt           # 全文注入


# ── 死桶读取路径 ──

class TestDeadBucketWiring:
    def test_context_injects_relationships_and_rules(self, project_dir):
        mem = MemoryScratchpad(project_dir)
        mem.upsert(MemoryItem(category="relationships", subject="凌尘↔苏婉",
                              aspect="关系", value="结成同盟", source_chapter=2))
        mem.upsert(MemoryItem(category="world_rules", subject="灵脉规则",
                              aspect="规则", value="灵脉夜间自行枯竭", source_chapter=2))
        mem.save()
        builder = ContextBuilder(project_dir, {"title": "书"})
        result = builder.build([], 3, step_id="chapter")
        assert "=== 关系与世界规则（近期沉淀） ===" in result
        assert "凌尘↔苏婉" in result and "灵脉规则" in result

    def test_rel_rules_block_empty_buckets_silent(self, project_dir):
        builder = ContextBuilder(project_dir, {"title": "书"})
        mem = MemoryScratchpad(project_dir)
        assert builder._build_rel_rules_block(mem) == ""


# ── 章节四维追踪（chapter_tracker 回归） ──

class TestChapterTracker:
    def test_give_pattern_records_recipient(self, project_dir):
        """「X 给了 Y 物品」入账给接受者（原全角括号正则编译即失败，长期未工作）。"""
        from src.novel_writer.core.chapter_tracker import track_resources
        mem = MemoryScratchpad(project_dir)
        track_resources(mem, 5, "凌尘把一枚养气丹给了苏婉，苏婉服下后气色好转。")
        items = mem.get_active("resources")
        assert any(i["subject"] == "苏婉" and "养气丹" in i["value"] for i in items)

    def test_track_all_dimensions_isolated(self, project_dir, monkeypatch):
        """单维正则异常不得拖垮其他维度（原 resources 崩溃会带走全部四维）。"""
        import src.novel_writer.core.chapter_tracker as ct
        monkeypatch.setattr(ct, "track_resources",
                            lambda *a: (_ for _ in ()).throw(RuntimeError("炸了")))
        mem = MemoryScratchpad(project_dir)
        ct.track_all(mem, 5, "他心中涌起愤怒，握紧了拳。")
        assert mem.get_active("emotional_arcs")  # 其余维度照常工作


# ── 分级压缩（缩量优先于丢弃） ──

class TestCompressGraded:
    def _rag_block(self) -> str:
        entries = [f"[第{i}章 相关度:{i}.0]\n{'容' * 300}" for i in (2, 4, 6)]
        return "\n\n".join(["=== RAG 检索相关段落 ==="] + entries)

    def test_stage1_shrinks_keeps_skeleton(self):
        from src.novel_writer.core.workflow.context import compress_context, estimate_tokens
        parts = [
            "项目: 测试书",
            self._rag_block(),
            "=== 第3章 ===\n" + "旧" * 4000,   # 旧章全文 → 截断
            "=== 第8章 ===\n" + "正" * 4000,   # 最近章 → 保持完整
        ]
        # 缩量后：meta + RAG(2×120字) + 旧章600字 + 第8章全文
        result = compress_context(parts, max_tokens=9500)
        text = "\n\n".join(result)
        assert "正" * 100 in text                    # 最近章全文保留
        assert "超限截断" in text                     # 旧章被截断而非丢弃
        assert "=== 第3章 ===" in text
        rag_part = next(p for p in result if p.startswith("=== RAG"))
        assert "[第6章" not in rag_part              # RAG 只留前 2 条
        assert estimate_tokens(text) <= 9500

    def test_stage2_drops_when_shrink_not_enough(self):
        from src.novel_writer.core.workflow.context import compress_context
        parts = [
            "项目: 测试书",
            self._rag_block(),
            "=== 第8章 ===\n" + "正" * 4000,
        ]
        result = compress_context(parts, max_tokens=300)
        text = "\n\n".join(result)
        assert "=== RAG" not in text                 # 一级不够 → 二级按优先级丢弃
        assert "项目: 测试书" in text                # other 类永不裁剪

    def test_within_budget_untouched(self):
        from src.novel_writer.core.workflow.context import compress_context
        parts = ["项目: 测试书", "=== 第1章 ===\n正文"]
        assert compress_context(parts, max_tokens=10_000) == parts


# ── token 用量与 Agent 轮换 ──

class TestUsageAndRotation:
    def test_usage_accumulated(self, project_dir):
        agent = make_agent("正文写作")
        runner = make_runner(project_dir, {"a": agent})
        wf = make_wf([WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                                   output="chapters/{n}_chapter.txt", repeat=2)])
        progress = asyncio.run(runner.run(wf, {}))
        # 每章 1 次正文 + 1 次 LLM 沉淀，共 4 次调用；usage_sink 覆盖内置步骤
        assert progress["_usage"]["total_tokens"] == 40
        assert runner.total_usage["total_tokens"] == 40

    def test_find_agent_rotation_opt_in(self, project_dir):
        a1 = make_agent("正文写作", name="a1")
        a2 = make_agent("正文写作", name="a2")
        default_runner = make_runner(project_dir, {"1": a1, "2": a2})
        assert default_runner.find_agent("正文写作") is a1
        assert default_runner.find_agent("正文写作") is a1  # 默认固定第一个

        rotating = make_runner(project_dir, {"1": a1, "2": a2}, rotate_agents=True)
        picks = [rotating.find_agent("正文写作") for _ in range(4)]
        assert picks == [a1, a2, a1, a2]  # 轮换交替（cycle 从第一个开始）


class TestStepCompletedOutputMissing:
    """进度 done 但产出文件被删 → 视为未完成，允许重跑。"""

    def test_done_without_output_file_reruns(self, tmp_path):
        from src.novel_writer.core.workflow.runner import WorkflowRunner
        from src.novel_writer.core.workflow.definition import WorkflowStep
        step = WorkflowStep(id="chapter_outline", output="planning/章节细纲.md")
        progress = {"chapter_outline": "done"}
        assert WorkflowRunner._step_completed(step, progress, tmp_path) is False
        # 写出文件后才算完成
        out = tmp_path / "planning" / "章节细纲.md"
        out.parent.mkdir(parents=True)
        out.write_text("## 第1章", encoding="utf-8")
        assert WorkflowRunner._step_completed(step, progress, tmp_path) is True

    def test_done_without_output_path_keeps_done(self):
        from src.novel_writer.core.workflow.runner import WorkflowRunner
        from src.novel_writer.core.workflow.definition import WorkflowStep
        step = WorkflowStep(id="toc", output="")
        assert WorkflowRunner._step_completed(step, {"toc": "done"}, None) is True
