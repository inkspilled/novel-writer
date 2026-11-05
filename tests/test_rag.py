"""RAG 检索回归测试：场景分块 + BM25/语义检索 + 元数据过滤 + 重排序 + 索引持久化。"""
import base64
import json
import struct
from pathlib import Path

from src.novel_writer.core.rag import RAGRetriever, configure_embedding, parse_query_filters
from src.novel_writer.core.rag_chunking import chunk_chapter, detect_scene_type
from src.novel_writer.core.rerank import configure_rerank


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


# ── 叙事单元分块 + 元数据 ──

class TestSceneChunking:
    def test_short_paragraphs_merge_into_one_scene(self):
        text = "凌尘握紧剑柄，灵力缓缓涌入剑身。\n\n「你终于来了。」苏晴的声音很冷。\n\n远处传来一声钟响。"
        drafts = chunk_chapter(text, chapter=1, known_names=["凌尘", "苏晴"])
        assert len(drafts) == 1
        assert drafts[0].scene == 0
        assert drafts[0].timeline == "第1章·场景1"
        assert "凌尘" in drafts[0].characters
        assert "苏晴" in drafts[0].characters

    def test_separator_and_time_jump_split_scenes(self):
        text = ("第一场戏，两人在殿前对峙，谁也不肯先开口。\n\n***\n\n"
                "次日清晨，山门前落满了雪，远处传来钟声。")
        drafts = chunk_chapter(text, chapter=2)
        assert len(drafts) == 2
        assert [d.scene for d in drafts] == [0, 1]
        assert drafts[0].timeline == "第2章·场景1"
        assert drafts[1].timeline == "第2章·场景2"

    def test_overlong_scene_splits_with_tail_overlap(self):
        paragraphs = [
            f"甲乙丙丁戊{i}。己庚辛壬癸{i}。子丑寅卯辰{i}。" for i in range(5)
        ]
        drafts = chunk_chapter(
            "\n\n".join(paragraphs), chapter=1, max_tokens=100, overlap_ratio=0.15)
        assert len(drafts) >= 2
        # 上一窗口的最后一句应作为下一窗口的开头重叠，防止情节断在边界
        tail_sentence = paragraphs[2].split("。")[-2] + "。"
        assert tail_sentence in drafts[0].text
        assert tail_sentence in drafts[1].text

    def test_short_stage_direction_belongs_to_next_scene(self):
        text = ("赵无极在书房里翻开一整摞档案，眉头越皱越紧。\n\n内门后山。\n\n"
                "陆清玄站在石阶下，抬头看着那扇没有灯光的窗。")
        drafts = chunk_chapter(text, chapter=5)
        assert len(drafts) == 2
        assert "内门后山" in drafts[1].text
        assert "赵无极" not in drafts[1].text

    def test_scene_type_detection(self):
        assert detect_scene_type("他拔剑斩出，剑气轰然炸开，鲜血飞溅。") == "战斗"
        assert detect_scene_type("“你来了。”他低声说。“嗯。”她答。") == "对话"
        assert detect_scene_type("“你的剑呢？”他问。“丢了。”她答。") == "对话"
        assert detect_scene_type("他想起当年在故乡的往事，记忆里的童年。") == "回忆"
        assert detect_scene_type("他坐在窗边喝茶，看着院子里的花。") == "日常"

    def test_metadata_survives_index_restore(self, tmp_path: Path):
        _write_chapter(
            tmp_path, 1, "甲",
            "凌尘拔剑斩出，剑气轰然炸开，鲜血飞溅，敌人应声倒地。\n\n***\n\n"
            "「你终于来了。」苏晴冷冷地说，凌尘沉默不语，殿内只剩下呼吸声。")
        r1 = RAGRetriever(tmp_path)
        r1.set_known_characters(["凌尘", "苏晴"])
        r1.load_chapters(up_to_chapter=2)
        before = [
            (c.scene, c.scene_type, c.characters, c.timeline)
            for c in r1.index.chunks
        ]
        r2 = RAGRetriever(tmp_path)
        after = [
            (c.scene, c.scene_type, c.characters, c.timeline)
            for c in r2.index.chunks
        ]
        assert before == after
        assert {item[1] for item in after} == {"战斗", "对话"}


# ── 元数据过滤 + 重排序 ──

class FakeRerankClient:
    """假重排客户端：把含「包子」的文档排第一，可注入故障。"""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def rerank(self, query, documents, top_n=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("模拟 rerank 服务故障")
        order = sorted(
            range(len(documents)),
            key=lambda i: (0 if "包子" in documents[i] else 1, i),
        )
        return [(i, float(len(documents) - rank)) for rank, i in enumerate(order)]


def _multi_scene_project(tmp_path: Path):
    _write_chapter(
        tmp_path, 1, "甲",
        "凌尘拔剑斩出，剑气轰然炸开，鲜血飞溅。敌人应声倒地。\n\n***\n\n"
        "「你终于来了。」苏晴冷冷地说。「我等你很久了。」凌尘答道。")
    _write_chapter(tmp_path, 2, "乙", "凌尘在集市上买了三个包子，慢慢走回客栈。")
    _write_chapter(tmp_path, 3, "丙", "凌尘在藏经阁发现玄铁令，心中一惊，决定先回房歇息。")


class TestMetadataRetrieval:
    def setup_method(self):
        configure_embedding(None)
        configure_rerank(None)

    def teardown_method(self):
        configure_embedding(None)
        configure_rerank(None)

    def test_hard_scene_type_filter(self, tmp_path):
        _multi_scene_project(tmp_path)
        r = RAGRetriever(tmp_path)
        r.load_chapters(up_to_chapter=10)
        out = r.retrieve("凌尘", top_k=5, scene_type="战斗")
        assert "鲜血" in out
        assert "包子" not in out
        assert "｜战斗｜" in out

    def test_hard_chapter_filter(self, tmp_path):
        _multi_scene_project(tmp_path)
        r = RAGRetriever(tmp_path)
        r.load_chapters(up_to_chapter=10)
        out = r.retrieve("凌尘", top_k=5, chapter=2)
        assert "包子" in out
        assert "鲜血" not in out
        assert "第2章" in out

    def test_hard_filter_falls_back_when_no_match(self, tmp_path):
        _multi_scene_project(tmp_path)
        r = RAGRetriever(tmp_path)
        r.load_chapters(up_to_chapter=10)
        out = r.retrieve("凌尘", chapter=99)
        assert "凌尘" in out  # 过滤后无结果 → 自动回退全量召回

    def test_query_chapter_and_scene_are_soft_filters(self, tmp_path):
        _multi_scene_project(tmp_path)
        r = RAGRetriever(tmp_path)
        r.load_chapters(up_to_chapter=10)
        out = r.retrieve("凌尘 第2章", top_k=5)
        assert out.index("第2章") < out.index("第1章")

    def test_chapter_limit_excludes_future_chapters(self, tmp_path):
        _multi_scene_project(tmp_path)
        r = RAGRetriever(tmp_path)
        r.load_chapters(up_to_chapter=10)          # 索引全部
        assert "包子" in r.retrieve("包子")
        r.load_chapters(up_to_chapter=2)           # 写作第2章时只应看到第1章
        assert r.retrieve("包子") == ""
        r.load_chapters(up_to_chapter=10 ** 9)     # 工具检索不受限
        assert "包子" in r.retrieve("包子")

    def test_parse_query_filters(self):
        assert parse_query_filters("第三章里主角和反派的对话") == (3, "对话")
        assert parse_query_filters("第12章 战斗场面") == (12, "战斗")
        assert parse_query_filters("随便找找") == (None, None)


class TestRerank:
    def setup_method(self):
        configure_embedding(None)
        configure_rerank(None)

    def teardown_method(self):
        configure_embedding(None)
        configure_rerank(None)

    def test_rerank_reorders_candidates(self, tmp_path):
        _multi_scene_project(tmp_path)
        configure_rerank({"model": "fake-rerank"})
        r = RAGRetriever(tmp_path)
        fake = FakeRerankClient()
        r._rerank_client = fake
        r._rerank_cfg_used = ("fake-rerank", "", "")
        r.load_chapters(up_to_chapter=10)

        out = r.retrieve("凌尘", top_k=4)
        assert fake.calls == 1
        assert out.index("包子") < out.index("鲜血")
        assert out.index("包子") < out.index("玄铁令")

    def test_rerank_failure_falls_back_to_recall_order(self, tmp_path):
        _multi_scene_project(tmp_path)
        configure_rerank({"model": "fake-rerank"})
        r = RAGRetriever(tmp_path)
        r._rerank_client = FakeRerankClient(fail=True)
        r._rerank_cfg_used = ("fake-rerank", "", "")
        r.load_chapters(up_to_chapter=10)
        out = r.retrieve("凌尘", top_k=3)
        assert "凌尘" in out

    def test_query_chapter_intent_survives_rerank(self, tmp_path):
        _multi_scene_project(tmp_path)
        configure_rerank({"model": "fake-rerank"})

        class PreferOtherChapter:
            def rerank(self, query, documents, top_n=None):
                order = sorted(
                    range(len(documents)),
                    key=lambda i: (0 if "玄铁令" in documents[i] else 1, i),
                )
                return [(i, float(len(documents) - rank)) for rank, i in enumerate(order)]

        r = RAGRetriever(tmp_path)
        r._rerank_client = PreferOtherChapter()
        r._rerank_cfg_used = ("fake-rerank", "", "")
        r.load_chapters(up_to_chapter=10)
        # reranker 偏向第3章，但查询里的「第2章」元数据意图应稳定排在前面
        out = r.retrieve("凌尘 第2章", top_k=4)
        assert out.index("包子") < out.index("玄铁令")


class TestVectorSearch:
    def setup_method(self):
        configure_embedding(None)  # 隔离全局配置
        configure_rerank(None)

    def teardown_method(self):
        configure_embedding(None)
        configure_rerank(None)

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
        # 两场景 → ch1_p0 / ch1_p1 两个 chunk；润色后只剩一场 → ch1_p1 成陈旧向量
        _write_chapter(tmp_path, 1, "甲",
                       "# 甲\n玄铁令在藏经阁的暗格里，无人知晓。\n\n***\n\n它的下落成为贯穿全书最大的谜团之一，无人能解。")
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


class TestToolFastPath:
    """Agent 工具检索走 use_semantic=False（e2f2184 的取舍：不赌 embedding 冷启动）。"""

    def test_use_semantic_false_skips_embedding(self, tmp_path):
        _write_chapter(tmp_path, 1, "甲", "# 甲\n玄铁令在藏经阁的暗格里，无人知晓。")
        configure_embedding({"model": "fake-embed"})
        configure_rerank(None)
        r = RAGRetriever(tmp_path)
        fake = FakeEmbedClient()
        r._embed_client = fake
        r._embed_cfg_used = ("fake-embed", "", "")
        r.load_chapters(up_to_chapter=2)
        fake.embedded_texts.clear()

        out = r.retrieve("玄铁令", top_k=2, use_semantic=False)
        assert "玄铁令" in out  # BM25 仍然召回
        assert fake.embedded_texts == []  # 连查询向量都没算
        assert r._vectors == {}  # 也没为 chunk 建向量

    def test_default_still_embeds(self, tmp_path):
        _write_chapter(tmp_path, 1, "甲", "# 甲\n玄铁令在藏经阁的暗格里，无人知晓。")
        configure_embedding({"model": "fake-embed"})
        configure_rerank(None)
        r = RAGRetriever(tmp_path)
        fake = FakeEmbedClient()
        r._embed_client = fake
        r._embed_cfg_used = ("fake-embed", "", "")
        r.load_chapters(up_to_chapter=2)
        fake.embedded_texts.clear()

        r.retrieve("玄铁令", top_k=2)
        assert fake.embedded_texts  # 默认路径仍走语义召回

