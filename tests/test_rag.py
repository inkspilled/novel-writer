"""RAG 检索回归测试：BM25 检索 + rag_index.json 索引持久化 + 语义（embedding）检索。"""
import base64
import json
import struct
from pathlib import Path

from src.novel_writer.core import rag as rag_mod
from src.novel_writer.core.rag import RAGRetriever, configure_embedding


def _write_chapter(project_dir: Path, n: int, title: str, body: str):
    (project_dir / "chapters").mkdir(exist_ok=True)
    (project_dir / "chapters" / f"{n}_{title}.txt").write_text(body, encoding="utf-8")


class FakeEmbedClient:
    """假 embedding 客户端：按关键词给 2 维向量，可计数、可注入故障。"""

    def __init__(self, model="fake-embed", fail=False):
        self.model = model
        self.fail = fail
        self.embedded_texts: list[str] = []

    def embed(self, texts):
        self.embedded_texts.extend(texts)
        if self.fail:
            raise RuntimeError("模拟 embedding 服务故障")
        out = []
        for text in texts:
            if "灵石" in text:
                out.append([1.0, 0.0])
            else:
                out.append([0.0, 1.0])
        return out


def _b64(vec):
    return base64.b64encode(struct.pack(f"{len(vec)}f", *vec)).decode("ascii")


class TestIndexPersistence:
    def test_index_saved_and_restored(self, tmp_path: Path):
        _write_chapter(tmp_path, 1, "甲", "# 甲\n玄铁令在藏经阁的暗格里，无人知晓。")
        r1 = RAGRetriever(tmp_path)
        r1.load_chapters(up_to_chapter=2)
        assert (tmp_path / "rag_index.json").exists()
        n_chunks = len(r1.index.chunks)
        assert n_chunks > 0

        # 模拟冷启动：新实例直接从磁盘恢复，不再重分块
        r2 = RAGRetriever(tmp_path)
        assert len(r2.index.chunks) == n_chunks
        assert r2._chapter_mtimes

    def test_search_works_after_restore(self, tmp_path: Path):
        _write_chapter(tmp_path, 1, "甲", "# 甲\n玄铁令在藏经阁的暗格里，无人知晓。")
        RAGRetriever(tmp_path).load_chapters(up_to_chapter=2)

        r2 = RAGRetriever(tmp_path)
        out = r2.retrieve("玄铁令 下落", top_k=3)
        assert "玄铁令" in out

    def test_edited_chapter_rechunked_after_restore(self, tmp_path: Path):
        import os
        import time
        _write_chapter(tmp_path, 1, "甲", "# 甲\n玄铁令在藏经阁的暗格里。")
        RAGRetriever(tmp_path).load_chapters(up_to_chapter=2)

        # 润色改写正文后，持久化索引必须按 mtime 失效重切。
        # 显式前移 mtime：两次写在同一时钟刻度内时 mtime 可能相同，会误判为未变化
        _write_chapter(tmp_path, 1, "甲", "# 甲\n改写后的全新剧情：主角夜袭敌营。")
        later = time.time() + 10
        os.utime(tmp_path / "chapters" / "1_甲.txt", (later, later))
        r2 = RAGRetriever(tmp_path)
        r2.load_chapters(up_to_chapter=2)
        text = "".join(c.text for c in r2.index.chunks)
        assert "夜袭敌营" in text
        assert "藏经阁" not in text

    def test_corrupt_index_falls_back_to_rebuild(self, tmp_path: Path):
        _write_chapter(tmp_path, 1, "甲", "# 甲\n玄铁令在藏经阁的暗格里，无人知晓它的下落。")
        (tmp_path / "rag_index.json").write_text("{broken json", encoding="utf-8")
        r = RAGRetriever(tmp_path)
        r.load_chapters(up_to_chapter=2)
        assert r.index.chunks  # 静默放弃恢复，全量重建成功

    def test_version_mismatch_falls_back_to_rebuild(self, tmp_path: Path):
        import json
        _write_chapter(tmp_path, 1, "甲", "# 甲\n玄铁令在藏经阁的暗格里，无人知晓它的下落。")
        (tmp_path / "rag_index.json").write_text(
            json.dumps({"version": 999, "chunks": [
                {"id": "x", "chapter": 1, "text": "过期结构的块"}]},
                ensure_ascii=False),
            encoding="utf-8")
        r = RAGRetriever(tmp_path)
        r.load_chapters(up_to_chapter=2)
        text = "".join(c.text for c in r.index.chunks)
        assert "玄铁令" in text


# ── 语义（embedding）检索 ──

class TestVectorSearch:
    def setup_method(self):
        configure_embedding(None)  # 隔离全局配置

    def teardown_method(self):
        configure_embedding(None)

    def test_semantic_search_ranks_by_vector(self, tmp_path):
        _write_chapter(tmp_path, 1, "甲", "# 甲\n他在坊市买了一块灵石，灵石温润有光。")
        _write_chapter(tmp_path, 2, "乙", "# 乙\n玄铁令在藏经阁的暗格里，无人知晓它的下落。")
        configure_embedding({"model": "fake-embed", "api_key": "k", "base_url": "http://x"})
        r = RAGRetriever(tmp_path)
        r._embed_client = FakeEmbedClient()
        r._embed_cfg_used = ("fake-embed", "k", "http://x")
        r.load_chapters(up_to_chapter=3)

        out = r.retrieve("灵石 的 下落", top_k=2)
        assert "灵石" in out
        # 语义排序应把向量最接近查询的 chunk 排最前
        assert out.index("第1章") < out.index("第2章")

    def test_no_config_falls_back_to_bm25(self, tmp_path):
        _write_chapter(tmp_path, 1, "甲", "# 甲\n玄铁令在藏经阁的暗格里，无人知晓它的下落。")
        r = RAGRetriever(tmp_path)  # configure_embedding(None)
        r.load_chapters(up_to_chapter=2)
        assert r._vector_search("玄铁令", 3) is None
        assert "玄铁令" in r.retrieve("玄铁令 下落", top_k=3)  # BM25 兜底

    def test_embed_failure_falls_back_to_bm25(self, tmp_path):
        _write_chapter(tmp_path, 1, "甲", "# 甲\n玄铁令在藏经阁的暗格里，无人知晓它的下落。")
        configure_embedding({"model": "fake-embed"})
        r = RAGRetriever(tmp_path)
        r._embed_client = FakeEmbedClient(fail=True)
        r._embed_cfg_used = ("fake-embed", "", "")
        r.load_chapters(up_to_chapter=2)
        out = r.retrieve("玄铁令 下落", top_k=3)
        assert "玄铁令" in out  # 服务故障 → BM25 结果

    def test_vectors_persist_and_not_reembedded(self, tmp_path):
        _write_chapter(tmp_path, 1, "甲", "# 甲\n玄铁令在藏经阁的暗格里，无人知晓它的下落。")
        configure_embedding({"model": "fake-embed"})
        fake = FakeEmbedClient()
        r1 = RAGRetriever(tmp_path)
        r1._embed_client = fake
        r1._embed_cfg_used = ("fake-embed", "", "")
        r1.load_chapters(up_to_chapter=2)
        r1.retrieve("玄铁令", top_k=2)
        assert (tmp_path / "rag_vectors.json").exists()
        chunk_count = len(fake.embedded_texts)

        # 冷启动：新检索器从盘上恢复向量，只需嵌查询本身
        r2 = RAGRetriever(tmp_path)
        fake2 = FakeEmbedClient()
        r2._embed_client = fake2
        r2._embed_cfg_used = ("fake-embed", "", "")
        r2.load_chapters(up_to_chapter=2)
        assert r2._vectors  # 已恢复
        r2.retrieve("玄铁令", top_k=2)
        assert len(fake2.embedded_texts) == 1  # 只有查询，chunk 不重嵌
        assert chunk_count >= 1

    def test_model_change_invalidates_vectors(self, tmp_path):
        _write_chapter(tmp_path, 1, "甲", "# 甲\n玄铁令在藏经阁的暗格里，无人知晓它的下落。")
        configure_embedding({"model": "fake-embed"})
        r1 = RAGRetriever(tmp_path)
        r1._embed_client = FakeEmbedClient()
        r1._embed_cfg_used = ("fake-embed", "", "")
        r1.load_chapters(up_to_chapter=2)
        r1.retrieve("玄铁令", top_k=2)
        assert r1._vectors

        configure_embedding({"model": "another-model"})
        r2 = RAGRetriever(tmp_path)
        r2._embed_client = FakeEmbedClient(model="another-model")
        r2._embed_cfg_used = ("another-model", "", "")
        r2.load_chapters(up_to_chapter=2)
        assert r2._vectors == {}  # 旧模型向量作废，将重新嵌入

    def test_stale_vectors_pruned_on_save(self, tmp_path):
        # 两段正文 → ch1_p0 / ch1_p1 两个 chunk；润色后只剩一段 → ch1_p1 成陈旧向量
        _write_chapter(tmp_path, 1, "甲",
                       "# 甲\n玄铁令在藏经阁的暗格里，无人知晓。\n\n它的下落成为贯穿全书最大的谜团之一，无人能解。")
        configure_embedding({"model": "fake-embed"})
        r = RAGRetriever(tmp_path)
        r._embed_client = FakeEmbedClient()
        r._embed_cfg_used = ("fake-embed", "", "")
        r.load_chapters(up_to_chapter=2)
        r.retrieve("玄铁令", top_k=2)
        assert any(k.startswith("ch1_p1") for k in r._vectors)

        # 润色后段落变少 → 多出的 chunk id 成陈旧向量，保存时清理
        _write_chapter(tmp_path, 1, "甲", "# 甲\n全新的剧情：主角夜袭敌营，烧了粮仓。")
        r.load_chapters(up_to_chapter=2)
        r.retrieve("夜袭", top_k=2)
        live_ids = {c.id for c in r.index.chunks}
        assert {k.split(":")[0] for k in r._vectors} <= live_ids
        assert not any(k.startswith("ch1_p1") for k in r._vectors)
