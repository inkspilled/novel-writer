"""章节封印：定稿摘要入正典，禁止覆写。"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.novel_writer.core.canon.seal import (
    ChapterSealedError, is_sealed, seal_chapter, sealed_info,
)


def _chapter(tmp_path: Path, text: str = "# 第1章 开端\n正文") -> Path:
    d = tmp_path / "chapters"; d.mkdir()
    p = d / "1_开端.txt"
    p.write_text(text, encoding="utf-8")
    return p


def test_seal_is_idempotent_and_detects_change(tmp_path: Path):
    p = _chapter(tmp_path)
    event = seal_chapter(tmp_path, 1)
    assert event is not None
    assert is_sealed(tmp_path, 1)
    assert sealed_info(tmp_path, 1)["digest"]

    # 内容未变：幂等
    assert seal_chapter(tmp_path, 1) is None

    # 内容被改：拒绝再次封印
    p.write_text("# 第1章 开端\n改过的正文", encoding="utf-8")
    with pytest.raises(ChapterSealedError):
        seal_chapter(tmp_path, 1)


def test_manual_edit_of_sealed_chapter_creates_revision(tmp_path: Path):
    from src.novel_writer.models.chapter import Chapter
    from src.novel_writer.core import project_io

    p = _chapter(tmp_path, "# 第1章 开端\n原始正文" * 20)
    seal_chapter(tmp_path, 1)
    chapter = Chapter(number=1, title="开端", _content_path=str(p))
    chapter.content = "# 第1章 开端\n修订后的正文" * 20

    # 原件保持封印内容
    assert "原始正文" in p.read_text(encoding="utf-8")
    # 当前章节指向版本文件
    assert "versions" in chapter._content_path
    assert "修订后的正文" in chapter.content
    # 重新扫描项目时使用最新版本
    items = project_io.scan_chapters(tmp_path)
    assert len(items) == 1
    assert "versions" in str(items[0]["content_path"])
    assert "修订后的正文" in items[0]["content_path"].read_text(encoding="utf-8")
