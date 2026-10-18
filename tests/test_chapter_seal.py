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
