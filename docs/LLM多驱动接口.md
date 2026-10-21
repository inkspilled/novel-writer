# LLM 多驱动接口

> 引擎：`src/novel_writer/core/llm/`（`base.py` / `client.py` / `claude.py` / `ollama.py` / `openai_compat.py`）
> 配置：`config/default_providers.json` + 用户 `data/config.json`
> 性质：`算法`（接口契约）+ `规范`（接入约定）

## 一句话总结

**统一 OpenAI 兼容 API**：`api_key + base_url + model` 三要素，无类型区分。支持 Ollama / llama.cpp / vLLM / LM Studio / 云端 API。模型列表可拉取（`GET /models`）下拉选择。全部懒加载 + 显式 CA 证书。

## 统一 API 形态

**无 `type` 字段区分**——所有服务走 OpenAI 兼容协议：

| 项 | 说明 |
|----|------|
| `base_url` | API 地址，**填什么用什么**（不自动补 `/v1`） |
| `api_key` | 密钥（本地服务可留空） |
| `model` | 模型名，支持「📋 拉取模型」按钮从 `GET /models` 获取 |

### 预置端点

| 名称 | base_url |
|------|----------|
| DeepSeek | `https://api.deepseek.com/v1` |
| Moonshot (Kimi) | `https://api.moonshot.cn/v1` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 小米 MiMo | `https://api.milai.com/v1` |
| OpenAI | `https://api.openai.com/v1` |
| Ollama (本地) | `http://localhost:11434/v1` |
| llama.cpp (本地) | `http://localhost:8080/v1` |
| vLLM (本地) | `http://localhost:8000/v1` |
| LM Studio (本地) | `http://localhost:1234/v1` |

## 统一契约（`base.py`）

```python
@dataclass
class LLMMessage:
    role: str      # system / user / assistant / tool
    content: str
    tool_calls: list[dict] | None = None   # assistant 发起工具调用
    tool_call_id: str = ""                 # tool 角色回填

@dataclass
class LLMResponse:
    content: str
    model: str = ""
    usage: dict[str, int] = {}             # prompt/completion/total tokens
    tool_calls: list[dict] = []
    finish_reason: str = ""                # stop / length / tool_calls

class BaseLLM:
    async def chat(messages, tools=None, thinking="auto", **kwargs) -> LLMResponse
```

Agent 层只依赖 `BaseLLM`，不感知后端差异。

## 工具调用（tools）

`chat(messages, …, tools=[...])` 支持 OpenAI function calling：

- 请求体带 `tools` + `tool_choice="auto"`；
- 响应解析 `message.tool_calls` 为 `{id,type,function:{name,arguments}}`，回填 `LLMResponse.tool_calls`；
- 历史消息支持 `assistant.tool_calls` 与 `role="tool" + tool_call_id`；
- 网关/模型拒绝 `tools` 时自动去掉该字段重试一次（`_is_invalid_request`），降级为纯文本；
- 流式 `stream_chat` 不承载工具调用（自由对话纯文本），工具循环只走非流式 `chat`。
- MiMo 的 `enable_thinking=false` 不保证关思考，`thinking=off` 同时发 `reasoning_effort=none`；实测同样 100 字开头从 53s 降到 12s。

其余三个驱动（Claude/Ollama/OpenAICompat）接受 `tools` 参数但当前未实现，工作流只装配 `LLMClient`。

## 性能红线

| 约定 | 值 | 原因 |
|------|-----|------|
| 全局限流器间隔 | **0.15s** | 曾为 5s，8 步规划纯等 40s+ |
| 限流器锁 | `threading.Lock` | 调用方各自 `new_event_loop()`，`asyncio.Lock` 跨 loop 竞争即死锁 |
| httpx 超时 | **120s**（连接 15s） | 移动网络/酒店 Wi-Fi 下快速失败，断了就断，交给断点续跑 |
| 硬墙钟超时 | **120s**/次（`NOVEL_WRITER_LLM_TIMEOUT`） | httpx 的 read timeout 会被 keepalive 字节重置；坏网下不硬等，失败即记录/续跑 |
| 流式空闲超时 | **90s** 无新 chunk（`NOVEL_WRITER_STREAM_IDLE_TIMEOUT`） | 连接活着但不吐字，坏网下尽快失败 |
| 流式总时长 | **600s**（`NOVEL_WRITER_STREAM_TOTAL_TIMEOUT`） | 防止慢速滴流把一章拖成几小时 |
| 超时重试 | **0 次** | 120s 都拿不到响应的请求，重试大概率还是卡；交给上层跳过/记录 |
| embedding 硬超时 | **60s**（`NOVEL_WRITER_EMBED_TIMEOUT`） | 本地 8B embedding 批量补嵌可能慢；超时回退 BM25，不阻塞写作 |
| HTTP 客户端 loop 绑定 | 每次调用 `_sync_loop()` | AgentWorker/WorkflowThread 各建 loop，httpx 客户端绑定创建时 loop，换 loop 须重建 |
| 流式重试 | **仅在尚未产出内容时** | 已 yield 后再从头重试会把重复文本拼进正文 |
| `max_retries` | **1** | 坏网下少重试，失败交给上层记录/断点续跑 |
| 退避封顶 | **15s** | 防止指数退避爆炸 |
| 懒加载 SDK | 是 | PySide6 shiboken 导入链冲突 |
| 显式 `certifi.where()` | 是 | Windows 系统证书库卡顿 |

## 与 Agent 层的关系

`config/agents.json` 每个 Agent 可配独立模型；`BaseAgent.run(prompt)` 内部走所配 LLM 驱动。工作流技能匹配在 Agent 层完成。

## 边界

- 本层不做多 key 轮换，由调用方或供应商侧保证。
- 新增供应商只需在 `default_providers.json` 加一行 `name + base_url`。
- Claude 原生接口（`claude.py`）保留作为备用，日常走 `LLMClient` 即可。
