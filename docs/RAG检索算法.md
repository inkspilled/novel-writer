# RAG 检索算法

> 引擎：`src/novel_writer/core/rag.py` → `RAGRetriever` / `BM25Index`；`core/embeddings.py` → `EmbeddingClient`
> 调用点：[分层上下文组装算法](分层上下文组装算法.md)（历史章节检索 + 规划文档片段检索）
> 落盘：`rag_index.json`（章节文本块 + mtime 指纹）+ `rag_vectors.json`（语义向量，配了 embedding 才有）
> 测试：`tests/test_rag.py`

## 一句话总结

轻量检索，**BM25 关键词为主、语义向量可选**：把历史章节/规划文档按段落分块建倒排索引，对当前写作任务查询返回 top-k 相关段落，替代「把所有前文塞进去」。配置 Embedding 模型后升级为语义检索（别名/近义可召回），未配置或调用失败自动回退 BM25——**任何情况下检索都不会失败**。

## 语义检索（Embedding，可选）

**配置**（设置 → 模型设置 → 语义检索）：`data/config.json` 的 `embedding` 键 `{model, base_url, api_key}`；model 留空 = 不启用。base_url/key 留空时回落当前供应商配置（OpenAI 兼容 `/embeddings` 端点；DeepSeek 等无此端点的供应商需单独填）。

**链路**（`RAGRetriever.retrieve`）：

```
查询 ─┬─ 配置了 embedding：向量检索（cosine top-k）
      │    ├─ 缺失向量批量补嵌（64 条/次，落盘 rag_vectors.json）
      │    └─ 失败 ↓
      └─ 未配置/失败：BM25 关键词检索
```

- 向量持久化 `rag_vectors.json`：`{version, model, dim, vectors: {"ch3_p2:<内容指纹>": "<base64 float32 归一化>"}}`——base64 比 JSON 浮点省 8 成体积；
- 向量键 = `chunk_id + md5(正文)前8位`：id 是 `ch{n}_p{i}`，润色后同段位 id 不变，**不带指纹会错用改写前的旧向量**；
- 补嵌时顺带清理陈旧键（章节润色后段落变少、id:指纹 组合失效的）；换 embedding 模型 → 全部向量作废重嵌；
- 余弦排序有 numpy 用 numpy，没有则纯 Python（功能不受影响）；
- 规划文档检索（`_rag_planning_fragments`）**仍走 BM25**：文档小、关键词命中准，无需语义化。

**成本**：首次启用时全书 chunk 批量嵌入（一次性，之后每章只嵌新增段落 + 1 次查询嵌入）；调用失败静默回退 BM25，不影响写作流程。

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

历史章节加载范围：`ch_num < up_to_chapter`（不含当前章）；跳过 `.outline.md` 与 `.bak.txt` 备份；空文件跳过。

## 索引持久化（`rag_index.json`）

```
{"version": 1, "chunks": [{"id", "chapter", "text"}], "chapter_mtimes": {"章号": mtime}}
```

- `RAGRetriever` 构造时尝试从 `rag_index.json` 恢复文本块与 mtime 指纹（token 在 `add_chunk` 时重算）；
- 恢复后 `load_chapters` 仍**逐章比对 mtime**：被编辑过的章节移除旧块重新分块——持久化只加速冷启动，不引入陈旧数据；
- 增量加载发生分块（`dirty`）后写盘；文件损坏/版本不符时静默放弃、走全量重建；
- 规划文档索引（`_planning_index`）仍按 mtime 在内存缓存，不落盘。

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

- BM25 的 bigram+unigram 对人名/专有名词友好，但同义词（「灵石」vs「灵玉」）无法召回——语义检索启用后由向量路径补足。
- 进程内检索器缓存（`_RETRIEVER_CACHE`）+ 磁盘持久化后，大书冷启动只需重算 df/解码向量，不需重新分块/重嵌。
- 语义检索是单路余弦（无 rerank、无混合加权）；rerank 为后续候补。
- 规划文档走 `ContextBuilder._rag_planning_fragments` 现场建 BM25 索引，不进 `RAGRetriever`（章节索引与规划索引分离）。
- `Chunk.tokens` 在 `add_chunk` 时填充；`search` 前会 `build()`（幂等）。
- 查询侧不做停用词过滤，`第N章` 等高频词会稀释 IDF（影响有限，因 BM25 本身有 IDF）。
