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


def test_script_prompt_slices_long_chapter(qapp, tmp_path):
    """没有细纲/概要时也不能整章塞：原文只取对白/动作切片并标注省略。"""
    body = ("他站在山门前，风把衣角吹得猎猎作响。" * 60 + "\n\n") * 20
    body += "他拔刀，血溅三尺，枪口顶住眉心。"
    worker, store, llm, _, _ = _worker(
        tmp_path, "script", chapters=[{"number": 1, "title": "一", "text": body}]
    )
    prompt = worker._script_prompt({"number": 1, "title": "一", "text": body}, 1, "上集结尾" * 300)
    assert len(body) > 20000
    assert len(prompt) < len(body) / 2  # 明显压缩，不是整章直塞
    assert "（略）" in prompt and "对白/动作切片" in prompt
    assert "枪口顶住眉心" in prompt  # 冲突段保留
    assert "原文" in worker._context_note and "送入" in worker._context_note


def test_script_prompt_prefers_structured_brief(qapp, tmp_path):
    """有细纲/概要/digest 时先吃中间物，原文只用来核对台词与动作。"""
    planning = tmp_path / "planning"
    planning.mkdir()
    (planning / "章节细纲.md").write_text(
        "# 章节细纲\n\n## 第1章 退婚\n- 顾言被当众退婚\n- 他亮出族老令牌\n- 结尾：门外来人\n",
        encoding="utf-8",
    )
    (tmp_path / "chapters").mkdir()
    (tmp_path / "chapters" / "1_退婚.summary.md").write_text(
        "顾言被退婚，亮令牌反制，结尾神秘人登门。", encoding="utf-8"
    )
    (tmp_path / "digest").mkdir()
    import json as _json
    (tmp_path / "digest" / "1.json").write_text(
        _json.dumps({"summary": "顾言反制退婚", "percept": {"events": ["顾言亮出令牌"]},
                     "threads": {"main": [{"title": "夺回家产", "status": "active"}]}},
                    ensure_ascii=False),
        encoding="utf-8",
    )
    body = "他站在山门前。\n\n" + "“你配吗？”顾言冷笑。\n\n" * 400
    worker, store, llm, _, _ = _worker(
        tmp_path, "script", chapters=[{"number": 1, "title": "退婚", "text": body}]
    )
    prompt = worker._script_prompt({"number": 1, "title": "退婚", "text": body}, 1, "")
    assert "结构化简报" in prompt
    assert "亮出族老令牌" in prompt  # 细纲进了提示词
    assert "顾言亮出令牌" in prompt  # digest 事件也进了
    assert "夺回家产" in prompt  # 故事线推进
    assert "细纲/概要/推演/台账" in worker._context_note


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


def test_script_prompt_includes_rag_prior_without_spoilers(qapp, tmp_path):
    """跨章检索补前情/伏笔，但不能把本章之后的剧情漏进来。"""
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    (chapters / "1_令牌.txt").write_text(
        "# 第1章 令牌\n\n玄铁令在藏经阁的暗格里，无人知晓。\n\n顾言握紧令牌，指节发白。",
        encoding="utf-8",
    )
    (chapters / "2_苏月.txt").write_text(
        "# 第2章 苏月\n\n苏月推门进来，看见桌上的令牌，脸色变了。", encoding="utf-8"
    )
    (chapters / "4_终局.txt").write_text(
        "# 第4章 终局\n\n幕后黑手现身，苏月真实身份揭晓。", encoding="utf-8"
    )
    body = "顾言把令牌拍在桌上：“从今天起，我说了算。”"
    worker, store, _, _, _ = _worker(
        tmp_path, "script", chapters=[{"number": 3, "title": "摊牌", "text": body}]
    )
    prompt = worker._script_prompt({"number": 3, "title": "摊牌", "text": body}, 1, "")
    assert "相关前文" in prompt
    assert "玄铁令" in prompt  # 第1章的伏笔被召回
    assert "幕后黑手" not in prompt  # 第4章不能剧透
    assert "跨章检索" in worker._context_note
