import asyncio
import json

import pytest

from src.novel_writer.core.drama import DramaStore
from src.novel_writer.core.drama_adaptation import AdaptiveDramaRunner, validate_plan


def chapters(count=9):
    return [{"number": n, "title": f"章节{n}", "text": f"素材{n}：线索{n}推动冲突。"} for n in range(1, count + 1)]


def plan(numbers):
    return {"source_chapters": numbers, "title": "失踪的信", "goal": "主角发现真相",
            "hook": "门外出现寄信人", "beats": ["收到信", "追查", "发现反转"], "carry_reason": "下一章开启新的冲突"}


SCRIPT = "# 第1集 失踪的信\n\n## 第1场 旧公寓 · 夜\n\n林晚：谁在外面？\n"


def setup(tmp_path):
    store = DramaStore(tmp_path)
    store.ensure()
    return store


def test_four_of_five_consumed_and_fifth_starts_next_window(tmp_path):
    store = setup(tmp_path)
    calls = []

    async def ask(stage, prompt):
        calls.append((stage, prompt))
        if stage == "plan":
            return json.dumps(plan([1, 2, 3, 4] if len(calls) == 1 else [5, 6, 7, 8, 9]))
        return SCRIPT

    result = asyncio.run(AdaptiveDramaRunner(store, ask).run(chapters()))
    assert "已生成 2 集" in result
    episodes = store.load_episodes()
    assert [ep["source_chapters"] for ep in episodes] == [[1, 2, 3, 4], [5, 6, 7, 8, 9]]
    assert "小说第5章" not in calls[1][1]  # 编剧不能看到待顺延章节
    assert "素材5" in calls[2][1] and "素材9" in calls[2][1]
    assert "林晚：谁在外面" in calls[2][1]
    assert store.adaptation_state()["pending_chapters"] == []
    assert "# 第2集" in store.load_script(2)


def test_next_episode_saves_pending_and_resumes_without_duplicates(tmp_path):
    store = setup(tmp_path)

    async def first(stage, prompt):
        return json.dumps(plan([1, 2, 3, 4])) if stage == "plan" else SCRIPT

    asyncio.run(AdaptiveDramaRunner(store, first).run(chapters(), one_episode=True))
    assert store.adaptation_state()["pending_chapters"] == [5, 6, 7, 8, 9]
    assert store.adaptation_state()["carry_reason"]

    async def second(stage, prompt):
        return json.dumps(plan([5, 6, 7, 8, 9])) if stage == "plan" else SCRIPT

    asyncio.run(AdaptiveDramaRunner(store, second).run(chapters()))
    async def must_not_call(*args):
        pytest.fail("已经改编的素材不应重复调用模型")
    asyncio.run(AdaptiveDramaRunner(store, must_not_call).run(chapters()))
    assert len(store.load_episodes()) == 2


def test_script_failure_does_not_consume_source(tmp_path):
    store = setup(tmp_path)

    async def ask(stage, prompt):
        return json.dumps(plan([1, 2])) if stage == "plan" else "模型忙，请稍后"

    with pytest.raises(ValueError, match="场次"):
        asyncio.run(AdaptiveDramaRunner(store, ask).run(chapters(3)))
    assert store.adaptation_state()["pending_chapters"] == [1, 2, 3]
    assert not store.load_episodes()[0].get("source_chapters")


@pytest.mark.parametrize("numbers", [[2, 3], [1, 3], [1, 1], [1, 2, 3, 4, 5, 6], [], [True]])
def test_plan_rejects_skipping_duplicate_or_nonexistent_sources(numbers):
    with pytest.raises(ValueError):
        validate_plan(plan(numbers), chapters(5))


def test_invalid_plan_repaired_once(tmp_path):
    store = setup(tmp_path)
    calls = []

    async def ask(stage, prompt):
        calls.append(stage)
        if stage == "plan":
            return json.dumps(plan([2] if len(calls) == 1 else [1, 2]))
        return SCRIPT

    asyncio.run(AdaptiveDramaRunner(store, ask).run(chapters(2)))
    assert calls == ["plan", "plan", "script"]


def test_insufficient_material_is_marked_waiting(tmp_path):
    store = setup(tmp_path)
    async def ask(stage, prompt):
        assert stage == "plan"
        return json.dumps({"wait_for_more": True, "carry_reason": "线索未兑现，等待下一章"})
    result = asyncio.run(AdaptiveDramaRunner(store, ask).run(chapters(1)))
    assert "等待下一章" in result
    assert store.adaptation_state()["pending_chapters"] == [1]
    assert not store.has_script(1)


def test_cancellation_between_planning_and_writing_preserves_pending(tmp_path):
    store = setup(tmp_path)
    stopped = []
    async def ask(stage, prompt):
        stopped.append(True)
        return json.dumps(plan([1]))
    asyncio.run(AdaptiveDramaRunner(store, ask, stopped=lambda: bool(stopped)).run(chapters(2)))
    assert store.adaptation_state()["pending_chapters"] == [1, 2]
    assert not store.has_script(1)


def test_changed_source_cannot_silently_mix_with_old_adaptation(tmp_path):
    store = setup(tmp_path)
    async def ask(stage, prompt):
        return json.dumps(plan([1])) if stage == "plan" else SCRIPT
    asyncio.run(AdaptiveDramaRunner(store, ask).run(chapters(2), one_episode=True))
    revised = chapters(2)
    revised[0]["text"] = "完全不同的新剧情"
    with pytest.raises(ValueError, match="已修改"):
        asyncio.run(AdaptiveDramaRunner(store, ask).run(revised))


def test_deleted_middle_episode_cannot_reorder_source_at_end(tmp_path):
    store = setup(tmp_path)
    count = []
    async def ask(stage, prompt):
        if stage == "plan":
            count.append(1)
            return json.dumps(plan([len(count)]))
        return SCRIPT
    asyncio.run(AdaptiveDramaRunner(store, ask).run(chapters(2)))
    store.delete_episode(1)
    with pytest.raises(ValueError, match="缺口"):
        asyncio.run(AdaptiveDramaRunner(store, ask).run(chapters(2)))


def test_metadata_failure_rolls_back_template_and_leaves_material_pending(tmp_path, monkeypatch):
    store = setup(tmp_path)
    previous = store.load_script(1)
    async def ask(stage, prompt):
        return json.dumps(plan([1])) if stage == "plan" else SCRIPT
    def fail(*args):
        raise OSError("磁盘写入失败")
    monkeypatch.setattr(store, "save_episodes", fail)
    with pytest.raises(OSError):
        asyncio.run(AdaptiveDramaRunner(store, ask).run(chapters(1)))
    assert store.load_script(1) == previous
    assert store.script_path(1).with_suffix(".pending.md").exists()
    assert store.adaptation_state()["pending_chapters"] == [1]
