"""重构后的工作流包回归测试。

锁定模板去重、内置步骤注册表、上下文压缩优先级与聊天记录持久化的行为。
"""
import pytest

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
    "main_plot", "sub_plot", "foreshadow", "fix_titles",
    "inspiration", "sim", "chapter", "chapter_summary", "world_state_update",
    "toc", "polish", "proofread", "summary",
    "update_outline", "update_characters", "update_foreshadow",
    "quality_check", "review",
]

EXPECTED_CONTINUE_IDS = [
    "fix_titles",
    "inspiration", "sim", "chapter", "chapter_summary", "world_state_update",
    "toc", "polish", "proofread", "summary",
    "update_outline", "update_characters", "update_foreshadow",
    "quality_check", "review",
]

EXPECTED_VALIDATE_IDS = ["fix_titles", "toc", "review", "proofread"]

EXPECTED_PLANNING_IDS = [
    "ideation", "outline", "characters", "world", "timeline",
    "main_plot", "sub_plot", "foreshadow",
]


class TestTemplateEquivalence:
    def test_default_step_order(self):
        assert [s["id"] for s in DEFAULT_WORKFLOW["steps"]] == EXPECTED_DEFAULT_IDS

    def test_continue_step_order(self):
        assert [s["id"] for s in CONTINUE_WORKFLOW["steps"]] == EXPECTED_CONTINUE_IDS

    def test_validate_step_order(self):
        assert [s["id"] for s in VALIDATE_WORKFLOW["steps"]] == EXPECTED_VALIDATE_IDS

    def test_planning_step_order(self):
        assert [s["id"] for s in PLANNING_WORKFLOW["steps"]] == EXPECTED_PLANNING_IDS

    def test_planning_is_prefix_of_default(self):
        default_ids = [s["id"] for s in DEFAULT_WORKFLOW["steps"]]
        assert EXPECTED_PLANNING_IDS == default_ids[:len(EXPECTED_PLANNING_IDS)]

    def test_continue_has_no_planning_steps(self):
        continue_ids = {s["id"] for s in CONTINUE_WORKFLOW["steps"]}
        planning_ids = {s["id"] for s in PLANNING_WORKFLOW["steps"]}
        assert not (continue_ids & planning_ids)

    def test_periodic_steps_preserved(self):
        """定时步骤的 every 间隔必须保持不变。"""
        by_id = {s["id"]: s for s in DEFAULT_WORKFLOW["steps"]}
        assert by_id["inspiration"]["every"] == 3
        assert by_id["sim"]["every"] == 1
        assert by_id["polish"]["every"] == 2
        assert by_id["summary"]["every"] == 5
        for sid in ("update_outline", "update_characters", "update_foreshadow"):
            assert by_id[sid]["every"] == 10
            assert by_id[sid]["update_planning"] is True

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
        }

    def test_registry_steps_absent_from_standard_path(self):
        """内置步骤没有 needs，不应进入标准 LLM 步骤路径。"""
        for s in DEFAULT_WORKFLOW["steps"]:
            if s["id"] in BUILTIN_STEP_HANDLERS:
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
