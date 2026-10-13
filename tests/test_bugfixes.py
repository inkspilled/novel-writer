"""关键 BUG 修复的回归测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.novel_writer.core.app_config import safe_project_dirname
from src.novel_writer.core import project_io
from src.novel_writer.core.workflow.context import compress_context, estimate_tokens, extract_chapter_section


# ── 上下文压缩：阶段二不得双重扣减 ──

class TestCompressContextBudget:
    def test_phase2_respects_budget(self):
        """阶段二裁剪后 token 和必须 ≤ max_tokens（曾因双重扣减严重超限）。"""
        parts = [
            "=== RAG ===\n" + "检索块内容" * 16000,
            "=== 第1章 ===\n" + "章节正文" * 12000,
            "=== 章节概要 ===\n" + "概要内容" * 2000,
        ]
        out = compress_context(parts, max_tokens=50000)
        assert sum(estimate_tokens(p) for p in out) <= 50000

    def test_under_budget_untouched(self):
        small = ["=== 章节概要 ===\n短内容"]
        assert compress_context(small, max_tokens=1000) == small


# ── 摘要选取：按章号而非字典序 ──

class TestLatestSummary:
    def test_picks_highest_chapter(self, tmp_path: Path):
        sdir = tmp_path / "summary"
        sdir.mkdir()
        (sdir / "第1-5章摘要.md").write_text("5", encoding="utf-8")
        (sdir / "第1-10章摘要.md").write_text("10", encoding="utf-8")
        (sdir / "第1-50章摘要.md").write_text("50", encoding="utf-8")
        (sdir / "第1-9章摘要.md").write_text("9", encoding="utf-8")
        assert project_io.latest_summary(tmp_path) == "50"


# ── 大纲章节号边界 ──

class TestExtractChapterSection:
    def test_n1_does_not_match_11(self):
        outline = "# 大纲\n# 1. 开篇\n主角出场。\n# 11. 高潮\n大战。\n"
        section = extract_chapter_section(outline, 1)
        assert "主角出场" in section
        assert "大战" not in section

    def test_n11_matches_11(self):
        outline = "# 大纲\n# 1. 开篇\n主角出场。\n# 11. 高潮\n大战。\n"
        section = extract_chapter_section(outline, 11)
        assert "大战" in section


# ── 章节重命名 ──

class TestRenameChapter:
    def test_rename_chapter_no_nameerror(self, tmp_path: Path):
        project_io.init_project_dir(tmp_path)
        f = tmp_path / "chapters" / "1_初来乍到.txt"
        f.write_text("# 第1章：初来乍到\n\n正文内容测试。\n", encoding="utf-8")
        old = project_io.rename_chapter(tmp_path, 1, "云涌")
        assert old == "初来乍到"
        assert (tmp_path / "chapters" / "1_云涌.txt").exists()

    def test_safe_rename_conflict(self, tmp_path: Path):
        project_io.init_project_dir(tmp_path)
        (tmp_path / "chapters" / "1_甲.txt").write_text("# 甲\n\n内容", encoding="utf-8")
        (tmp_path / "chapters" / "1_乙.txt").write_text("# 乙\n\n内容", encoding="utf-8")
        # 扫描到 number=1 的第一个；冲突检测应拒绝覆盖已存在文件
        old, err = project_io.safe_rename_chapter(tmp_path, 1, "丙")
        # 要么成功改名，要么报冲突；绝不能抛 NameError
        assert isinstance(err, str)


# ── 项目目录名清洗 ──

class TestSafeProjectDirname:
    def test_blocks_path_traversal(self):
        assert ".." not in safe_project_dirname("..")
        assert "/" not in safe_project_dirname("a/b")
        assert "\\" not in safe_project_dirname("a\\b")

    def test_windows_reserved(self):
        assert safe_project_dirname("con").upper() != "CON"
        assert safe_project_dirname("NUL").upper() != "NUL"

    def test_empty_fallback(self):
        assert safe_project_dirname("   ") == "未命名项目"
        assert safe_project_dirname("") == "未命名项目"


# ── 原子写 JSON ──

class TestWriteJson:
    def test_roundtrip(self, tmp_path: Path):
        p = tmp_path / "x.json"
        project_io.write_json(p, {"a": 1, "中文": "值"})
        assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "中文": "值"}
        assert not list(tmp_path.glob("*.tmp"))

    def test_load_meta_corrupt(self, tmp_path: Path):
        (tmp_path / "meta.json").write_text("{not json", encoding="utf-8")
        assert project_io.load_meta(tmp_path) == {}


# ── 信息边界：告诉句式记在被告知者头上 ──

class TestInfoBoundary:
    def test_tell_records_receiver(self, tmp_path: Path):
        from src.novel_writer.core.memory import MemoryScratchpad
        from src.novel_writer.core.chapter_tracker import track_info_boundary
        mem = MemoryScratchpad(tmp_path)
        track_info_boundary(mem, 1, "张三告诉李四关于玄铁令的秘密所在。")
        subjects = [i.get("subject") for i in mem.get_active("info_boundary")]
        assert "李四" in subjects
        assert "张三" not in subjects
