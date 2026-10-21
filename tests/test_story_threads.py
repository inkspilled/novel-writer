"""故事线台账（时间线/主线/支线/伏笔）回归测试。

覆盖：章号解析 → 规划文档解析 → 台账落盘/手工编辑保护 →
记忆与世界状态对账 → 写作上下文反哺 → 工作流落盘后自动同步。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.novel_writer.core.memory import MemoryItem, MemoryScratchpad
from src.novel_writer.core.story_threads import (
    LOOP_OVERDUE_CHAPTERS, StoryLedger, ThreadKind, ThreadStatus,
    cn_to_int, detect_status, extract_chapters, infer_owner, parse_planning_doc,
    parse_volume_ranges, sync_story_threads, volume_ordinal,
)
from src.novel_writer.core.world_state import WorldState


def make_project(tmp_path: Path, chapters: int = 2, target: int = 100) -> Path:
    (tmp_path / "planning").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chapters").mkdir(parents=True, exist_ok=True)
    (tmp_path / "meta.json").write_text(
        json.dumps({"title": "测试书", "target_chapters": target}, ensure_ascii=False),
        encoding="utf-8",
    )
    for n in range(1, chapters + 1):
        (tmp_path / "chapters" / f"{n}_第{n}章.txt").write_text(
            f"# 第{n}章 测试\n\n正文内容。", encoding="utf-8")
    return tmp_path


def write_planning(project: Path, name: str, content: str):
    (project / "planning" / name).write_text(content, encoding="utf-8")


# ── 章号解析 ──

class TestChapterParsing:
    @pytest.mark.parametrize("text,expected", [
        ("12", 12), ("十二", 12), ("二十", 20), ("一百零三", 103), ("三十五", 35),
    ])
    def test_cn_to_int(self, text, expected):
        assert cn_to_int(text) == expected

    def test_extract_chapters_single_and_range(self):
        assert extract_chapters("第3章埋设，第12章回收") == [3, 12]
        assert extract_chapters("第十二章") == [12]
        assert extract_chapters("第120-125章") == [120, 125]
        assert extract_chapters("第120至125章") == [120, 125]
        assert extract_chapters("没有章号") == []

    def test_detect_status(self):
        assert detect_status("第5章已回收") == ThreadStatus.DONE
        assert detect_status("推进中") == ThreadStatus.ACTIVE
        assert detect_status("放弃") == ThreadStatus.ABANDONED
        assert detect_status("普通描述") is None


# ── 规划文档解析 ──

class TestParsePlanningDoc:
    def test_list_items_with_heading_groups(self):
        doc = """# 主线

## 第一卷：宗门风波

- 林牧拜入青云宗（第1-5章）
  - 与苏婉初遇
- 发现药圃异常（第6章，进行中）

## 第二卷：禁地迷局

- 禁地试炼揭露叛变（第30章）
"""
        items = parse_planning_doc(doc, "main")
        titles = [i["title"] for i in items]
        assert "与苏婉初遇" in titles
        # 父项因为带子列表，被提升为分组，子项归入该分组
        child = next(i for i in items if i["title"] == "与苏婉初遇")
        assert child["group"] == "林牧拜入青云宗（第1-5章）"
        # 同级兄弟不继承父项分组，用所属卷标题
        sibling = next(i for i in items if i["title"] == "发现药圃异常")
        assert sibling["group"] == "第一卷：宗门风波"

    def test_table_with_semantic_header(self):
        doc = """# 伏笔

| 伏笔 | 埋设 | 回收 | 状态 |
|---|---|---|---|
| 青霜剑剑灵 | 第2章 | 第20章 | 推进中 |
| 药圃灵脉 | 第3章 | 第18章 | 已回收 |
"""
        items = parse_planning_doc(doc, "foreshadow")
        assert [i["title"] for i in items] == ["青霜剑剑灵", "药圃灵脉"]
        assert items[0]["chapters"] == {"plant": 2, "resolve": 20}
        assert items[0]["status"] == ThreadStatus.ACTIVE.value
        assert items[1]["status"] == ThreadStatus.DONE.value

    def test_prose_paragraph_is_not_split_into_items(self):
        doc = "# 伏笔\n\n这是一段说明文字，不应该被当成伏笔条目。\n\n- 老人临别赠言\n"
        items = parse_planning_doc(doc, "foreshadow")
        assert [i["title"] for i in items] == ["老人临别赠言"]
        assert items[0]["group"] == ""  # 一级标题「伏笔」不当分组名

    def test_numbered_list_and_bold_lines(self):
        doc = """# 时间线

1. 宗门大比开幕（第10章）
2. **禁地开启**（第2章）
"""
        items = parse_planning_doc(doc, "timeline")
        assert {i["title"] for i in items} == {"宗门大比开幕", "禁地开启"}
        assert next(i for i in items if i["title"] == "禁地开启")["chapters"] == {"plant": 2}


# ── 台账读写与手工编辑 ──

class TestLedgerBasics:
    def test_add_update_roundtrip(self, tmp_path):
        project = make_project(tmp_path)
        ledger = StoryLedger(project).load()
        thread = ledger.add("main", "拜入宗门", detail="林牧入门", plant=1)
        ledger.update(thread.id, {"status": "done", "notes": "自己标的"})
        ledger.save()

        reloaded = StoryLedger(project).load()
        got = reloaded.get(thread.id)
        assert got is not None
        assert got.title == "拜入宗门"
        assert got.status == "done"
        assert got.notes == "自己标的"
        assert "status" in got.user_edited and "notes" in got.user_edited

    def test_progress_headline(self, tmp_path):
        project = make_project(tmp_path, chapters=3, target=30)
        write_planning(project, "主线.md", "# 主线\n- 拜入宗门（第1章）\n- 禁地试炼（第2章）\n")
        write_planning(project, "支线.md", "# 支线\n- 感情线（第1章）\n")
        write_planning(project, "伏笔.md", "# 伏笔\n- 剑灵（第1章埋设，第2章回收）\n")
        ledger = sync_story_threads(project)
        ledger.update(ledger.all("main")[0].id, {"status": "done"})
        progress = ledger.progress()
        assert progress.current_chapter == 3
        assert progress.total_chapters == 30
        assert progress.main_done == 1 and progress.main_total == 2
        assert progress.subplot_total == 1
        assert progress.foreshadow_done == 1
        assert "第 3 章 / 30" in progress.headline()


# ── 规划导入 ──

class TestPlanningImport:
    def test_import_and_idempotent(self, tmp_path):
        project = make_project(tmp_path)
        write_planning(project, "主线.md", "# 主线\n- 拜入宗门（第1章）\n- 禁地试炼（第2章，进行中）\n")
        ledger = sync_story_threads(project)
        assert len(ledger.all("main")) == 2
        assert ledger.all("main")[1].status == ThreadStatus.ACTIVE.value
        # 二次同步不重复导入
        again = sync_story_threads(project)
        assert len(again.all("main")) == 2

    def test_manual_edit_not_overwritten_by_resync(self, tmp_path):
        project = make_project(tmp_path)
        write_planning(project, "主线.md", "# 主线\n- 拜入宗门（第1章）\n")
        ledger = sync_story_threads(project)
        thread = ledger.all("main")[0]
        ledger.update(thread.id, {"status": "done", "notes": "作者手工完成"})
        ledger.save()

        again = sync_story_threads(project)
        got = again.get(thread.id)
        assert got.status == "done"
        assert got.notes == "作者手工完成"

    def test_removed_from_planning_is_soft_deleted(self, tmp_path):
        project = make_project(tmp_path)
        write_planning(project, "支线.md", "# 支线\n- 感情线（第1章）\n- 复仇线（第2章）\n")
        ledger = sync_story_threads(project)
        assert len(ledger.all("subplot")) == 2

        write_planning(project, "支线.md", "# 支线\n- 感情线（第1章）\n")
        again = sync_story_threads(project)
        assert len(again.all("subplot")) == 1
        # 软删除仍可恢复
        assert len(again.all("subplot", include_inactive=True)) == 2

    def test_planned_foreshadow_not_marked_recovered_early(self, tmp_path):
        project = make_project(tmp_path, chapters=2)
        write_planning(project, "伏笔.md", "# 伏笔\n- 剑灵（第1章埋设，第20章回收）\n")
        ledger = sync_story_threads(project)
        thread = ledger.all("foreshadow")[0]
        assert thread.status == ThreadStatus.ACTIVE.value
        assert thread.resolve_chapter == 20

        # 写到第 20 章后自动转为已回收
        (project / "chapters" / "20_第20章.txt").write_text("正文", encoding="utf-8")
        later = sync_story_threads(project)
        assert later.get(thread.id).status == ThreadStatus.DONE.value


# ── 记忆 / 世界状态对账 ──

class TestReconcile:
    def test_open_loops_and_subplots_from_memory(self, tmp_path):
        project = make_project(tmp_path)
        mem = MemoryScratchpad(project)
        mem.upsert(MemoryItem(category="open_loops", subject="第2章伏笔1·玉佩",
                              aspect="悬念", value="神秘玉佩突然发热",
                              source_chapter=2, payload={"urgency": 0.6}))
        mem.upsert(MemoryItem(category="subplots", subject="感情线", aspect="进度",
                              value="推进中：苏婉夜访", source_chapter=2,
                              payload={"status": "推进中"}))
        mem.save()

        ledger = sync_story_threads(project)
        loop = ledger.find_by_title("神秘玉佩突然发热", ThreadKind.FORESHADOW.value)
        assert loop is not None and loop.status == ThreadStatus.ACTIVE.value
        assert loop.planted_chapter == 2
        sub = ledger.find_by_title("感情线", ThreadKind.SUBPLOT.value)
        assert sub is not None and sub.status == ThreadStatus.ACTIVE.value

    def test_recovered_loop_closes_matching_thread(self, tmp_path):
        project = make_project(tmp_path)
        write_planning(project, "伏笔.md", "# 伏笔\n- 青霜剑剑灵（第1章埋设，第20章回收）\n")
        ledger = sync_story_threads(project)
        target = ledger.all("foreshadow")[0]

        mem = MemoryScratchpad(project)
        mem.close_loop("青霜剑剑灵", 2)
        mem.save()

        again = sync_story_threads(project)
        got = again.get(target.id)
        assert got.status == ThreadStatus.DONE.value
        assert got.resolve_chapter == 2

    def test_world_state_timeline_marks_happened(self, tmp_path):
        project = make_project(tmp_path)
        write_planning(project, "时间线.md", "# 时间线\n- 禁地开启（第10章）\n")
        gs = WorldState(project)
        gs.load()
        gs.add_timeline_event(2, "禁地开启，弟子入场", "禁地")
        gs.save()

        ledger = sync_story_threads(project)
        thread = ledger.all("timeline")[0]
        assert thread.happened is True
        # 实际发生章优先于规划章
        assert thread.planted_chapter == 2
        assert thread.location == "禁地"

    def test_overdue_foreshadow(self, tmp_path):
        project = make_project(tmp_path, chapters=30)
        write_planning(project, "伏笔.md", "# 伏笔\n- 陈年旧事（第1章埋设）\n")
        ledger = sync_story_threads(project)
        thread = ledger.all("foreshadow")[0]
        assert thread.is_overdue(LOOP_OVERDUE_CHAPTERS + 1)
        assert ledger.progress().foreshadow_overdue == 1


# ── 反哺写作上下文 ──

class TestContextBackfeed:
    def test_build_context_block_lists_open_threads(self, tmp_path):
        project = make_project(tmp_path, chapters=20)
        write_planning(project, "主线.md", "# 主线\n- 拜入宗门（第1章）\n")
        write_planning(project, "支线.md", "# 支线\n- 感情线（第2章）\n")
        write_planning(project, "伏笔.md", "# 伏笔\n- 陈年旧事（第1章埋设）\n")
        write_planning(project, "时间线.md", "# 时间线\n- 禁地开启（第2章）\n")
        ledger = sync_story_threads(project)
        block = ledger.build_context_block(chapter=20)
        assert "故事线台账" in block
        assert "待回收伏笔" in block
        assert "陈年旧事" in block
        assert "优先回收" in block  # 19 章未回收 → 超期
        assert "时间线·最近已发生" in block

    def test_context_builder_injects_ledger_for_chapter(self, tmp_path):
        from src.novel_writer.core.workflow.context import ContextBuilder

        project = make_project(tmp_path)
        write_planning(project, "主线.md", "# 主线\n- 拜入宗门（第1章，进行中）\n")
        sync_story_threads(project)

        builder = ContextBuilder(project, {"title": "测试书", "target_chapters": 100}, 48_000)
        text = builder.build([], 2, "chapter")
        assert "故事线台账" in text

    def test_context_builder_skips_ledger_for_global_steps(self, tmp_path):
        from src.novel_writer.core.workflow.context import ContextBuilder

        project = make_project(tmp_path)
        write_planning(project, "主线.md", "# 主线\n- 拜入宗门（第1章）\n")
        sync_story_threads(project)

        builder = ContextBuilder(project, {"title": "测试书"}, 48_000)
        text = builder.build([], 2, "toc")
        assert "故事线台账" not in text


# ── 工作流落盘后自动同步 ──

class TestWorkflowSync:
    def test_runner_sync_story_ledger_emits_event(self, tmp_path):
        from src.novel_writer.core.workflow.runner import WorkflowRunner

        project = make_project(tmp_path)
        write_planning(project, "主线.md", "# 主线\n- 拜入宗门（第1章）\n")
        events: list[tuple[str, dict]] = []
        runner = WorkflowRunner({}, project, {"title": "测试书", "target_chapters": 100})
        runner.on_event = lambda kind, payload: events.append((kind, payload))

        runner._sync_story_ledger(1)

        assert (project / "story_threads.json").exists()
        assert events and events[0][0] == "story_track"
        assert events[0][1]["main_total"] == 1

    def test_world_state_timeline_chapter_defaults_to_update_chapter(self, tmp_path):
        project = make_project(tmp_path)
        gs = WorldState(project)
        gs.load()
        gs.add_character("林牧", {"location": "药圃"})
        gs.apply_llm_update({"timeline": [{"event": "林牧发现药圃异常"}]}, chapter=3)
        assert gs.timeline[-1]["chapter"] == 3
        gs.save()


def test_manual_subplot_fields_not_overwritten_by_memory(tmp_path):
    """UI 手工改过支线状态/章号后，记忆对账不得改回去。"""
    project = make_project(tmp_path)
    write_planning(project, "支线.md", "# 支线\n- 感情线（第2章）\n")
    ledger = sync_story_threads(project)
    sub = ledger.all("subplot")[0]
    ledger.update(sub.id, {"chapters": {"plant": 1, "resolve": 30}})
    ledger.update(sub.id, {"status": "done"})
    ledger.save()

    mem = MemoryScratchpad(project)
    mem.upsert(MemoryItem(category="subplots", subject="感情线", aspect="进度",
                          value="推进中：第5章新进展", source_chapter=5,
                          payload={"status": "推进中"}))
    mem.save()

    again = sync_story_threads(project)
    got = again.get(sub.id)
    assert got.status == "done"
    assert got.planted_chapter == 1
    assert got.resolve_chapter == 30


# ── 卷 / 人物字段联动 ──

class TestVolumeAndOwner:
    def test_parse_volume_ranges(self):
        text = (
            "# 主线\n"
            "## 卷一 外门断事（1—50章）\n"
            "## 第二卷 江湖\n"
            "## 卷三 大道（第90-120章）\n"
        )
        assert parse_volume_ranges(text, 150) == [
            (1, "卷一 外门断事", 1, 50),
            (2, "第二卷 江湖", 51, 89),
            (3, "卷三 大道", 90, 120),
        ]

    def test_volume_ordinal(self):
        assert volume_ordinal("卷一 外门断事") == 1
        assert volume_ordinal("第二卷 江湖") == 2
        assert volume_ordinal("支线一：苏怀瑾") == 0

    def test_infer_owner_orders_by_occurrence(self):
        text = "顾言立下申辩状，赵九衡收进袖中"
        assert infer_owner(text, ["赵九衡", "顾言"]) == "顾言、赵九衡"
        assert infer_owner("没有角色", ["顾言"]) == ""

    def test_reconcile_fills_group_and_owner(self, tmp_path):
        project = make_project(tmp_path, chapters=3)
        write_planning(
            project, "主线.md",
            "# 主线\n## 卷一 外门断事（1—50章）\n- 拜入宗门（第1章）\n",
        )
        write_planning(project, "人物设定.md", "## 顾言\n- 主角\n## 赵九衡\n- 执事\n")
        gs = WorldState(project)
        gs.load()
        gs.add_timeline_event(3, "顾言立下申辩状，赵九衡收进袖中", "执事堂")
        gs.save()

        ledger = sync_story_threads(project)
        thread = next(t for t in ledger.all("timeline") if "申辩状" in t.title)
        assert thread.group.startswith("卷一")
        assert thread.owner == "顾言、赵九衡"
        assert ledger.volume_for_chapter(3).startswith("卷一")
        assert "〔顾言、赵九衡〕" in ledger.build_context_block(3)
