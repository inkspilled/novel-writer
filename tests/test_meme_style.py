"""梗密度（现代梗）约束与体检的测试。"""
from __future__ import annotations

import json

from src.novel_writer.core.meme_style import (
    DEFAULT_MEME_LEVEL,
    MEME_LEVELS,
    build_meme_constraint,
    measure_memes,
    normalize_meme_level,
)
from src.novel_writer.core.narrative_voice import build_voice_constraint
from src.novel_writer.core.quality_checker import QualityChecker
from src.novel_writer.core.workflow.context import ContextBuilder
from src.novel_writer.core.workflow.definition import WorkflowMode, build_workflow
from src.novel_writer.models.project import Project

MEME_TEXT = (
    "顾言看了一眼工牌，心想这不就是996吗。管事画的饼比丹炉还大，"
    "他决定先摸鱼，再找机会内卷回去。旁边人都在躺平，只有他还在打卡。"
) * 8


def test_normalize_meme_level():
    assert normalize_meme_level("") == DEFAULT_MEME_LEVEL
    assert normalize_meme_level(None) == DEFAULT_MEME_LEVEL
    assert normalize_meme_level("关") == "无"
    assert normalize_meme_level("低密度") == "少"
    assert normalize_meme_level("适中") == "中"
    assert normalize_meme_level("高密度") == "高"
    assert normalize_meme_level("未知档位") == DEFAULT_MEME_LEVEL
    assert set(MEME_LEVELS) == {"无", "少", "中", "高"}


def test_constraint_off_forbids_memes():
    text = build_meme_constraint("无", "仙侠")
    assert "不使用任何现代网络梗" in text
    assert "1–2 个" not in text


def test_constraint_mid_sets_quota_and_placement():
    text = build_meme_constraint("中", "仙侠", "大白话")
    assert "3–5 个" in text
    assert "996" in text
    assert "主角心理吐槽" in text
    assert "世界内人物不能把 996/KPI 当日常词" in text


def test_constraint_modern_genre_allows_natural_use():
    text = build_meme_constraint("中", "都市")
    assert "可以自然出现在对话和旁白里" in text


def test_voice_constraint_defers_to_meme_level():
    on = build_voice_constraint("大白话", "仙侠", "中")
    off = build_voice_constraint("大白话", "仙侠", "无")
    assert "现代梗按【梗密度】控制" in on
    assert "不用现代流行语" in off


def test_measure_memes_counts_and_density():
    metrics = measure_memes(MEME_TEXT, "中")
    assert metrics.chars > 300
    assert metrics.count >= 4
    assert {"996", "画饼", "摸鱼", "内卷", "躺平", "打卡"} & set(metrics.hits)
    assert metrics.per_k > 0


def test_measure_memes_off_reports_violation():
    metrics = measure_memes(MEME_TEXT, "无")
    assert metrics.count > 0
    assert any("梗密度=无" in issue for issue in metrics.issues)


def test_measure_memes_reports_too_few_for_mid():
    plain = "顾言走进执事堂，把申辩状放在案上。赵九衡看了很久。" * 20
    metrics = measure_memes(plain, "中")
    assert metrics.count == 0
    assert any("偏少" in issue for issue in metrics.issues)


def test_measure_memes_reports_repetition():
    text = "摸鱼摸鱼摸鱼，他就想摸鱼。" * 30
    metrics = measure_memes(text, "少")
    assert any("重复" in issue for issue in metrics.issues)


def test_quality_report_includes_meme_section(tmp_path):
    checker = QualityChecker(tmp_path)
    report = checker.check_chapter(MEME_TEXT, 3, voice="大白话", meme_level="中", genre="仙侠")
    assert report.meme_level == "中"
    assert report.meme_metrics["count"] > 0
    text = checker.generate_report_text(report)
    assert "梗密度（中" in text
    assert "现代梗" in text


def test_context_injects_meme_block_only_for_chapter(tmp_path):
    (tmp_path / "meta.json").write_text(
        json.dumps({"title": "测试书", "meme_level": "中"}, ensure_ascii=False),
        encoding="utf-8",
    )
    info = {
        "title": "测试书",
        "genre": "仙侠",
        "narrative_voice": "大白话",
        "meme_level": "中",
    }
    builder = ContextBuilder(tmp_path, info)
    chapter = builder.build([], 1, step_id="chapter")
    polish = builder.build([], 1, step_id="polish")
    assert "梗密度 · 中" in chapter
    assert "梗密度 · 中" not in polish


def test_project_round_trips_meme_level(tmp_path):
    project = Project(title="测试书", meme_level="高")
    project.set_project_dir(tmp_path)
    project.save()
    loaded = Project.load(tmp_path)
    assert loaded.meme_level == "高"


def test_build_workflow_carries_meme_level():
    wf = build_workflow(
        WorkflowMode.NEW_BOOK,
        {"title": "测试书", "meme_level": "高密度"},
        1,
        10,
    )
    assert wf.project["meme_level"] == "高"
