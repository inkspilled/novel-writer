"""轻量 RAG 检索 — BM25 关键词检索，无需外部嵌入模型。

从历史章节中检索与当前写作任务最相关的段落，
替代「把所有前文塞进去」的方式。

原理：
1. 对历史章节按段落分块，建立倒排索引
2. 用 BM25 算法对查询进行相关性排序
3. 返回 top-k 最相关段落作为上下文
"""
from __future__ import annotations

import base64
import json
import math
import re
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)

# rag_index.json 的结构版本：不匹配时放弃恢复，走全量重建
RAG_INDEX_VERSION = 1
# rag_vectors.json（语义检索向量）的结构版本
RAG_VECTORS_VERSION = 1
# 单次 embedding API 调用的批量上限
_EMBED_BATCH = 64

try:  # 可选加速：无 numpy 时用纯 Python 余弦（大书检索会慢，功能不受影响）
    import numpy as _np
except ImportError:
    _np = None

# ── 语义检索配置（应用级，UI 设置后经 configure_embedding 注入） ──

_EMBED_CFG: dict | None = None


def configure_embedding(cfg: dict | None):
    """注入 embedding 配置（{model, api_key, base_url}）；None/无 model = 只用 BM25。

    在工作流启动前调用；已缓存的检索器会在下次检索时按新配置重建客户端。
    """
    global _EMBED_CFG
    _EMBED_CFG = dict(cfg) if cfg and cfg.get("model") else None


def _tokenize(text: str) -> list[str]:
    """中文分词：按字符 bigram + 标点切分。"""
    tokens = []
    # 去掉标点和空白
    cleaned = re.sub(r"[，。！？、；：\"'（）【】\s\n\r]+", " ", text)
    for word in cleaned.split():
        if len(word) <= 1:
            continue
        # 英文单词直接用
        if re.match(r"^[a-zA-Z0-9_]+$", word):
            tokens.append(word.lower())
        else:
            # 中文按 bigram
            for i in range(len(word) - 1):
                tokens.append(word[i:i+2])
            # 也加 unigram
            for ch in word:
                if '一' <= ch <= '鿿':
                    tokens.append(ch)
    return tokens


@dataclass
class Chunk:
    """文档块。"""
    id: str = ""
    chapter: int = 0
    text: str = ""
    tokens: list[str] = field(default_factory=list)


class BM25Index:
    """BM25 检索索引。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.chunks: list[Chunk] = []
        self.df: dict[str, int] = defaultdict(int)  # 文档频率
        self.avg_dl: float = 0.0
        self.N: int = 0
        self._built = False
        self._dirty_remove = False  # 有 chunk 被移除时，下次 build 需全量重算 df

    def add_chunk(self, chunk: Chunk):
        chunk.tokens = _tokenize(chunk.text)
        self.chunks.append(chunk)

    def invalidate(self):
        """标记索引需全量重建（移除 chunk 后调用）。"""
        self._dirty_remove = True
        self._built = False

    def build(self):
        """构建索引（增量：只对新增 chunk 累计 df；移除过 chunk 时全量重算）。"""
        if self._built and self.N == len(self.chunks) and not self._dirty_remove:
            return
        if self._dirty_remove:
            self.df = defaultdict(int)
            new_chunks = self.chunks
        else:
            new_chunks = self.chunks[self.N:]
        for chunk in new_chunks:
            seen = set()
            for token in chunk.tokens:
                if token not in seen:
                    self.df[token] += 1
                    seen.add(token)
        self.N = len(self.chunks)
        self.avg_dl = (sum(len(c.tokens) for c in self.chunks) / self.N) if self.N else 0.0
        self._built = True
        self._dirty_remove = False

    def search(self, query: str, top_k: int = 5) -> list[tuple[Chunk, float]]:
        """BM25 检索，返回 (chunk, score) 列表。"""
        if not self._built:
            self.build()
        if not self.chunks:
            return []

        query_tokens = _tokenize(query)
        scores = []

        for chunk in self.chunks:
            score = 0.0
            dl = len(chunk.tokens)
            tf_counter = Counter(chunk.tokens)

            for qt in query_tokens:
                if qt not in tf_counter:
                    continue
                tf = tf_counter[qt]
                df = self.df.get(qt, 0)
                idf = math.log((self.N - df + 0.5) / (df + 0.5) + 1)
                # avg_dl 为 0（空索引）时退化为标准 TF，避免除零
                denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avg_dl) if self.avg_dl else tf + self.k1
                tf_norm = (tf * (self.k1 + 1)) / denom
                score += idf * tf_norm

            if score > 0:
                scores.append((chunk, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


class RAGRetriever:
    """项目级 RAG 检索器。"""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.index = BM25Index()
        self._loaded = False
        self._chapter_mtimes: dict[int, float] = {}  # 已入索引章节的 mtime，用于增量/脏检测
        self._index_file = project_dir / "rag_index.json"
        self._load_index()
        # 语义检索：向量缓存（chunk_id → base64 float32 归一化向量）+ 懒建客户端
        self._vectors_file = project_dir / "rag_vectors.json"
        self._vectors: dict[str, str] = {}
        self._vec_model: str = ""
        self._vec_dim: int = 0
        self._embed_client = None
        self._embed_cfg_used: tuple = ()
        self._load_vectors()

    # ── 语义检索 ──

    def _load_vectors(self):
        """从 rag_vectors.json 恢复向量。模型不匹配时全部作废（换模型必须重嵌）。"""
        try:
            if not self._vectors_file.exists():
                return
            raw = json.loads(self._vectors_file.read_text(encoding="utf-8"))
            if raw.get("version") != RAG_VECTORS_VERSION:
                return
            if raw.get("model") != (_EMBED_CFG or {}).get("model", ""):
                return
            self._vectors = dict(raw.get("vectors", {}))
            self._vec_model = raw.get("model", "")
            self._vec_dim = int(raw.get("dim", 0))
        except (json.JSONDecodeError, OSError, ValueError, TypeError) as e:
            logger.debug("rag_vectors.json 恢复失败: %s", e)
            self._vectors = {}

    def _save_vectors(self):
        try:
            data = {
                "version": RAG_VECTORS_VERSION,
                "model": self._vec_model,
                "dim": self._vec_dim,
                "vectors": self._vectors,
            }
            from .project_io import write_json
            write_json(self._vectors_file, data, indent=0)
        except OSError as e:
            logger.debug("rag_vectors.json 写盘失败（不影响检索）: %s", e)

    def _get_embed_client(self):
        """按全局配置懒建/重建 embedding 客户端；未配置返回 None。"""
        cfg = _EMBED_CFG
        if not cfg:
            return None
        key = (cfg.get("model", ""), cfg.get("api_key", ""), cfg.get("base_url", ""))
        if self._embed_client is not None and self._embed_cfg_used == key:
            return self._embed_client
        try:
            from .embeddings import EmbeddingClient
            # 重建前关掉旧客户端，避免 httpx 连接泄漏
            if self._embed_client is not None:
                self._embed_client.close()
            self._embed_client = EmbeddingClient(key[0], key[1], key[2])
            self._embed_cfg_used = key
        except Exception as e:
            logger.warning("Embedding 客户端创建失败，回退 BM25: %s", e)
            self._embed_client = None
        if self._embed_client and self._vec_model and self._vec_model != cfg.get("model"):
            self._vectors = {}  # 换了模型，旧向量全部作废
            self._vec_model = cfg.get("model", "")
        return self._embed_client

    @staticmethod
    def _normalize(vec: list[float]) -> bytes:
        """归一化并编码为 base64 float32（JSON 里省 8 成以上体积）。"""
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        packed = struct.pack(f"{len(vec)}f", *(x / norm for x in vec))
        return base64.b64encode(packed).decode("ascii")

    @staticmethod
    def _top_k_by_cosine(query: list[float], vectors: dict[str, str], top_k: int):
        """余弦相似度 top-k（库内向量已归一化；query 也归一化后点积即余弦）。"""
        if _np is not None:
            q = _np.asarray(query, dtype=_np.float32)
            qnorm = float(_np.linalg.norm(q)) or 1.0
            q = q / qnorm
            ids = list(vectors.keys())
            mat = _np.stack([
                _np.frombuffer(base64.b64decode(vectors[i]), dtype=_np.float32) for i in ids
            ])
            scores = mat @ q
            order = _np.argsort(scores)[::-1][:top_k]
            return [(ids[int(k)], float(scores[int(k)])) for k in order]
        qnorm = math.sqrt(sum(x * x for x in query)) or 1.0
        scored = []
        for cid, b64 in vectors.items():
            vec = struct.unpack(f"{len(query)}f", base64.b64decode(b64))
            score = sum(a * b for a, b in zip(query, vec)) / qnorm
            scored.append((cid, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    @staticmethod
    def _vec_key(chunk: Chunk) -> str:
        """向量缓存键 = chunk id + 内容指纹。id 是 ch{n}_p{i}，润色后同段位 id 不变，
        不带指纹会错用改写前正文的旧向量。"""
        import hashlib
        fp = hashlib.md5(chunk.text.encode("utf-8")).hexdigest()[:8]
        return f"{chunk.id}:{fp}"

    def _ensure_vectors(self) -> bool:
        """为缺失向量的 chunk 批量补嵌，并清理陈旧键。失败返回 False（回退 BM25）。"""
        client = self._get_embed_client()
        if client is None:
            return False
        live_keys = {self._vec_key(c) for c in self.index.chunks if c.id}
        # 清理陈旧向量：章节润色重切后，旧 id:指纹 组合不再存在
        stale = set(self._vectors) - live_keys
        if stale:
            for k in stale:
                self._vectors.pop(k, None)
        missing = [
            c for c in self.index.chunks
            if c.id and self._vec_key(c) not in self._vectors
        ]
        if stale and not missing:
            self._save_vectors()
        if not missing:
            return True
        try:
            for start in range(0, len(missing), _EMBED_BATCH):
                batch = missing[start:start + _EMBED_BATCH]
                vecs = client.embed([c.text for c in batch])
                for chunk, vec in zip(batch, vecs):
                    self._vectors[self._vec_key(chunk)] = self._normalize(vec)
                logger.debug("语义索引补嵌 %d/%d chunks", min(start + _EMBED_BATCH, len(missing)), len(missing))
            self._vec_model = (_EMBED_CFG or {}).get("model", "")
            if self._vectors:
                sample = base64.b64decode(next(iter(self._vectors.values())))
                self._vec_dim = len(sample) // 4
            self._save_vectors()
            return True
        except Exception as e:
            logger.warning("向量补嵌失败，本次回退 BM25: %s", e)
            return False

    def _vector_search(self, query: str, top_k: int) -> list[tuple[Chunk, float]] | None:
        """语义检索；不可用/失败返回 None（调用方回退 BM25）。"""
        client = self._get_embed_client()
        if client is None or not self.index.chunks:
            return None
        if not self._ensure_vectors():
            return None
        try:
            qvec = client.embed([query])[0]
            hits = self._top_k_by_cosine(qvec, self._vectors, top_k)
        except Exception as e:
            logger.warning("语义检索失败，回退 BM25: %s", e)
            return None
        by_key = {self._vec_key(c): c for c in self.index.chunks if c.id}
        return [(by_key[k], score) for k, score in hits if k in by_key]

    # ── 索引持久化 ──

    def _load_index(self):
        """从 rag_index.json 恢复章节文本块与 mtime 指纹（冷启动免重分块）。

        恢复后 load_chapters 仍逐章比对 mtime：被编辑过的章节会移除旧块重新分块，
        因此持久化只加速启动，不引入陈旧数据。文件缺失/损坏/版本不符时静默放弃。
        """
        try:
            if not self._index_file.exists():
                return
            raw = json.loads(self._index_file.read_text(encoding="utf-8"))
            if raw.get("version") != RAG_INDEX_VERSION:
                return
            for ch in raw.get("chunks", []):
                text = ch.get("text", "")
                if not text:
                    continue
                self.index.add_chunk(Chunk(
                    id=ch.get("id", ""),
                    chapter=ch.get("chapter", 0),
                    text=text,
                ))
            self._chapter_mtimes = {
                int(k): float(v) for k, v in raw.get("chapter_mtimes", {}).items()
            }
            logger.debug("RAG 索引已恢复: %d chunks / %d 章", self.index.N, len(self._chapter_mtimes))
        except (json.JSONDecodeError, OSError, ValueError, TypeError) as e:
            logger.debug("rag_index.json 恢复失败，走全量重建: %s", e)
            self.index = BM25Index()
            self._chapter_mtimes = {}

    def _save_index(self):
        """把章节文本块 + mtime 指纹落盘到 rag_index.json。"""
        try:
            data = {
                "version": RAG_INDEX_VERSION,
                "chunks": [
                    {"id": c.id, "chapter": c.chapter, "text": c.text}
                    for c in self.index.chunks
                ],
                "chapter_mtimes": {str(k): v for k, v in self._chapter_mtimes.items()},
            }
            from .project_io import write_json
            write_json(self._index_file, data, indent=0)
        except OSError as e:
            logger.debug("rag_index.json 写盘失败（不影响检索）: %s", e)

    def load_chapters(self, up_to_chapter: int):
        """加载历史章节到索引（增量：只分块新增或被编辑过的章节）。"""
        chapters_dir = self.project_dir / "chapters"
        if not chapters_dir.exists():
            return
        dirty = False
        for ch_file in sorted(chapters_dir.glob("*.txt")):
            if ch_file.name.endswith(".outline.md") or ch_file.name.endswith(".bak.txt"):
                continue
            m = re.match(r"^(\d+)_", ch_file.name)
            if not m:
                continue
            ch_num = int(m.group(1))
            if ch_num >= up_to_chapter:
                continue
            try:
                mtime = ch_file.stat().st_mtime
            except OSError:
                continue
            if self._chapter_mtimes.get(ch_num) == mtime:
                continue  # 未变化，跳过分块
            content = ch_file.read_text(encoding="utf-8")
            # 移除该章旧 chunk（章节可能被润色改写过）
            before = len(self.index.chunks)
            self.index.chunks = [c for c in self.index.chunks if c.chapter != ch_num]
            if len(self.index.chunks) != before:
                self.index.invalidate()
            if content.strip():
                # 按段落分块
                paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
                for i, para in enumerate(paragraphs):
                    if len(para) < 20:
                        continue
                    self.index.add_chunk(Chunk(
                        id=f"ch{ch_num}_p{i}",
                        chapter=ch_num,
                        text=para,
                    ))
            self._chapter_mtimes[ch_num] = mtime
            dirty = True
        if dirty:
            self.index.build()
            self._save_index()
        self._loaded = True

    def load_planning(self):
        """加载规划文档到索引（按段落分块）。"""
        planning_dir = self.project_dir / "planning"
        if not planning_dir.exists():
            return
        for md_file in sorted(planning_dir.glob("*.md")):
            content = md_file.read_text(encoding="utf-8")
            if not content.strip():
                continue
            # 按段落或二级标题分块
            sections = re.split(r"\n(?=#)|\n\n+", content)
            for i, section in enumerate(sections):
                section = section.strip()
                if len(section) < 20:
                    continue
                self.index.add_chunk(Chunk(
                    id=f"plan_{md_file.stem}_s{i}",
                    chapter=0,
                    text=section,
                ))

    def retrieve(self, query: str, top_k: int = 5) -> str:
        """检索相关段落，返回格式化文本。

        配置了 embedding 时走语义向量检索（别名/近义可召回），
        未配置或失败时回退 BM25 关键词检索。
        """
        if not self.index._built:
            self.index.build()
        results = self._vector_search(query, top_k)
        if results is None:
            results = self.index.search(query, top_k)
        if not results:
            return ""
        parts = ["=== RAG 检索相关段落 ==="]
        for chunk, score in results:
            label = f"第{chunk.chapter}章" if chunk.chapter > 0 else "规划文档"
            parts.append(f"[{label} 相关度:{score:.1f}]\n{chunk.text[:300]}")
        return "\n\n".join(parts)


# ── 项目级检索器缓存：避免逐章写作时每次全量重建索引 ──

_RETRIEVER_CACHE: dict[str, RAGRetriever] = {}


def get_retriever(project_dir: Path) -> RAGRetriever:
    """取项目级缓存的检索器。load_chapters 按章节 mtime 增量加载，重复调用只分块新章节。"""
    key = str(project_dir)
    retriever = _RETRIEVER_CACHE.get(key)
    if retriever is None:
        retriever = RAGRetriever(project_dir)
        _RETRIEVER_CACHE[key] = retriever
    return retriever
