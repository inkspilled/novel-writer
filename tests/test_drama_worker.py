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


@pytest.fixture(autouse=True)
def _offline_embedding():
    """Worker 用例不联网：语义检索自动回退 BM25（真实 embedding 另跑集成脚本）。"""
    from src.novel_writer.core import rag

    rag.configure_embedding(None)
    yield
    rag.configure_embedding(None)


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


class _EmptyThenTextLLM(FakeLLM):
    """第一次只回 reasoning（content 空），关思考重试后才给正文。"""

    def __init__(self):
        super().__init__()
        self.thinking_modes: list[str] = []

    async def chat(self, messages, temperature=0.7, max_tokens=4096,
                   thinking="auto", tools=None, model=None):
        self.calls.append(messages[-1].content)
        self.thinking_modes.append(thinking)
        if len(self.calls) == 1:
            return LLMResponse(content="", model="fake-model",
                               usage={"total_tokens": 40}, finish_reason="length")
        return LLMResponse(content="# 改编圣经\n\n## 一句话卖点\n\n废柴逆袭。",
                           model="fake-model",
                           usage={"total_tokens": 60}, finish_reason="stop")


class _AlwaysEmptyLLM(FakeLLM):
    async def chat(self, messages, temperature=0.7, max_tokens=4096,
                   thinking="auto", tools=None, model=None):
        self.calls.append(messages[-1].content)
        return LLMResponse(content="", model="fake-model",
                           usage={"total_tokens": 7}, finish_reason="length")


def test_empty_content_retries_without_thinking(qapp, tmp_path):
    """思考吃满预算导致 content 为空时，自动关思考重试一次，而不是直接失败。"""
    worker, store, _, ok, failed = _worker(tmp_path, "adapt")
    worker.llm = _EmptyThenTextLLM()
    worker.run()
    assert ok and not failed
    assert worker.llm.thinking_modes[:2] == ["off", "off"]  # adapt 本身已关思考
    assert "废柴逆袭" in "".join(store.bible_text())


def test_empty_content_twice_reports_actionable_error(qapp, tmp_path):
    """两次都空：错误信息要说清 finish_reason 和下一步，而不是笼统的「没有内容」。"""
    worker, store, _, ok, failed = _worker(tmp_path, "adapt")
    worker.llm = _AlwaysEmptyLLM()
    worker.run()
    assert failed and not ok
    assert "没有返回正文" in failed[0]
    assert "finish=length" in failed[0]


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


class _FullFlowLLM(FakeLLM):
    """全流程四阶段的假模型：按 prompt 开头特征分流，避免节选内容误命中。"""

    async def chat(self, messages, temperature=0.7, max_tokens=4096, **kwargs):
        user = messages[-1].content
        self.calls.append(user)
        if "分集策划" in user:  # plan：以「你是短剧分集策划」开头
            return LLMResponse(content=json.dumps({
                "source_chapters": [1], "title": "骨牌", "goal": "夺牌",
                "hook": "来人是谁", "beats": ["相遇"],
                "carry_reason": "素材已用完"}, ensure_ascii=False),
                model="fake-model", usage={"total_tokens": 110}, finish_reason="stop")
        if "剧本拆成" in user:  # 分镜：把第N集剧本拆成 20-40 个镜头
            return LLMResponse(content=json.dumps({"shots": [
                {"scene_id": "SC01", "shot_size": "特写", "camera": "推近",
                 "duration_sec": 2.5, "location": "山门", "time": "夜",
                 "characters": "林乐天", "action": "掌心渗血",
                 "dialogue": "林乐天：走。", "sfx": "", "transition": "硬切",
                 "status": "draft", "notes": ""}]}, ensure_ascii=False),
                model="fake-model", usage={"total_tokens": 120}, finish_reason="stop")
        if "短剧改编圣经" in user:  # adapt：请为下面的小说做一份短剧改编圣经
            return LLMResponse(content="# 改编圣经\n\n## 节奏规则\n\n前 3 秒必须出冲突。\n",
                               model="fake-model", usage={"total_tokens": 100},
                               finish_reason="stop")
        return LLMResponse(content="# 第1集 骨牌\n\n## 第1场 山门 · 夜\n\n林乐天：走。\n",
                           model="fake-model", usage={"total_tokens": 130},
                           finish_reason="stop")


def test_worker_full_pipeline_end_to_end(qapp, tmp_path):
    """全流程：圣经 → 智能分集(规划+剧本) → 分镜 → 资产，一次到底。"""
    chapters = [
        {"number": 1, "title": "第1章 骨牌", "text": "林乐天捡到骨牌。"},
    ]
    worker, store, llm, ok, failed = _worker(
        tmp_path, "full", scope="all", chapters=chapters, current_chapter=1
    )
    full_llm = _FullFlowLLM()
    worker.llm = full_llm
    usages: list[dict] = []
    worker.usage_updated.connect(usages.append)
    worker.run()
    assert ok and not failed, failed
    # 调用顺序：圣经 → 分集规划 → 剧本 → 分镜，共 4 次模型调用
    assert len(full_llm.calls) == 4
    assert "短剧改编圣经" in full_llm.calls[0]
    assert "分集策划" in full_llm.calls[1]
    assert store.load_episodes()[0]["source_chapters"] == [1]
    assert len(store.load_storyboard(1)["shots"]) == 1
    assert any(a["name"] == "林乐天" for a in store.load_assets())  # 资产已提取
    assert usages and usages[-1]["total_tokens"] == 460  # usage 逐次累计


def test_worker_full_skips_existing_bible(qapp, tmp_path):
    """全流程不覆盖已有圣经：预置圣经后不再发起改编调用。"""
    store = DramaStore(tmp_path)
    store.ensure({"title": "测试", "target_chapters": 30})
    store.save_bible("# 已有圣经\n\n节奏规则：前 3 秒冲突。\n")
    worker, _, llm, ok, failed = _worker(
        tmp_path, "full", scope="all",
        chapters=[{"number": 1, "title": "第1章", "text": "林乐天捡到骨牌。"}],
    )
    worker.store = store
    full_llm = _FullFlowLLM()
    worker.llm = full_llm
    worker.run()
    assert ok and not failed, failed
    assert all("短剧改编圣经" not in c for c in full_llm.calls)  # 没有改编调用


def test_worker_assets_extracts_locally_without_llm(qapp, tmp_path):
    """资产任务是本地规则提取：零模型调用，仍走统一的 worker 通道。"""
    store = DramaStore(tmp_path)
    store.ensure({"title": "测试", "target_chapters": 30})
    store.upsert_episode_for_chapter(
        1, "第1章", "# 第1集 测试\n\n## 第1场 山门 · 夜\n\n林乐天：走。\n"
    )
    store.save_storyboard(1, {"shots": [{
        "shot_id": "E01-S001", "scene_id": "SC01", "shot_size": "特写",
        "camera": "固定", "duration_sec": 2.5, "location": "山门", "time": "夜",
        "characters": "林乐天、苏月", "action": "掌心渗血", "dialogue": "",
        "sfx": "", "transition": "硬切", "status": "draft", "notes": "",
    }]}, update_source=True)
    worker, store, llm, ok, failed = _worker(tmp_path, "assets")
    worker.run()
    assert ok and not failed
    assert llm.calls == []  # 本地规则，不调模型
    names = {a["name"] for a in store.load_assets()}
    assert {"林乐天", "苏月", "山门"} <= names
    # 再跑一次不堆重复（提取按 kind+name 去重）
    worker2, _, _, ok2, _ = _worker(tmp_path, "assets")
    worker2.run()
    assert ok2
    assert len(store.load_assets()) == 3


def test_adapt_prompt_slices_planning_docs(qapp, tmp_path):
    """改编圣经任务不能把四份规划文档全文塞进去。"""
    planning = tmp_path / "planning"
    planning.mkdir()
    for name in ("立意.md", "大纲.md", "人物设定.md", "世界观.md"):
        (planning / name).write_text(
            f"# {name}\n\n## 节奏规则\n" + "铺陈设定。" * 800, encoding="utf-8"
        )
    worker, store, _, _, _ = _worker(tmp_path, "adapt")
    prompt = worker._adapt_prompt()
    assert "按预算切片" in prompt
    assert len(prompt) < 12000  # 四份各 3200+ 字，必须裁
    assert "切片" in worker._context_note


def test_adapt_prompt_includes_rolling_summary(qapp, tmp_path):
    """改编圣经要用「已写了什么」的滚动摘要，而不是只看规划稿。"""
    (tmp_path / "summary").mkdir()
    (tmp_path / "summary" / "第1-10章摘要.md").write_text(
        "# 剧情摘要\n顾言被退婚，三个月后夺回家产。", encoding="utf-8"
    )
    worker, store, _, _, _ = _worker(tmp_path, "adapt")
    prompt = worker._adapt_prompt()
    assert "已完成章节滚动摘要" in prompt
    assert "夺回家产" in prompt
