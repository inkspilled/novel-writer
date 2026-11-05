"""叙述语言（大白话约束）与质量检查语言质感诊断的测试。"""
from __future__ import annotations

from src.novel_writer.core.narrative_voice import (
    DEFAULT_VOICE,
    VOICE_OPTIONS,
    build_voice_constraint,
    measure_voice,
    normalize_voice,
    voice_profile,
)
from src.novel_writer.core.quality_checker import QualityChecker
from src.novel_writer.core.workflow.context import ContextBuilder
from src.novel_writer.core.workflow.definition import WorkflowMode, build_workflow
from src.novel_writer.models.project import Project

ORNATE = """他心中涌起一股难以言喻的悲凉，仿佛整个世界都在这一刻沉默下来，连风也停了。
然而，他旋即压下这丝情绪，面无表情地望向远方的山影。
与此同时，一道淡淡的虚影缓缓浮现，犹如从岁月深处走来一般，带着说不清道不明的意味。
他不由得心头一震，眼中闪过一丝不易察觉的慌乱，暗道：“竟有此事？”
他深吸一口气，缓缓抬起手，指尖微微发颤，仿佛要抓住什么，又仿佛什么都抓不住。
良久，他才回过神来，若有所思地垂下眼，心中五味杂陈，难以平静。
远处的钟声悠悠传来，他默然转身，脚步声在空旷的大殿里回荡，渐行渐远。
他知道，有些东西一旦开始，就再也回不去了；然而事已至此，也只能走一步看一步。
""" * 2

PLAIN = """顾言把铁环举到窗边。风从缝里钻进来，他手背有点冷。
他看了一会儿，没看懂。环上那道刻痕很浅，像被谁用指甲划的。
“走吧。”他把铁环塞进怀里，又摸了摸胸口。东西都在。
门外有人在扫地。他等那人扫过去，才推门出去。
日头很毒。他眯着眼，沿着墙根走。走到一半，听见身后有人喊他。
他没回头，脚步快了些。"""


def test_normalize_voice_defaults_to_plain():
    assert normalize_voice("") == DEFAULT_VOICE
    assert normalize_voice(None) == DEFAULT_VOICE
    assert normalize_voice("不存在的档位") == DEFAULT_VOICE
    assert DEFAULT_VOICE in VOICE_OPTIONS


def test_normalize_voice_accepts_aliases():
    assert normalize_voice("口语化") == "大白话"
    assert normalize_voice("朴素") == "平实"
    assert normalize_voice("文艺") == "文学"
    assert normalize_voice("大白话（推荐）") == "大白话"


def test_plain_constraint_has_hard_rules():
    text = build_voice_constraint("大白话")
    assert "语言质感" in text
    assert "每千字不超过 3 个" in text
    assert "平均不超过 18 字" in text
    assert "不憋金句" in text


def test_constraint_mentions_style_without_asking_for_orange_prose():
    text = build_voice_constraint("大白话", style="史诗宏大")
    assert "史诗宏大" in text
    assert "不靠形容词" in text


def test_literary_profile_is_looser_than_plain():
    plain = voice_profile("大白话")
    literary = voice_profile("文学")
    assert literary.max_idiom_per_k > plain.max_idiom_per_k
    assert literary.max_sentence_len > plain.max_sentence_len


def test_measure_voice_flags_ornate_text():
    metrics = measure_voice(ORNATE, "大白话")
    assert metrics.chars > 300
    assert metrics.idiom_per_k > 3
    assert metrics.bookish_per_k > 2
    assert metrics.simile_per_k > 1
    assert metrics.issues


def test_measure_voice_accepts_plain_text():
    metrics = measure_voice(PLAIN, "大白话")
    assert metrics.chars > 100
    assert metrics.idiom_per_k <= 3
    assert metrics.simile_per_k <= 1
    assert metrics.issues == []


def test_quality_report_includes_voice_diagnostics(tmp_path):
    checker = QualityChecker(tmp_path)
    report = checker.check_chapter(ORNATE, 3, voice="大白话")
    assert report.voice == "大白话"
    assert report.voice_issues
    assert report.voice_metrics["idiom_per_k"] > 0
    text = checker.generate_report_text(report)
    assert "语言质感（大白话" in text
    assert "成语/四字词" in text


def test_context_injects_voice_block_for_chapter(tmp_path):
    (tmp_path / "planning").mkdir()
    info = {
        "title": "测试书",
        "genre": "仙侠",
        "style": "史诗宏大",
        "narrative_voice": "大白话",
    }
    builder = ContextBuilder(tmp_path, info)
    context = builder.build(["planning/大纲.md"], 1, step_id="chapter")
    assert "语言质感 · 大白话" in context
    assert "史诗宏大" in context


def test_context_skips_voice_block_for_review(tmp_path):
    info = {"title": "测试书", "genre": "仙侠", "style": "史诗宏大", "narrative_voice": "大白话"}
    builder = ContextBuilder(tmp_path, info)
    context = builder.build([], 1, step_id="review")
    assert "语言质感" not in context


def test_context_falls_back_to_meta_when_project_info_is_stale(tmp_path):
    (tmp_path / "meta.json").write_text(
        '{"title": "测试书", "narrative_voice": "平实"}', encoding="utf-8"
    )
    builder = ContextBuilder(tmp_path, {"title": "测试书"})
    context = builder.build([], 1, step_id="chapter")
    assert "语言质感 · 平实" in context


def test_project_round_trips_voice(tmp_path):
    project = Project(title="测试书", genre="仙侠", narrative_voice="平实")
    project.set_project_dir(tmp_path)
    project.save()
    loaded = Project.load(tmp_path)
    assert loaded.narrative_voice == "平实"


def test_project_defaults_to_plain_when_meta_has_no_voice(tmp_path):
    (tmp_path / "meta.json").write_text('{"title": "旧项目"}', encoding="utf-8")
    loaded = Project.load(tmp_path)
    assert loaded.narrative_voice == DEFAULT_VOICE


def test_build_workflow_carries_normalized_voice():
    wf = build_workflow(
        WorkflowMode.NEW_BOOK,
        {"title": "测试书", "narrative_voice": "口语化"},
        1,
        10,
    )
    assert wf.project["narrative_voice"] == "大白话"


def test_refresh_builtin_workflow_uses_current_prompts():
    from src.novel_writer.core.workflow.definition import (
        DEFAULT_WORKFLOW,
        refresh_builtin_workflow,
    )

    stale = {
        "name": DEFAULT_WORKFLOW["name"],
        "steps": [{**step, "prompt": "老提示词"} for step in DEFAULT_WORKFLOW["steps"]],
        "project": {"title": "测试书", "target_chapters": 20},
    }
    fresh = refresh_builtin_workflow(stale, {"title": "测试书", "narrative_voice": "大白话"}, 6)
    assert fresh is not None
    assert fresh["project"]["narrative_voice"] == "大白话"
    polish = next(s for s in fresh["steps"] if s["id"] == "polish")
    assert "减法润色" in polish["prompt"]


def test_refresh_builtin_workflow_keeps_custom_workflow():
    from src.novel_writer.core.workflow.definition import refresh_builtin_workflow

    custom = {"name": "我的自定义流程", "steps": [{"id": "chapter"}], "project": {"target_chapters": 20}}
    assert refresh_builtin_workflow(custom, {"title": "测试书"}, 1) is None


def test_refresh_builtin_workflow_keeps_modified_structure():
    from src.novel_writer.core.workflow.definition import (
        DEFAULT_WORKFLOW,
        refresh_builtin_workflow,
    )

    modified = {
        "name": DEFAULT_WORKFLOW["name"],
        "steps": DEFAULT_WORKFLOW["steps"] + [{"id": "my_step", "prompt": "自定义"}],
        "project": {"title": "测试书", "target_chapters": 20},
    }
    assert refresh_builtin_workflow(modified, {"title": "测试书"}, 1) is None
