"""短剧 AI Worker 回归：用假 LLM 验证真实调用链、范围、去重和 JSON 解析。"""
from __future__ import annotations

import json

import pytest

from src.novel_writer.core.drama import DramaStore
from src.novel_writer.core.llm.base import LLMResponse

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeLLM:
    model = "fake-model"

    def __init__(self):
        self.calls: list[str] = []

    async def chat(self, messages, temperature=0.7, max_tokens=4096,
                   thinking="auto", tools=None, model=None):
        user = messages[-1].content
        self.calls.append(user)
        if "只输出 JSON" in user or "JSON 结构" in user:
            content = json.dumps({
                "shots": [
                    {"scene_id": "SC01", "shot_size": "特写", "camera": "推近",
                     "duration_sec": 2.5, "location": "山门", "time": "夜",
                     "characters": "林乐天", "action": "掌心渗血",
                     "dialogue": "林乐天：走。", "sfx": "风声",
                     "transition": "硬切", "status": "draft", "notes": ""},
                    {"scene_id": "SC02", "shot_size": "中景", "camera": "固定",
                     "duration_sec": 3, "location": "石阶", "time": "晨",
                     "characters": "苏月", "action": "拦住去路",
                     "dialogue": "苏月：等等。", "sfx": "", "transition": "硬切",
                     "status": "draft", "notes": ""},
                ]
            }, ensure_ascii=False)
        elif "改编圣经" in user:
            content = "# 改编圣经\n\n## 一句话卖点\n\n废柴逆袭，骨牌吞灵。\n"
        else:
            content = "# 第1集 测试\n\n## 第1场 山门 · 夜\n\n林乐天：走。\n"
        return LLMResponse(
            content=content, model="fake-model",
            usage={"total_tokens": 123}, finish_reason="stop",
        )


def _worker(tmp_path, task, scope="current", chapters=None, current_chapter=1, current_episode=1):
    from src.novel_writer.ui.drama.drama_worker import DramaAIWorker

    store = DramaStore(tmp_path)
    store.ensure({"title": "测试", "target_chapters": 30})
    llm = FakeLLM()
    worker = DramaAIWorker(
        llm=llm, store=store, project_info={"title": "测试"},
        task=task, scope=scope, chapters=chapters or [],
        current_chapter=current_chapter, current_episode=current_episode,
    )
    ok: list[str] = []
    failed: list[str] = []
    worker.finished_ok.connect(ok.append)
    worker.failed.connect(failed.append)
    return worker, store, llm, ok, failed


def test_worker_adapt_calls_model_and_writes_bible(qapp, tmp_path):
    planning = tmp_path / "planning"
    planning.mkdir()
    (planning / "立意.md").write_text("# 立意\n\n废柴逆袭\n", encoding="utf-8")
    worker, store, llm, ok, failed = _worker(tmp_path, "adapt")
    worker.run()
    assert ok and not failed
    assert len(llm.calls) == 1
    assert "废柴逆袭" in "".join(store.bible_text())


def test_worker_script_all_chapters_and_upsert_no_duplicates(qapp, tmp_path):
    chapters = [
        {"number": 1, "title": "第1章 骨牌", "text": "林乐天捡到骨牌。"},
        {"number": 2, "title": "第2章 苏月", "text": "苏月拦住他。"},
    ]
    worker, store, llm, ok, failed = _worker(
        tmp_path, "script", scope="all", chapters=chapters, current_chapter=1
    )
    worker.run()
    assert ok and not failed
    assert len(llm.calls) == 2
    episodes = store.load_episodes()
    assert len(episodes) == 2  # 默认模板集复用为第1集，再新增第2集
    assert {e["source_chapter"] for e in episodes} == {1, 2}

    worker2, _, _, ok2, failed2 = _worker(
        tmp_path, "script", scope="all", chapters=chapters, current_chapter=1
    )
    worker2.run()
    assert ok2 and not failed2
    assert len(store.load_episodes()) == 2  # 不重复堆集


def test_worker_storyboard_parses_json(qapp, tmp_path):
    _, store, _, _, _ = _worker(tmp_path, "storyboard")
    store.upsert_episode_for_chapter(
        1, "第1章", "# 第1集 测试\n\n## 第1场 山门 · 夜\n\n林乐天：走。\n"
    )
    worker, store, llm, ok, failed = _worker(
        tmp_path, "storyboard", scope="current", current_episode=1
    )
    worker.run()
    assert ok and not failed
    data = store.load_storyboard(1)
    assert len(data["shots"]) == 2
    assert data["shots"][0]["shot_id"] == "E01-S001"
    assert data["shots"][0]["shot_size"] == "特写"
    assert data["script_changed"] is False


def test_script_uses_saved_continuity_and_series_settings(qapp, tmp_path):
    worker, store, llm, ok, failed = _worker(
        tmp_path, "script", chapters=[{"number": 2, "title": "追踪", "text": "张三追上黑衣人。"}],
        current_chapter=2,
    )
    store.upsert_episode_for_chapter(1, "相遇", "# 第1集\n\n## 第1场 门口 · 夜\n\n张三：钥匙在我手里。")
    store.save_series({"episode_seconds": 180, "platform": "快手", "aspect": "16:9"})
    worker.run()
    assert ok and not failed
    prompt = llm.calls[0]
    assert "钥匙在我手里" in prompt
    assert "180 秒" in prompt and "16:9" in prompt and "快手" in prompt
    assert "短剧第2集" in prompt


def test_storyboard_rejects_empty_template(qapp, tmp_path):
    worker, store, llm, ok, failed = _worker(tmp_path, "storyboard")
    worker.run()
    assert failed and not llm.calls


def test_adaptive_worker_uses_planner_then_writer(qapp, tmp_path):
    worker, store, llm, ok, failed = _worker(
        tmp_path, "segment", chapters=[{"number": n, "title": f"章节{n}", "text": f"第{n}章事件"} for n in range(1, 6)]
    )
    class AdaptiveLLM(FakeLLM):
        async def chat(self, messages, temperature=0.7, max_tokens=4096, **kwargs):
            user = messages[-1].content
            self.calls.append(user)
            if "只输出 JSON" in user:
                return LLMResponse(content=json.dumps({"source_chapters": [1, 2, 3, 4],
                    "title": "追踪", "goal": "发现线索", "hook": "来人是谁", "beats": ["相遇", "追踪"],
                    "carry_reason": "第5章是新冲突"}))
            return LLMResponse(content="# 第1集 追踪\n\n## 第1场 山门 · 夜\n\n张三：走。")
    worker.llm = AdaptiveLLM()
    worker.run()
    assert ok and not failed
    assert len(worker.llm.calls) == 2
    assert store.load_episodes()[0]["source_chapters"] == [1, 2, 3, 4]
    assert store.adaptation_state()["pending_chapters"] == [5]
