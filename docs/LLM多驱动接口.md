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
| Ollama (本地) | `http://localhost:11434` |
| llama.cpp (本地) | `http://localhost:8080/v1` |
| vLLM (本地) | `http://localhost:8000/v1` |
| LM Studio (本地) | `http://localhost:1234/v1` |

## 统一契约（`base.py`）

```python
@dataclass
class LLMMessage:
    role: str      # system / user / assistant
    content: str

@dataclass
class LLMResponse:
    content: str

class BaseLLM:
    async def chat(messages, **kwargs) -> LLMResponse
```

Agent 层只依赖 `BaseLLM`，不感知后端差异。

## 性能红线

| 约定 | 值 | 原因 |
|------|-----|------|
| 全局限流器间隔 | **0.3s** | 曾为 5s，8 步规划纯等 40s+ |
| httpx 超时 | **300s**（连接 15s） | 曾为 5s，LLM 生成必超时→重试 |
| `max_retries` | **3** | 曾为 8，一次抖动最多等 2 分钟 |
| 退避封顶 | **15s** | 防止指数退避爆炸 |
| 懒加载 SDK | 是 | PySide6 shiboken 导入链冲突 |
| 显式 `certifi.where()` | 是 | Windows 系统证书库卡顿 |

## 与 Agent 层的关系

`config/agents.json` 每个 Agent 可配独立模型；`BaseAgent.run(prompt)` 内部走所配 LLM 驱动。工作流技能匹配在 Agent 层完成。

## 边界

- 本层不做多 key 轮换，由调用方或供应商侧保证。
- 新增供应商只需在 `default_providers.json` 加一行 `name + base_url`。
- Claude 原生接口（`claude.py`）保留作为备用，日常走 `LLMClient` 即可。
