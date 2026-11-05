"""轻量 RAG 检索 — BM25 关键词检索 + 可选语义召回 + 可选重排序。

从历史章节中检索与当前写作任务最相关的段落，
替代「把所有前文塞进去」的方式。

原理：
1. 按「叙事单元」（场景）分块：场景整块入索引，超长场景才在内部按自然段切，
   并保留 10%–20% 重叠，避免情节被拦腰截断；
2. 每块附带元数据：章节 / 场景序号 / 场景类型 / 出场人物 / 时间线；
3. 查询先召回 Top 20–50 候选（语义优先 + BM25 补充），
   有需要时按元数据过滤，再用可插拔的重排序模型（Qwen3-Reranker / BGE 等）精排，最后取 top-k。
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
from typing import Callable

from .logger import get_logger
from .rag_chunking import chunk_chapter

logger = get_logger(__name__)

# rag_index.json 的结构版本：不匹配时放弃恢复，走全量重建
# v2：chunk 增加 scene / scene_type / characters / timeline 元数据
RAG_INDEX_VERSION = 3
# rag_vectors.json（语义检索向量）的结构版本
RAG_VECTORS_VERSION = 1
# 单次 embedding API 调用的批量上限
_EMBED_BATCH = 64

# 进入重排序前的候选数（召回少而准，重排序才有意义）
RAG_CANDIDATE_K = 30
# 重排序候选上限，控制 reranker 延迟
RAG_MAX_CANDIDATES = 50
# 单个块注入上下文的字符上限（场景块可能较长，留足上下文）
RAG_TEXT_CAP = 1200

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


def semantic_available() -> bool:
    """是否配置了 embedding（语义检索可用）；调用方据此决定要不要走向量召回。"""
    return bool(_EMBED_CFG and _EMBED_CFG.get("model"))


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
    """文档块（一个叙事场景，或超长场景切出的窗口）。"""
    id: str = ""
    chapter: int = 0
    text: str = ""
    tokens: list[str] = field(default_factory=list)
    scene: int = 0
    scene_type: str = "日常"
    characters: list[str] = field(default_factory=list)
    timeline: str = ""


# ── 查询侧元数据：从自然语言里识别「第N章」「对话/战斗」等意图 ──

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CHAPTER_QUERY_RE = re.compile(r"第\s*([0-9零〇一二两三四五六七八九十百千]+)\s*章")
_SCENE_TYPE_QUERY_WORDS = {
    "战斗": ("战斗", "打斗", "交手", "厮杀", "对决", "开打", "武斗", "斗法"),
    "对话": ("对话", "谈话", "交谈", "谈判", "争吵", "对质", "聊天", "对峙"),
    "回忆": ("回忆", "往事", "当年", "闪回", "追忆", "回想"),
    "日常": ("日常", "生活", "吃饭", "休息", "闲逛"),
}


def _cn_to_int(text: str) -> int:
    """把「十二」「一百零三」或阿拉伯数字转成 int（识别不了返回 0）。"""
    text = (text or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    section = 0
    number = 0
    for char in text:
        if char in _CN_DIGITS:
            number = _CN_DIGITS[char]
        elif char == "十":
            section += (number or 1) * 10
            number = 0
        elif char == "百":
            section += (number or 1) * 100
            number = 0
        elif char == "千":
            section += (number or 1) * 1000
            number = 0
        else:
            return 0
    return section + number


def parse_query_filters(query: str) -> tuple[int | None, str | None]:
    """从查询里识别章节号与场景类型（用于软过滤/加权，不强行截断召回）。"""
    chapter = None
    match = _CHAPTER_QUERY_RE.search(query or "")
    if match:
        chapter = _cn_to_int(match.group(1)) or None
    scene_type = None
    for candidate, words in _SCENE_TYPE_QUERY_WORDS.items():
        if any(word in (query or "") for word in words):
            scene_type = candidate
            break
    return chapter, scene_type


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

    def search(
        self,
        query: str,
        top_k: int = 5,
        chunk_filter: Callable[[Chunk], bool] | None = None,
    ) -> list[tuple[Chunk, float]]:
        """BM25 检索，返回 (chunk, score) 列表；chunk_filter 用于元数据预过滤。"""
        if not self._built:
            self.build()
        if not self.chunks:
            return []

        query_tokens = _tokenize(query)
        scores = []

        for chunk in self.chunks:
            if chunk_filter is not None and not chunk_filter(chunk):
                continue
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
        self._chapter_mtimes: dict[int, tuple[int, int]] = {}  # (mtime_ns, size)，用于增量/脏检测
        self._index_file = project_dir / "rag_index.json"
        # 元数据抽取用的已知角色名（world_state.json，按 mtime 刷新）
        self._known_names: list[str] | None = None
        self._known_names_mtime: int = -1
        self._known_names_manual = False
        self._index_known_names: list[str] = []
        # 检索章节上界：写作上下文只应看到「前 n-2 章」，即使索引里已有更后面的章
        self._chapter_limit: int | None = None
        self._load_index()
        # 语义检索：向量缓存（chunk_id → base64 float32 归一化向量）+ 懒建客户端
        self._vectors_file = project_dir / "rag_vectors.json"
        self._vectors: dict[str, str] = {}
        self._vec_model: str = ""
        self._vec_dim: int = 0
        self._embed_client = None
        self._embed_cfg_used: tuple = ()
        self._load_vectors()
        # 重排序客户端（配置注入后懒建）
        self._rerank_client = None
        self._rerank_cfg_used: tuple = ()

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

    def _get_rerank_client(self):
        """按全局配置懒建/重建重排序客户端；未配置返回 None。"""
        from . import rerank as rerank_mod

        cfg = rerank_mod.rerank_config()
        if not cfg:
            if self._rerank_client is not None:
                close = getattr(self._rerank_client, "close", None)
                if callable(close):
                    close()
                self._rerank_client = None
                self._rerank_cfg_used = ()
            return None
        key = (cfg.get("model", ""), cfg.get("api_key", ""), cfg.get("base_url", ""))
        if self._rerank_client is not None and self._rerank_cfg_used == key:
            return self._rerank_client
        try:
            if self._rerank_client is not None:
                close = getattr(self._rerank_client, "close", None)
                if callable(close):
                    close()
            self._rerank_client = rerank_mod.RerankClient(key[0], key[1], key[2])
            self._rerank_cfg_used = key
        except Exception as e:
            logger.warning("Rerank 客户端创建失败，跳过重排序: %s", e)
            self._rerank_client = None
        return self._rerank_client

    def set_known_characters(self, names: list[str]) -> None:
        """显式指定角色名表（测试 / Agent 已知人物时使用）。"""
        self._known_names = sorted({str(n).strip() for n in names if str(n).strip()})
        self._known_names_manual = True

    def _load_known_characters(self) -> list[str]:
        """从 world_state.json 读角色名；文件变化时刷新，供块元数据抽取。"""
        if self._known_names_manual:
            return self._known_names or []
        path = self.project_dir / "world_state.json"
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            self._known_names = []
            self._known_names_mtime = -1
            return self._known_names
        if self._known_names is not None and mtime == self._known_names_mtime:
            return self._known_names
        names: set[str] = set()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                names.update(str(k) for k in (data.get("characters") or {}))
        except (json.JSONDecodeError, OSError, ValueError, TypeError) as e:
            logger.debug("world_state.json 读取失败，块元数据不含人物: %s", e)
        self._known_names = sorted(n for n in names if n)
        self._known_names_mtime = mtime
        return self._known_names

    @staticmethod
    def _match_chunk(
        chunk: Chunk,
        chapter: int | None = None,
        scene_type: str | None = None,
    ) -> bool:
        if chapter is not None and chunk.chapter != chapter:
            return False
        if scene_type and chunk.scene_type != scene_type:
            return False
        return True

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

    def _vector_search(
        self,
        query: str,
        top_k: int,
        chunk_filter: Callable[[Chunk], bool] | None = None,
    ) -> list[tuple[Chunk, float]] | None:
        """语义检索；不可用/失败返回 None（调用方回退 BM25）。"""
        client = self._get_embed_client()
        if client is None or not self.index.chunks:
            return None
        if not self._ensure_vectors():
            return None
        vectors = self._vectors
        if chunk_filter is not None:
            allowed = {
                self._vec_key(c) for c in self.index.chunks
                if c.id and chunk_filter(c)
            }
            vectors = {k: v for k, v in vectors.items() if k in allowed}
            if not vectors:
                return None
        try:
            qvec = client.embed([query])[0]
            hits = self._top_k_by_cosine(qvec, vectors, top_k)
        except Exception as e:
            logger.warning("语义检索失败，回退 BM25: %s", e)
            return None
        by_key = {self._vec_key(c): c for c in self.index.chunks if c.id}
        return [(by_key[k], score) for k, score in hits if k in by_key]

    # ── 索引持久化 ──

    def _load_index(self):
        """从 rag_index.json 恢复章节文本块、元数据与 mtime 指纹（冷启动免重分块）。

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
                characters = ch.get("characters") or []
                if not isinstance(characters, list):
                    characters = []
                self.index.add_chunk(Chunk(
                    id=ch.get("id", ""),
                    chapter=ch.get("chapter", 0),
                    text=text,
                    scene=int(ch.get("scene", 0) or 0),
                    scene_type=str(ch.get("scene_type", "日常") or "日常"),
                    characters=[str(name) for name in characters],
                    timeline=str(ch.get("timeline", "") or ""),
                ))
            self._chapter_mtimes = self._parse_fingerprints(raw.get("chapter_mtimes"))
            self._index_known_names = [str(n) for n in (raw.get("known_characters") or [])]
            logger.debug("RAG 索引已恢复: %d chunks / %d 章", self.index.N, len(self._chapter_mtimes))
        except (json.JSONDecodeError, OSError, ValueError, TypeError) as e:
            logger.debug("rag_index.json 恢复失败，走全量重建: %s", e)
            self.index = BM25Index()
            self._chapter_mtimes = {}
            self._index_known_names = []

    @staticmethod
    def _parse_fingerprints(raw) -> dict[int, tuple[int, int]]:
        """解析 chapter_mtimes；兼容旧格式。

        旧版存 float mtime，同秒内等长改写会漏检；遇到旧值直接丢弃指纹，
        让 load_chapters 重分块并写入新格式 ``[mtime_ns, size]``。
        """
        out: dict[int, tuple[int, int]] = {}
        if not isinstance(raw, dict):
            return out
        for key, value in raw.items():
            try:
                out[int(key)] = (int(value[0]), int(value[1]))
            except (TypeError, ValueError, IndexError, KeyError):
                continue
        return out

    def _save_index(self):
        """把章节文本块 + 元数据 + mtime 指纹落盘到 rag_index.json。"""
        try:
            data = {
                "version": RAG_INDEX_VERSION,
                "chunks": [
                    {
                        "id": c.id,
                        "chapter": c.chapter,
                        "text": c.text,
                        "scene": c.scene,
                        "scene_type": c.scene_type,
                        "characters": c.characters,
                        "timeline": c.timeline,
                    }
                    for c in self.index.chunks
                ],
                "known_characters": self._index_known_names,
                "chapter_mtimes": {
                    str(k): [v[0], v[1]] for k, v in self._chapter_mtimes.items()
                },
            }
            from .project_io import write_json
            write_json(self._index_file, data, indent=0)
        except OSError as e:
            logger.debug("rag_index.json 写盘失败（不影响检索）: %s", e)

    def load_chapters(self, up_to_chapter: int):
        """加载历史章节到索引（增量：只分块新增或被编辑过的章节）。

        ``up_to_chapter`` 同时作为**检索上界**：写作上下文传 n-2 时，即使索引里
        已经缓存了更后面的章节，检索也不会把「未来剧情」注入当前章。
        """
        self._chapter_limit = up_to_chapter if up_to_chapter < 100_000_000 else None
        chapters_dir = self.project_dir / "chapters"
        present = {
            int(m.group(1)) for path in chapters_dir.glob("*.txt")
            if (m := re.match(r"^(\d+)_.+(?<!\.bak)\.txt$", path.name))
        }
        removed = set(self._chapter_mtimes) - present
        if removed:
            self.index.chunks = [c for c in self.index.chunks if c.chapter not in removed]
            for number in removed:
                self._chapter_mtimes.pop(number, None)
            self.index.invalidate()
        if not chapters_dir.exists():
            if removed:
                self.index.build()
                self._save_index()
            return
        known_names = self._load_known_characters()
        dirty = bool(removed)
        if known_names != self._index_known_names:
            # 角色名表变化：只重算元数据里的 characters，不重新分块/重嵌
            from .rag_chunking import extract_characters
            for chunk in self.index.chunks:
                chunk.characters = extract_characters(chunk.text, known_names)
            self._index_known_names = list(known_names)
            dirty = True
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
                st = ch_file.stat()
            except OSError:
                continue
            fingerprint = (st.st_mtime_ns, st.st_size)
            if self._chapter_mtimes.get(ch_num) == fingerprint:
                continue  # 未变化，跳过分块
            content = ch_file.read_text(encoding="utf-8")
            # 移除该章旧 chunk（章节可能被润色改写过）
            before = len(self.index.chunks)
            self.index.chunks = [c for c in self.index.chunks if c.chapter != ch_num]
            if len(self.index.chunks) != before:
                self.index.invalidate()
            for i, draft in enumerate(chunk_chapter(
                content,
                chapter=ch_num,
                known_names=known_names,
            )):
                self.index.add_chunk(Chunk(
                    id=f"ch{ch_num}_p{i}",
                    chapter=ch_num,
                    text=draft.text,
                    scene=draft.scene,
                    scene_type=draft.scene_type,
                    characters=draft.characters,
                    timeline=draft.timeline,
                ))
            self._chapter_mtimes[ch_num] = fingerprint
            dirty = True
        if dirty:
            self.index.build()
            self._save_index()
        self._loaded = True

    # ── 检索：召回 →（元数据过滤）→ 重排序 → top-k ──

    def _recall(self, query: str, candidate_k: int,
                chunk_filter: Callable[[Chunk], bool] | None,
                use_semantic: bool = True) -> list[tuple[Chunk, float]]:
        """候选召回：语义与 BM25 按 RRF 名次融合，未配置语义时使用 BM25。"""
        vector_hits = self._vector_search(query, candidate_k, chunk_filter) if use_semantic else None
        lexical_hits = self.index.search(query, candidate_k, chunk_filter=chunk_filter)
        if not vector_hits:
            return lexical_hits[:RAG_MAX_CANDIDATES]
        # RRF 按名次融合，避免余弦与 BM25 分数量纲不同，也避免向量结果独占前排。
        chunks: dict[str, Chunk] = {}
        scores: dict[str, float] = defaultdict(float)
        for hits in (vector_hits, lexical_hits):
            for rank, (chunk, _) in enumerate(hits, start=1):
                chunks[chunk.id] = chunk
                scores[chunk.id] += 1.0 / (60 + rank)
        ordered = sorted(scores, key=lambda key: scores[key], reverse=True)
        return [(chunks[key], scores[key]) for key in ordered[:RAG_MAX_CANDIDATES]]

    @staticmethod
    def _soft_order(
        results: list[tuple[Chunk, float]],
        chapter: int | None,
        scene_type: str | None,
    ) -> list[tuple[Chunk, float]]:
        """查询里识别出的章节/场景意图只做软排序，不强行过滤（避免误伤召回）。"""
        def matched(item: tuple[Chunk, float]) -> int:
            chunk = item[0]
            if chapter is not None and chunk.chapter != chapter:
                return 1
            if scene_type and chunk.scene_type != scene_type:
                return 1
            return 0
        return sorted(results, key=matched)

    def _rerank_results(
        self,
        query: str,
        results: list[tuple[Chunk, float]],
    ) -> list[tuple[Chunk, float]]:
        """用重排序模型重排候选；未配置或失败时原样返回召回排序。"""
        client = self._get_rerank_client()
        if client is None or len(results) < 2:
            return results
        try:
            scored = client.rerank(
                query,
                [chunk.text for chunk, _ in results],
                top_n=len(results),
            )
        except Exception as e:
            logger.warning("重排序失败，保留召回排序: %s", e)
            return results
        if not scored:
            return results
        reordered: list[tuple[Chunk, float]] = []
        used: set[str] = set()
        for index, score in scored:
            if not 0 <= index < len(results):
                continue
            chunk = results[index][0]
            if chunk.id in used:
                continue
            used.add(chunk.id)
            reordered.append((chunk, score))
        # 重排模型只返回 top_n 时，补齐未返回的候选，避免结果变少
        for chunk, score in results:
            if chunk.id not in used:
                reordered.append((chunk, score))
        return reordered

    @staticmethod
    def _format_score(score: float) -> str:
        return f"{score:.3f}" if abs(score) <= 2 else f"{score:.1f}"

    def _format_result(self, chunk: Chunk, score: float, text_cap: int) -> str:
        label = f"第{chunk.chapter}章" if chunk.chapter > 0 else "规划文档"
        meta = [label]
        if chunk.chapter > 0:
            if chunk.timeline:
                meta.append(chunk.timeline.split("·", 1)[-1])
            if chunk.scene_type:
                meta.append(chunk.scene_type)
            if chunk.characters:
                meta.append("人物：" + "、".join(chunk.characters[:4]))
        meta.append(f"相关度:{self._format_score(score)}")
        text = chunk.text
        if text_cap and len(text) > text_cap:
            text = text[:text_cap] + "…"
        return f"[{'｜'.join(meta)}]\n{text}"

    def search_chunks(
        self,
        query: str,
        top_k: int = 5,
        chapter: int | None = None,
        scene_type: str | None = None,
        candidate_k: int | None = None,
        use_semantic: bool = True,
    ) -> list[tuple[Chunk, float]]:
        """检索并返回「块 + 分数」，供上层自行去重/拼装（``retrieve`` 只是它的格式化版）。

        流程：召回 Top 20–50 候选 → 元数据过滤 → 重排序 → 取 top_k。
        - ``chapter`` / ``scene_type`` 显式传入时按元数据硬过滤（无结果自动回退）；
        - 查询里出现的「第N章」「战斗/对话/回忆」只做软排序，避免误伤召回；
        - 配置了 embedding 时与 BM25 按 RRF 融合；未配置或失败时纯 BM25；
        - ``use_semantic=False`` 时跳过语义召回（一次调用不能阻塞在 embedding 冷启动上）；
        - ``self._chapter_limit``（``load_chapters(up_to_chapter=N)`` 设置）限制只检索前 N 章。
        """
        if not self.index._built:
            self.index.build()
        auto_chapter, auto_scene = parse_query_filters(query)
        hard_filter = chapter is not None or scene_type is not None
        limit = self._chapter_limit

        def in_scope(chunk: Chunk) -> bool:
            return limit is None or chunk.chapter <= 0 or chunk.chapter < limit

        if hard_filter:
            chunk_filter: Callable[[Chunk], bool] | None = (
                lambda c: in_scope(c) and self._match_chunk(c, chapter, scene_type)
            )
        elif limit is not None:
            chunk_filter = in_scope
        else:
            chunk_filter = None
        k = int(candidate_k or max(RAG_CANDIDATE_K, top_k * 6))
        k = max(top_k, min(k, RAG_MAX_CANDIDATES))

        results = self._recall(query, k, chunk_filter, use_semantic=use_semantic)
        if not results and hard_filter:
            logger.debug("元数据过滤后无候选（chapter=%s scene=%s），回退章节范围内召回",
                         chapter, scene_type)
            results = self._recall(
                query, k, in_scope if limit is not None else None,
                use_semantic=use_semantic,
            )
        if not results:
            return []
        results = self._rerank_results(query, results)
        if not results:
            return []
        # 查询解析出的章节/场景意图在重排后再稳定分区一次：
        # reranker 只看正文、看不到块元数据，不补这一步会把「第2章」的意图丢掉。
        if not hard_filter and (auto_chapter is not None or auto_scene):
            results = self._soft_order(results, auto_chapter, auto_scene)
        return results[:top_k]

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        chapter: int | None = None,
        scene_type: str | None = None,
        candidate_k: int | None = None,
        text_cap: int = RAG_TEXT_CAP,
        use_semantic: bool = True,
    ) -> str:
        """检索相关叙事块，返回格式化文本（参数与流程见 ``search_chunks``）。"""
        results = self.search_chunks(
            query, top_k=top_k, chapter=chapter, scene_type=scene_type,
            candidate_k=candidate_k, use_semantic=use_semantic,
        )
        if not results:
            return ""
        parts = ["=== RAG 检索相关段落 ==="]
        for chunk, score in results[:top_k]:
            parts.append(self._format_result(chunk, score, text_cap))
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
