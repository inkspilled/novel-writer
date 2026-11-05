"""重构后的工作流包回归测试。

锁定模板去重、内置步骤注册表、上下文压缩优先级与聊天记录持久化的行为。
"""

from src.novel_writer.core.workflow import (
    BUILTIN_STEP_HANDLERS,
    CONTINUE_WORKFLOW,
    DEFAULT_WORKFLOW,
    PLANNING_WORKFLOW,
    VALIDATE_WORKFLOW,
    WorkflowMode,
    build_workflow,
    compress_context,
)


# ── 模板去重等价性：步骤 id 序列必须与重构前完全一致 ──

EXPECTED_DEFAULT_IDS = [
    "ideation", "outline", "characters", "world", "timeline",
    "main_plot", "sub_plot", "foreshadow", "chapter_outline", "fix_titles",
    "inspiration", "sim", "character_panel", "chapter", "polish", "chapter_digest",
    "proofread", "toc", "chapter_summary", "quality_check",
    "world_state_update", "percept_extract", "seal_chapter", "offscreen_tick", "summary",
    "update_characters",
    "update_chapter_outline", "review_panel",
    "review",
]

EXPECTED_CONTINUE_IDS = [
    "fix_titles", "chapter_outline",
    "inspiration", "sim", "character_panel", "chapter", "polish", "chapter_digest",
    "proofread", "toc", "chapter_summary", "quality_check",
    "world_state_update", "percept_extract", "seal_chapter", "offscreen_tick", "summary",
    "update_characters",
    "update_chapter_outline", "review_panel",
    "review",
]

EXPECTED_VALIDATE_IDS = ["fix_titles", "toc", "review", "proofread"]

EXPECTED_PLANNING_IDS = [
    "ideation", "outline", "characters", "world", "timeline",
    "main_plot", "sub_plot", "foreshadow", "chapter_outline",
]


class TestTemplateEquivalence:
    def test_default_step_order(self):
        assert [s["id"] for s in DEFAULT_WORKFLOW["steps"]] == EXPECTED_DEFAULT_IDS

    def test_polish_runs_before_post_extraction(self):
        """润色必须先于概要/世界状态/沉淀，否则章后数据基于草稿。"""
        ids = [s["id"] for s in DEFAULT_WORKFLOW["steps"]]
        assert ids.index("chapter") < ids.index("polish") < ids.index("chapter_summary")
        assert ids.index("polish") < ids.index("world_state_update")
        assert ids.index("polish") < ids.index("percept_extract")

    def test_continue_step_order(self):
        assert [s["id"] for s in CONTINUE_WORKFLOW["steps"]] == EXPECTED_CONTINUE_IDS

    def test_validate_step_order(self):
        assert [s["id"] for s in VALIDATE_WORKFLOW["steps"]] == EXPECTED_VALIDATE_IDS

    def test_planning_step_order(self):
        assert [s["id"] for s in PLANNING_WORKFLOW["steps"]] == EXPECTED_PLANNING_IDS

    def test_planning_core_is_prefix_of_default(self):
        default_ids = [s["id"] for s in DEFAULT_WORKFLOW["steps"]]
        core = EXPECTED_PLANNING_IDS[:-1]  # 细纲已移到文末
        assert core == default_ids[:len(core)]

    def test_continue_only_reuses_chapter_outline(self):
        """续写不重跑规划链，只允许补一份缺失的章节细纲。"""
        continue_ids = {s["id"] for s in CONTINUE_WORKFLOW["steps"]}
        planning_ids = {s["id"] for s in PLANNING_WORKFLOW["steps"]}
        assert continue_ids & planning_ids == {"chapter_outline"}

    def test_periodic_steps_preserved(self):
        """定时步骤的 every 间隔：高频只留必要项，规划反哺降到 10 章。"""
        by_id = {s["id"]: s for s in DEFAULT_WORKFLOW["steps"]}
        assert by_id["inspiration"]["every"] == 3
        assert by_id["sim"]["every"] == 1
        assert by_id["polish"]["every"] == 2
        assert by_id["summary"]["every"] == 5
        assert by_id["update_chapter_outline"]["every"] == 5
        assert by_id["update_characters"]["every"] == 10
        assert by_id["update_characters"]["update_planning"] is True
        # 伏笔不再回写规划稿：台账是唯一来源
        assert "update_foreshadow" not in by_id

    def test_templates_not_mutated_by_build(self):
        """build_workflow 的深拷贝不能污染模块级模板。"""
        build_workflow(WorkflowMode.NEW_BOOK, {"title": "测试", "genre": "玄幻", "style": "热血"},
                       start_chapter=1, end_chapter=5)
        assert [s["id"] for s in DEFAULT_WORKFLOW["steps"]] == EXPECTED_DEFAULT_IDS
        chapter = next(s for s in DEFAULT_WORKFLOW["steps"] if s["id"] == "chapter")
        assert "repeat" not in chapter


# ── build_workflow 模式行为 ──

class TestBuildWorkflowRefactor:
    def test_continue_sets_start_chapter(self):
        wf = build_workflow(WorkflowMode.CONTINUE, {"title": "书", "genre": "玄幻", "style": "热血"},
                            start_chapter=11, end_chapter=30)
        assert wf.project["_start_chapter"] == 11
        chapter = next(s for s in wf.steps if s.id == "chapter")
        assert "第11章到第30章" in chapter.prompt

    def test_fill_gaps_only_missing_chapters(self, tmp_path):
        ch_dir = tmp_path / "chapters"
        ch_dir.mkdir(parents=True)
        (ch_dir / "1_第一章.txt").write_text("内容" * 100, encoding="utf-8")
        (ch_dir / "2_第二章.txt").write_text("", encoding="utf-8")  # 空文件算缺失
        project_info = {"title": "书", "_project_dir": str(tmp_path)}
        wf = build_workflow(WorkflowMode.FILL_GAPS, project_info, start_chapter=1, end_chapter=4)
        assert wf.project["_gaps"] == [2, 3, 4]


# ── 内置步骤注册表 ──

class TestBuiltinStepHandlers:
    def test_all_builtin_steps_registered(self):
        assert set(BUILTIN_STEP_HANDLERS) == {
            "toc", "fix_titles", "world_state_update", "chapter_summary", "quality_check",
            "proofread", "review",  # 批处理改造后由 handler 自行编排 LLM（分批校对/审核）
            "chapter_outline", "update_chapter_outline", "review_panel", "pov_sim",
            "character_panel",
            "percept_extract", "seal_chapter", "offscreen_tick", "chapter_digest",
        }

    def test_digest_runs_after_polish_and_before_consumers(self):
        """章后抽取必须读润色后的最终正文，且早于四个消费步骤。"""
        ids = [s["id"] for s in DEFAULT_WORKFLOW["steps"]]
        assert ids.index("polish") < ids.index("chapter_digest")
        for consumer in ("chapter_summary", "world_state_update", "percept_extract"):
            assert ids.index("chapter_digest") < ids.index(consumer)

    def test_parallel_post_steps_are_adjacent_and_disjoint(self):
        """parallel_ok 只用在不写同一份文件的步骤；相邻才真的会并发。"""
        steps = {s["id"]: s for s in DEFAULT_WORKFLOW["steps"]}
        parallel = [sid for sid, s in steps.items() if s.get("parallel_ok")]
        assert "polish" not in parallel
        assert "world_state_update" not in parallel
        assert "percept_extract" not in parallel
        ids = [s["id"] for s in DEFAULT_WORKFLOW["steps"]]
        positions = [ids.index(sid) for sid in parallel]
        assert positions == list(range(min(positions), max(positions) + 1))

    def test_chapter_step_drops_archived_planning_docs(self):
        """四份归档规划稿不再进正文上下文；主线/伏笔改由台账反哺。"""
        chapter = next(s for s in DEFAULT_WORKFLOW["steps"] if s["id"] == "chapter")
        for name in ("时间线.md", "主线.md", "支线.md", "伏笔.md"):
            assert f"planning/{name}" not in chapter["input"]
        assert "planning/大纲.md" in chapter["input"]

    def test_tool_steps_have_no_needs(self):
        """纯工具步骤没有 needs；proofread/review 走注册表分发但不在此列。"""
        tool_steps = {"toc", "fix_titles", "chapter_summary", "world_state_update", "quality_check"}
        for s in DEFAULT_WORKFLOW["steps"]:
            if s["id"] in tool_steps:
                assert s.get("needs", "") == ""


# ── 上下文压缩优先级 ──

def _cn_part(header: str, chars: int) -> str:
    return header + "测" * chars


class TestCompressContext:
    def test_no_compression_within_budget(self):
        parts = ["短文本", "另一段"]
        assert compress_context(parts, max_tokens=10_000) == parts

    def test_rag_trimmed_before_chapters(self):
        """RAG 检索结果先于旧章节全文被裁剪。"""
        rag = _cn_part("=== RAG 检索结果 ===\n", 600)
        chapter = _cn_part("=== 第3章 ===\n", 600)
        summary = _cn_part("=== 章节概要 ===\n", 100)
        result = compress_context([rag, chapter, summary], max_tokens=1500)
        joined = "\n\n".join(result)
        assert "=== RAG" not in joined      # 最先裁剪
        assert "=== 第3章" in joined        # 章节全文保留
        assert "=== 章节概要" in joined

    def test_chapters_trimmed_before_planning(self):
        """旧章节全文先于规划文档被裁剪。"""
        chapter = _cn_part("=== 第3章 ===\n", 600)
        planning = _cn_part("=== 规划文档（摘要）===\n", 600)
        result = compress_context([chapter, planning], max_tokens=1000)
        joined = "\n\n".join(result)
        assert "=== 第3章" not in joined
        assert "=== 规划文档" in joined

    def test_summary_trimmed_after_planning(self):
        """裁剪优先级：规划文档先于章节概要被裁剪（概要更靠近末位）。"""
        planning = _cn_part("=== 规划文档（摘要）===\n", 600)
        summary = _cn_part("=== 章节概要 ===\n", 600)
        result = compress_context([planning, summary], max_tokens=1200)
        joined = "\n\n".join(result)
        assert "=== 规划文档" not in joined
        assert "=== 章节概要" in joined

    def test_never_removes_unmarked_parts(self):
        """无标记的 other 部分不参与裁剪。"""
        other = _cn_part("项目: 测试小说\n", 800)
        rag = _cn_part("=== RAG ===\n", 600)
        result = compress_context([rag, other], max_tokens=500)
        assert other in result  # other 不可裁剪，只能靠 rag 腾空间


class TestPolishContext:
    def test_polish_gets_full_chapter_not_rag_fragments(self, tmp_path):
        """润色必须看到完整正文；只给 top-5 片段会让模型无米下锅。"""
        from src.novel_writer.core.workflow.context import ContextBuilder

        chapters = tmp_path / "chapters"
        chapters.mkdir()
        opening = "开头独一无二：凌尘推开山门，风雪灌了进来。"
        ending = "结尾独一无二：他握紧骨牌，决定明日下山。"
        body = opening + "\n\n" + ("中间段落，用于制造足够长的正文。" * 40) + "\n\n" + ending
        (chapters / "1_风雪.txt").write_text(body, encoding="utf-8")

        ctx = ContextBuilder(tmp_path, {"title": "测试书"}, 48_000).build(
            ["chapters/{n}_chapter.txt"], 1, "polish")
        assert "当前章节正文（全文，待润色）" in ctx
        assert opening in ctx
        assert ending in ctx

    def test_summary_context_gets_recent_chapter_summaries(self, tmp_path):
        """滚动摘要/update_* 至少要看近几章的结构化概要，不能只二次压缩旧摘要。"""
        from src.novel_writer.core.workflow.context import ContextBuilder

        chapters = tmp_path / "chapters"
        chapters.mkdir()
        for i, title in ((1, "开端"), (2, "转折")):
            (chapters / f"{i}_{title}.txt").write_text(
                f"第{i}章正文，用于占位。", encoding="utf-8")
            (chapters / f"{i}_{title}.summary.md").write_text(
                f"第{i}章核心事件：独一无二的事件{i}。", encoding="utf-8")

        ctx = ContextBuilder(tmp_path, {"title": "测试书"}, 48_000).build(
            ["prev_chapters"], 2, "summary")
        assert "=== 近章结构化概要 ===" in ctx
        assert "独一无二的事件1" in ctx
        assert "独一无二的事件2" in ctx
