"""离线缓存模拟器回归：组装可跑、行数正确、命中占比合法。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tools.cache_sim import simulate
from src.novel_writer.core.workflow.definition import WorkflowStep


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    (tmp_path / "planning").mkdir()
    (tmp_path / "chapters").mkdir()
    (tmp_path / "planning" / "人物设定.md").write_text("# 人物\n林牧：主角", encoding="utf-8")
    (tmp_path / "planning" / "大纲.md").write_text("第一卷：开局\n### 第1章\n awake", encoding="utf-8")
    for n in (1, 2, 3):
        (tmp_path / "chapters" / f"{n}_第{n}章.txt").write_text(
            f"第{n}章正文。" + "剧情推进。" * 50, encoding="utf-8")
    return tmp_path


def _steps() -> list[WorkflowStep]:
    return [
        WorkflowStep(id="chapter", needs="正文写作", prompt="写第{n}章",
                     input_files=["planning/大纲.md", "planning/人物设定.md"],
                     output="chapters/{n}_第{n}章.txt", repeat=0),
        WorkflowStep(id="proofread", needs="错别字检查", prompt="校对第{n}章",
                     input_files=["chapters/{n}_第{n}章.txt"],
                     output="review/校对报告_第{n}章.md", every=1, repeat=0),
    ]


def test_simulate_rows_structure_and_bounds(sample_project):
    info = {"target_chapters": 3, "title": "测"}
    rows = simulate(sample_project, info, _steps(), chapters=3)
    # 每章两步（proofread every=1），共 6 行
    assert len(rows) == 6
    for r in rows:
        assert r.in_tokens > 0
        assert 0 <= r.hit_tokens <= r.in_tokens
        assert 0.0 <= r.hit_pct <= 100.0
    assert {r.step for r in rows} == {"chapter", "proofread"}


def test_simulate_hit_grows_when_prefix_reused(sample_project):
    info = {"target_chapters": 3, "title": "测"}
    rows = simulate(sample_project, info, _steps(), chapters=3)
    by_chapter = {r.chapter: r for r in rows if r.step == "chapter"}
    # 第 2 章起与第 1 章共享 system 前缀，命中不得为 0（块取整允许很小但应 > 0）
    assert by_chapter[2].hit_tokens > 0
    assert by_chapter[3].hit_tokens > 0


def test_simulate_respects_ttl(sample_project):
    info = {"target_chapters": 3, "title": "测"}
    warm = simulate(sample_project, info, _steps(), chapters=3, ttl_chapters=0)
    cold = simulate(sample_project, info, _steps(), chapters=3, ttl_chapters=1)
    warm_total = sum(r.hit_tokens for r in warm)
    cold_total = sum(r.hit_tokens for r in cold)
    assert cold_total <= warm_total
