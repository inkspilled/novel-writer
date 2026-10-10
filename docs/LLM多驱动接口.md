# LLM 多驱动接口

> 引擎：`src/novel_writer/core/llm/`（`base.py` / `client.py` / `claude.py` / `ollama.py` / `openai_compat.py`）
> 配置：`config/default_providers.json` + 用户 `data/config.json`
> 性质：`算法`（接口契约）+ `规范`（接入约定）

## 一句话总结

统一 LLM 抽象层，四种驱动覆盖 OpenAI 兼容 / Claude 原生 / Ollama 本地；接口收敛为 `api_key + base_url + model`，切换后端只改配置。全部懒加载 + 显式 CA 证书，规避 PySide6 导入链冲突与 Windows 证书库卡顿。

## 驱动矩阵

| 驱动 | 文件 | 协议 | 适用 |
|------|------|------|------|
| `LLMClient` | `client.py` | OpenAI 兼容 | DeepSeek/Kimi/GLM/通义/OpenAI |
| `OpenAICompatLLM` | `openai_compat.py` | OpenAI 兼容 | 通用 OpenAI 兼容接口 |
| `ClaudeLLM` | `claude.py` | Anthropic 原生 | Claude 系列（需 `anthropic` 包） |
| `OllamaLLM` | `ollama.py` | Ollama HTTP | 本地模型（qwen 等） |

## 统一契约（`base.py`）

```python
@dataclass
class LLMMessage:
    role: str      # system / user / assistant
    content: str

@dataclass
class LLMResponse:
    content: str
    # ...

class BaseLLM:
    async def chat(messages, **kwargs) -> LLMResponse
```

Agent 层只依赖 `BaseLLM`，不感知后端差异。

## 供应商配置

`config/default_providers.json`：

```json
{"name": "供应商名", "type": "openai_compat", "base_url": "https://api.example.com/v1"}
```

| type | 说明 |
|------|------|
| `openai_compat` | OpenAI 兼容协议 |
| `ollama` | 本地 Ollama |

Claude 走原生类型（设置界面选择，需 `pip install anthropic`）。

## 接入红线

| 约定 | 原因 |
|------|------|
| **懒加载** SDK（`_get_async_openai()` 等） | PySide6 shiboken 与 openai/anthropic 导入链冲突 |
| 显式 `certifi.where()` 指定 CA | Windows 系统证书库扫描卡顿 |
| 统一 `api_key + base_url + model` | 切换后端零代码改动 |
| 流式输出按 Agent 隔离 | 切换面板不影响进行中的流 |

## 与 Agent 层的关系

见 [Agent 系统](../DEV_GUIDE.md)：`config/agents.json` 每个 Agent 可配独立模型；`BaseAgent.run(prompt)` 内部走所配 LLM 驱动。工作流技能匹配（`needs` 字段）在 Agent 层完成，与 LLM 驱动无关。

## 边限

- 本层**不做**重试/限流/多 key 轮换，由调用方或供应商侧保证。
- Ollama 模型列表通过 HTTP 探测已安装模型。
- 新增供应商类型需在 `client.py` 分发逻辑 + 设置对话框同步。
