"""轻量 RAG 检索 — BM25 关键词检索，无需外部嵌入模型。

从历史章节中检索与当前写作任务最相关的段落，
替代「把所有前文塞进去」的方式。

原理：
1. 对历史章节按段落分块，建立倒排索引
2. 用 BM25 算法对查询进行相关性排序
3. 返回 top-k 最相关段落作为上下文
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path


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
                tf_norm = (tf * (self.k1 + 1)) / (tf + self.k1 * (1 - self.b + self.b * dl / self.avg_dl))
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

    def load_chapters(self, up_to_chapter: int):
        """加载历史章节到索引（增量：只分块新增或被编辑过的章节）。"""
        chapters_dir = self.project_dir / "chapters"
        if not chapters_dir.exists():
            return
        dirty = False
        for ch_file in sorted(chapters_dir.glob("*.txt")):
            if ch_file.name.endswith(".outline.md"):
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
        """检索相关段落，返回格式化文本。"""
        if not self.index._built:
            self.index.build()
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
