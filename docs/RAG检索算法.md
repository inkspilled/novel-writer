# RAG 检索算法

> 引擎：`src/novel_writer/core/rag.py` → `RAGRetriever` / `BM25Index`；
> `core/rag_chunking.py` → 场景分块 + 元数据；`core/rerank.py` → `RerankClient`；
> `core/embeddings.py` → `EmbeddingClient`
> 调用点：[分层上下文组装算法](分层上下文组装算法.md)（历史章节检索 + 规划文档片段检索）、
> Agent 工具 `search_chapters`（`core/agents/tools.py`）
> 落盘：`rag_index.json`（场景块 + 元数据 + mtime 指纹）+ `rag_vectors.json`（语义向量，配了 embedding 才有）
> 测试：`tests/test_rag.py`、`tests/test_rerank.py`

## 一句话总结

**分块策略决定检索上限**。历史章节先按「场景」切块（而不是按固定字数硬切），每块带
章节/场景类型/出场人物/时间线元数据；检索时先召回 Top 20–50 候选，按元数据过滤，
再用**可插拔的重排序模型**（Qwen3-Reranker / BGE / Cohere / Jina 等，模型名由配置决定）
精排，最后取 top-k。BM25 与语义向量检索都是召回层，
**任何一层失败都自动降级，检索不会失败**。

## 分块：按场景切，不按字数切

小说检索失败最常见的原因是切块把情节拦腰截断（「他拔出了剑」与「对方应声倒地」
落在两个块里）。`core/rag_chunking.py` 的处理顺序：

1. **切自然段**：按空行 `\n\n` 切；没有空行的老稿退化为按单换行切；
2. **合并成场景**：连续段落属于同一场景，遇到以下信号断开：
   - 独占一行的分隔符：`***`、`---`、`===`、`◇◇◇`、`※※※` 等；
   - 段首时间跳转：`次日`、`翌日`、`三天后`、`半个时辰后`、`当晚`、`清晨`、`黄昏`……；
   - 段首位移/视角切换：`与此同时`、`另一边`、`回到`、`走出`、`走进`、`离开`、`踏入`、`搬进`……；
   - 短转场句（≤24 字且含地点/时辰提示，如「内门后山。」「同一夜。」「赵无极的书房还亮着。」）；
   - 章内小标题：`第N章/卷/回`。
   过短的场景会并进相邻场景（极短的舞台指示 ≤12 字归入下一场，其余归入上一场），
   避免 20 字下限把单句转场整段丢掉。
3. **整场一块**：场景估算 token 未超过 `DEFAULT_MAX_CHUNK_TOKENS=1500` 时，整场入索引；
4. **超长才切**：只在场景内部按自然段切，相邻窗口保留 `DEFAULT_OVERLAP_RATIO=15%`
   的尾部重叠（整段放不下时退到句级），防止关键情节断在边界；重叠**只在场景内部**
   生效，场景边界本身是叙事断点，不做跨场景重叠，避免把两场戏混进同一个块；
5. **极长段落**：先按句末标点切；仍超限才按字符硬切（兜底，正常网文不会触发）。

> token 估算与 `workflow/context.estimate_tokens` 同口径：中文≈1.5/字，英文单词≈1.3。
> 所以 1500 token 约等于 1000 个汉字；块内正文注入上限为 `RAG_TEXT_CAP=1200` 字。

**过滤**：保留短事实句，避免关键道具、位置等短句因不足 20 字而丢失；仅跳过空内容与纯标题。规划文档仍按 Markdown
分节走 BM25（文档小、关键词命中准），不套用场景分块。

### 块元数据

| 字段 | 含义 | 来源 |
|------|------|------|
| `chapter` | 章号 | 文件名 `^{n}_` |
| `scene` | 章内场景序号（0 起） | 场景分组结果 |
| `scene_type` | `对话` / `战斗` / `日常` / `回忆` | 规则打分（见下） |
| `characters` | 出场人物（最多 6 个） | `world_state.json` 的角色名在块内做子串匹配 |
| `timeline` | `第N章·场景M` | 章号 + 场景序号 |

**场景类型**不调 LLM，用轻量规则：
- 战斗：兵器/攻防/受伤类关键词密度；
- 回忆：`回忆/想起/当年/往事/童年/记忆里` 等标记；
- 对话：引号内文本占比（`“…”`、`「…」`）；
- 都不明显 → 日常。

角色名表从 `world_state.json` 的 `characters` 读取；角色名变化时只重算块内
`characters`，不重新分块、不重嵌向量。

## 检索流水线

```
查询
 ├─ 1. 识别元数据意图
 │     显式参数：chapter=3 / scene_type=战斗（Agent 工具可传）
 │     查询文本：「第三章」「对话/战斗/回忆」（只做软排序）
 ├─ 2. 候选召回 Top 20–50（默认 30，上限 50）
 │     ├─ 配了 embedding：向量 cosine top-k，与 BM25 使用 RRF 名次融合
 │     └─ 未配 embedding：使用 BM25 关键词召回
 ├─ 3. 元数据过滤
 │     显式参数 → 硬过滤；过滤后为空自动回退
 │     查询解析 → 只把匹配块排前，不删召回
 │     load_chapters(up_to=n-2) 同时设检索上界：索引里有未来章节也不会注入
 ├─ 4. 重排序（可选，模型可插拔）
 │     任意兼容 /rerank 的服务：Qwen3-Reranker / BGE / Cohere / Jina / TEI / vLLM
 └─ 5. 取 top-k（上下文组装默认 5）输出
```

### 为什么先召回 20–50 再重排

向量/BM25 都是「宽召回」：问「主角第一次用剑」，会召回一堆带「剑」的段落。
重排序模型同时看查询与候选全文，能按完整意图把「初次拔剑」那段提到最前。
候选太少会漏，太多会拖慢 reranker；默认 30 是精度/延迟的折中。

### 硬过滤 vs 软过滤

| 来源 | 行为 | 场景 |
|------|------|------|
| `retrieve(chapter=3, scene_type="对话")` | **硬过滤**，只在这些块里召回；无结果自动回退（仍受章节上界约束） | Agent 明确知道范围 |
| 查询文本「第三章的对话」 | **软排序**，匹配块排前但不删除其他块 | 自动上下文查询，避免大纲提到别章时误伤 |

Agent 工具 `search_chapters` 已暴露 `chapter` / `scene_type` 参数，问「第三章主角和
反派对峙的对话」时可以直接硬过滤，精度提升最明显。

## 语义检索（Embedding，可选）

**配置**（设置 → 模型设置 → 语义检索）：`data/config.json` 的 `embedding` 键
`{model, base_url, api_key}`；model 留空 = 不启用。base_url/key 留空时回落当前供应商
配置（OpenAI 兼容 `/embeddings` 端点；DeepSeek 等无此端点的供应商需单独填）。

- 向量持久化 `rag_vectors.json`：`{version, model, dim, vectors: {"ch3_p2:<内容指纹>": "<base64 float32 归一化>"}}`；
- 向量键 = `chunk_id + md5(正文)前8位`：id 是 `ch{n}_p{i}`，润色后同段位 id 不变，
  **不带指纹会错用改写前正文的旧向量**；
- 补嵌时顺带清理陈旧键；换 embedding 模型 → 全部向量作废重嵌；
- 余弦排序有 numpy 用 numpy，没有则纯 Python（功能不受影响）；
- 与 BM25 的融合用 **RRF 名次融合**（`1/(60+rank)` 累加），不是分数加权：两路分数量纲不同，按名次融更稳；
- embedding 单次调用有 **60s 硬墙钟**（`NOVEL_WRITER_EMBED_TIMEOUT`）：本地 embedding 冷启动曾挂 9 分钟，超时直接抛错、本次检索回退 BM25。

## 重排序（任意 /rerank 模型，可选）

**配置**（设置 → 模型设置 → 重排序）：`data/config.json` 的 `rerank` 键
`{model, base_url, api_key}`；**`model` 是自由填写的模型名，不写死任何厂商**，
唯一要求是服务暴露兼容的 `/rerank` 接口。model 留空 = 不启用。base_url/key 留空时回落当前供应商。

**兼容协议**（`core/rerank.py` 按地址/响应自适应）：

| 服务 | 请求形态 |
|------|----------|
| Cohere / Jina / vLLM / Xinference | `{model, query, documents, top_n}` |
| HuggingFace TEI | `{query, texts}`（首次 4xx 自动换这套 payload） |
| DashScope 原生 | `{model, input:{query, documents}, parameters:{top_n}}` |

- base_url 已带 `rerank` 时原样使用；DashScope 根地址自动补
  `/services/rerank/text-rerank/text-rerank`；其余补 `/rerank`；
- 响应兼容 `results[].relevance_score` / `score` 与 `output.results[]`；
- 本地 4060 可跑 Qwen3-Reranker-0.6B 或 BGE-reranker-v2-m3（由 vLLM / SGLang / TEI 等
  暴露 `/rerank` 端点），与 0.6B embedding 同时挂载压力不大；本地示例：
  `model=BAAI/bge-reranker-v2-m3`、`base_url=http://localhost:8000/v1`（客户端自动补 `/rerank`）；
- 任何异常/超时/空结果 → 保留召回排序，**不影响写作流程**。

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

## 索引持久化（`rag_index.json`）

```json
{
  "version": 3,
  "chunks": [
    {"id": "ch3_p0", "chapter": 3, "text": "……", "scene": 0,
     "scene_type": "战斗", "characters": ["凌尘"], "timeline": "第3章·场景1"}
  ],
  "known_characters": ["凌尘", "苏晴"],
  "chapter_mtimes": {"3": 1710000000.0}
}
```

- `RAGRetriever` 构造时尝试恢复；恢复后 `load_chapters` 仍**逐章比对 mtime**，
  被编辑过的章节移除旧块重新分块——持久化只加速冷启动，不引入陈旧数据；
- `load_chapters(up_to)` 只分块 `章号 < up_to` 的章节，并把 `up_to` 记为检索上界；
  写作上下文传 `n-2` 时，即使索引里缓存了后续章节，检索也不会把未来剧情注入当前章；
  Agent 工具的 `search_chapters` 传 `10**9`，可检索全书；
- 版本不符时静默放弃、走全量重建；v3 会重新索引旧版漏掉的短事实句；
- `known_characters` 用于判断角色名表是否变化；变化时只重算 `characters` 元数据；
- 规划文档索引（`_planning_index`）仍按 mtime 在内存缓存，不落盘。

## 检索输出（`retrieve`）

```
=== RAG 检索相关段落 ===
[第3章｜场景2｜战斗｜人物：凌尘、苏晴｜相关度:0.812]
（块正文，最多 1200 字）

[规划文档｜相关度:8.1]
（…）
```

规划文档侧另有独立路径 `_rag_planning_fragments`（在 `context.py`），输出格式为
`=== 规划文档相关片段 ===`，与本 `retrieve` 的格式**不同**，两处勿混。

签名：`retrieve(query, top_k=5, chapter=None, scene_type=None, candidate_k=None, text_cap=RAG_TEXT_CAP)`——
`candidate_k` 覆盖默认候选数（`RAG_CANDIDATE_K=30`，上限 `RAG_MAX_CANDIDATES=50`），
`text_cap` 控制每块注入字数（默认 `RAG_TEXT_CAP=1200`）。

## 在上下文组装中的两条链路

| 链路 | 查询来源 | 索引内容 | top_k |
|------|----------|----------|-------|
| 历史章节 RAG | 大纲中第 n 章描述（`extract_chapter_query`） | `chapters/*.txt`，`up_to=n-2` | 5 |
| 规划文档 RAG | `step_id + 第n章 + 书名` | 当前步骤 `input_files` 里的规划 md | 5 |

规划 RAG **失败时回退**：每个文档取前 500 字（`（摘要）` 标记）。检索成功但无结果返回空串（不回退）。

## 边界与已知限制

- BM25 的 bigram+unigram 对人名/专有名词友好，但同义词（「灵石」vs「灵玉」）无法召回——
  语义检索启用后由向量路径补足。
- 场景边界是**规则启发式**：没有分隔符/时间地点标记的长对话可能被并成一场；
  超长场景内部切分仍可能切断动作链。需要更高精度时可在章前跑一次 LLM 场景标注，
  当前版本未接入（保持离线可用与零额外 token）。
- 场景类型是关键词/引号比例的粗分类；战斗中的对话、回忆中的战斗可能归到主类型。
- 角色名只认 `world_state.json` 里已登记的名字；未登记的新角色不会进元数据
  （避免把普通词误判成人名）。
- 语义检索是与 BM25 的 **RRF 名次融合**（不是分数加权混合），融合后再交给 reranker 精排；
  单路（未配 embedding 或调用失败）时退化为纯 BM25。
- 规划文档走 `ContextBuilder._rag_planning_fragments` 现场建 BM25 索引，不进
  `RAGRetriever`（章节索引与规划索引分离）。
- `Chunk.tokens` 在 `add_chunk` 时填充；`search` 前会 `build()`（幂等）。
- 查询侧不做停用词过滤，`第N章` 等高频词会稀释 IDF（影响有限，因 BM25 本身有 IDF）。
- **RAG 不是知识库的唯一入口**：它负责「散文/设定片段召回」；角色当前位置、当前目标、
  伏笔是否回收等**事实**必须读 `world_state.json` / `memory.json` / `story_threads.json`，
  否则会出现「检索到旧段落，答成旧状态」。两者互补，见[项目存储与文件格式](项目存储与文件格式.md)「流水账选型」。
