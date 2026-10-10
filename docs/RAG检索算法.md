# RAG 检索算法

> 引擎：`src/novel_writer/core/rag.py` → `RAGRetriever` / `BM25Index`
> 调用点：[分层上下文组装算法](分层上下文组装算法.md)（历史章节检索 + 规划文档片段检索）
> 测试：暂无专属测试

## 一句话总结

轻量 BM25 关键词检索，**无需外部嵌入模型**。把历史章节/规划文档按段落分块建倒排索引，对当前写作任务查询返回 top-k 相关段落，替代「把所有前文塞进去」。

## 分词（`_tokenize`）

| 文本形态 | 策略 |
|----------|------|
| 英文/数字单词 | 整词小写入索引 |
| 中文段 | **bigram**（相邻 2 字）+ **unigram**（汉字单字） |
| 标点/空白 | 作为切分边界丢弃 |

例：`灵脉异常` → `灵脉,脉异,异常,灵,脉,异,常`。

> 范围：汉字判定为 `一`–`鿿`（U+4E00–U+9FFF）。

## BM25 公式

标准 BM25，参数 `k1=1.5, b=0.75`：

```
score(q, d) = Σ_{t∈q}  IDF(t) × TF_norm(t, d)

IDF(t)    = ln( (N - df + 0.5) / (df + 0.5) + 1 )
TF_norm   = tf × (k1+1) / (tf + k1 × (1 - b + b × dl / avg_dl))
```

- `N` = chunk 总数，`df` = 含该 token 的 chunk 数（每 chunk 内去重计）
- `dl` = chunk token 数，`avg_dl` = 平均 token 数

## 分块策略

| 来源 | 分块规则 | id 格式 | chapter |
|------|----------|---------|---------|
| 历史章节 `.txt` | 按 `\n\n` 分段，跳过 <20 字段 | `ch{N}_p{i}` | N |
| 规划文档 `.md` | 按 `\n(?=#)` 或 `\n\n+` 分节，跳过 <20 字节 | `plan_{stem}_s{i}` | 0 |

历史章节加载范围：`ch_num < up_to_chapter`（不含当前章）；跳过 `.outline.md`；空文件跳过。

## 检索输出（`retrieve`）

```
=== RAG 检索相关段落 ===
[第3章 相关度:12.4]
（chunk 文本截断 300 字）

[规划文档 相关度:8.1]
（…）
```

规划文档侧另有独立路径 `_rag_planning_fragments`（在 context.py），输出格式为 `=== 规划文档相关片段 ===`，与本文 `retrieve` 的格式**不同**，两处勿混。

## 在上下文组装中的两条链路

| 链路 | 查询来源 | 索引内容 | top_k |
|------|----------|----------|-------|
| 历史章节 RAG | 大纲中第 n 章描述（`extract_chapter_query`） | `chapters/*.txt`，`up_to=n-2` | 5 |
| 规划文档 RAG | `step_id + 第n章 + 书名` | 当前步骤 `input_files` 里的规划 md | 5 |

规划 RAG **失败时回退**：每个文档取前 500 字（`（摘要）` 标记）。检索成功但无结果返回空串（不回退）。

## 边界与已知限制

- bigram+unigram 对人名/专有名词友好，但同义词（「灵石」vs「灵玉」）无法召回。
- 索引是**每次调用现场构建**（`load_chapters` 有 `_loaded` 懒标记，但 `RAGRetriever` 实例通常短命），大书全量重建有开销。
- `load_planning()` 存在但**上下文组装未调用**——规划文档走的是 `_rag_planning_fragments` 现场建索引。
- `Chunk.tokens` 在 `add_chunk` 时填充；`search` 前会 `build()`（幂等）。
- 查询侧不做停用词过滤，`第N章` 等高频词会稀释 IDF（影响有限，因 BM25 本身有 IDF）。
