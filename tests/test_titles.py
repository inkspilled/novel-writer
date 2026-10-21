"""章节标题校准回归：只修坏标题，正常标题不得被批量改写。"""
from __future__ import annotations

from src.novel_writer.core.workflow.builtin_steps import (
    _pick_title_problems,
    _title_subtitle,
)


def test_title_subtitle_strips_prefix():
    assert _title_subtitle("第1章 欠薪", 1) == "欠薪"
    assert _title_subtitle("第一章：欠薪", 1) == "欠薪"
    assert _title_subtitle("第12章：旧库对峙", 12) == "旧库对峙"


def test_healthy_titles_are_not_touched():
    chapters = [
        {"number": 1, "title": "第1章 欠薪"},
        {"number": 2, "title": "第2章 杖责"},
        {"number": 3, "title": "第3章 道种"},
    ]
    problems, style = _pick_title_problems(chapters)
    assert problems == []
    assert style == ["欠薪", "杖责", "道种"]


def test_placeholder_and_duplicate_titles_are_selected():
    chapters = [
        {"number": 1, "title": "第1章 欠薪"},
        {"number": 2, "title": "第2章 第2章"},
        {"number": 3, "title": "第3章 欠薪"},
        {"number": 4, "title": "第4章 暂定"},
    ]
    problems, _style = _pick_title_problems(chapters)
    assert {ch["number"] for ch in problems} == {1, 2, 3, 4}
