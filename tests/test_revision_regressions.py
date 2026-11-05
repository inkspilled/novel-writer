"""检索、上下文和短剧生产流程的边界回归。"""
import hashlib

import pytest

from src.novel_writer.core import project_io
from src.novel_writer.core.drama import DramaStore, normalize_shot
from src.novel_writer.core.rag import Chunk, RAGRetriever
from src.novel_writer.core.workflow.digest import load_digest, needs_digest, save_digest


def test_summary_window_uses_chapter_order(tmp_path):
    for n in (1, 2, 9, 10, 11):
        project_io.write_md(tmp_path / "chapters" / f"{n}_标题.summary.md", f"概要{n}结束")
    text = project_io.load_chapter_summaries(tmp_path, window=3)
    assert "概要2结束" not in text
    assert text.index("概要9结束") < text.index("概要10结束") < text.index("概要11结束")


def test_equal_length_revision_invalidates_digest_and_consumers(tmp_path):
    path = tmp_path / "chapters" / "1_初章.txt"
    project_io.write_md(path, "张三拿到了钥匙")
    data = {"summary": "旧概要", "world": {}, "percept": {"events": []},
            "memory": {"open_loops": []}, "_content_hash": hashlib.sha256("张三拿到了钥匙".encode()).hexdigest()}
    save_digest(tmp_path, 1, data)
    assert not needs_digest(tmp_path, 1, "张三拿到了钥匙")
    project_io.write_md(path, "李四拿到了钥匙")
    assert needs_digest(tmp_path, 1, "李四拿到了钥匙")
    assert load_digest(tmp_path, 1) == {}


def test_deleted_chapter_is_removed_from_rag_and_disk_cache(tmp_path):
    path = tmp_path / "chapters" / "1_秘密.txt"
    project_io.write_md(path, "玄铁令藏在山门后的暗格里。")
    retriever = RAGRetriever(tmp_path)
    retriever.load_chapters(10)
    assert "玄铁令" in retriever.retrieve("玄铁令")
    path.unlink()
    retriever.load_chapters(10)
    assert retriever.retrieve("玄铁令") == ""
    assert not RAGRetriever(tmp_path).index.chunks


def test_zero_history_limit_cannot_leak_cached_future(tmp_path):
    project_io.write_md(tmp_path / "chapters" / "1_秘密.txt", "玄铁令藏在山门后的暗格里。")
    retriever = RAGRetriever(tmp_path)
    retriever.load_chapters(10)
    retriever.load_chapters(0)
    assert retriever.retrieve("玄铁令") == ""


def test_hybrid_recall_promotes_agreement_over_vector_only(tmp_path, monkeypatch):
    retriever = RAGRetriever(tmp_path)
    a, b = Chunk(id="a", text="无关"), Chunk(id="b", text="人物确切名字")
    monkeypatch.setattr(retriever, "_vector_search", lambda *args: [(a, .99), (b, .8)])
    monkeypatch.setattr(retriever.index, "search", lambda *args, **kwargs: [(b, 100)])
    assert retriever._recall("名字", 10, None)[0][0].id == "b"


def test_drama_episode_number_agrees_with_saved_episode(tmp_path):
    store = DramaStore(tmp_path)
    store.ensure()
    target = store.target_episode_number(8, "相遇")
    ep = store.upsert_episode_for_chapter(8, "相遇", "# 第1集\n\n## 第1场 山门 · 夜\n\n张三：走。")
    assert target == ep["number"] == 1


def test_drama_ensure_does_not_reset_after_first_episode_deleted(tmp_path):
    store = DramaStore(tmp_path)
    store.ensure()
    store.add_episode("第二集")
    store.delete_episode(1)
    store.ensure()
    assert [e["number"] for e in store.load_episodes()] == [2]
    store.delete_episode(2)
    store.ensure()
    assert store.load_episodes() == []
    assert store.add_episode()["number"] == 1


def test_drama_backup_export_and_duration_report(tmp_path):
    store = DramaStore(tmp_path)
    store.ensure()
    assert not store.has_script(1)
    store.save_script(1, "# 第1集\n\n## 第1场 山门 · 夜\n\n张三：走。")
    store.save_script(1, "# 第1集\n\n## 第1场 山门 · 夜\n\n张三：停。")
    assert "张三：走。" in store.script_path(1).with_suffix(".bak.md").read_text(encoding="utf-8")
    assert "张三：停。" in store.export_scripts().read_text(encoding="utf-8")
    store.save_storyboard(1, {"shots": [{"duration_sec": 3}]}, update_source=True)
    report = store.episode_report(1)
    assert report["seconds"] == 3
    assert any("目标" in issue for issue in report["issues"])


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_shot_duration_cannot_poison_totals(value):
    assert normalize_shot({"duration_sec": value})["duration_sec"] == 0
