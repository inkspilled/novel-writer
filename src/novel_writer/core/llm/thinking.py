"""思考（reasoning/thinking）开关方言映射。

不同厂商的 OpenAI 兼容接口，思考开关字段并不统一：
- 通义千问 / DashScope / vLLM / SGLang : extra_body.enable_thinking
- 智谱 GLM                             : extra_body.thinking = {type: disabled|enabled}
- DeepSeek                             : extra_body.reasoning_effort = none|low|...
- Moonshot / Kimi                      : extra_body.enable_thinking（新版）或 thinking
- 小米 MiMo (api.milai.com)            : extra_body.enable_thinking（默认）/可配置
- OpenAI o 系列                        : reasoning_effort = none|minimal|low|medium|high
- Ollama                               : 顶层 think: false（由 ollama.py 处理）
- Claude                               : thinking={type, budget_tokens}（由 claude.py 处理）

因此按 base_url / 模型名推断方言，也允许在模型配置里显式指定
thinking_dialect 或直接写 thinking_off_body / thinking_on_body 覆盖。
"""
from __future__ import annotations

import json
from typing import Literal

from .base import ThinkingMode
from ..logger import get_logger

logger = get_logger(__name__)

ThinkingDialect = Literal[
    "qwen",       # enable_thinking
    "glm",        # thinking: {type}
    "deepseek",   # reasoning_effort
    "openai",     # reasoning_effort
    "kimi",       # enable_thinking + thinking
    "mimo",       # enable_thinking（小米）
    "none",       # 不传任何思考字段
]

# 各方言在 off / on 下写入 extra_body 的载荷
_DIALECT_PAYLOADS: dict[str, tuple[dict, dict]] = {
    "qwen": (
        {"enable_thinking": False},
        {"enable_thinking": True},
    ),
    "glm": (
        {"thinking": {"type": "disabled"}},
        {"thinking": {"type": "enabled"}},
    ),
    "deepseek": (
        {"reasoning_effort": "none"},
        {"reasoning_effort": "medium"},
    ),
    "openai": (
        {"reasoning_effort": "none"},
        {"reasoning_effort": "medium"},
    ),
    "kimi": (
        {"enable_thinking": False},
        {"enable_thinking": True},
    ),
    # MiMo 的 enable_thinking=false 不一定会真正关思考；reasoning_effort=none
    # 才会让 stream 直接出正文（实测 53s→12s）。
    "mimo": (
        {"enable_thinking": False, "reasoning_effort": "none"},
        {"enable_thinking": True},
    ),
    "none": ({}, {}),
}


def detect_dialect(model: str = "", base_url: str = "") -> ThinkingDialect:
    """按 base_url / 模型名推断思考方言。显式配置优先于推断。"""
    url = (base_url or "").lower()
    name = (model or "").lower()

    # 本地 Ollama 优先：模型名可能带 qwen/glm 等，但接口走原生 think 字段
    if "localhost:11434" in url or "127.0.0.1:11434" in url or "ollama" in url:
        return "none"
    if "milai.com" in url or "mimo" in name or name.startswith("mimo-"):
        return "mimo"
    if "bigmodel.cn" in url or "zhipu" in url or name.startswith("glm"):
        return "glm"
    if "deepseek" in url or name.startswith("deepseek"):
        return "deepseek"
    if "moonshot" in url or "kimi" in name:
        return "kimi"
    if "dashscope" in url or "qwen" in name or "tongyi" in name:
        return "qwen"
    if "openai.com" in url or name.startswith("o1") or name.startswith("o3") or name.startswith("gpt-5"):
        return "openai"
    # vLLM / LM Studio / llama.cpp 等本地推理：模板方言最接近 Qwen
    if any(x in url for x in ("8000", "8080", "1234", "vllm", "lmstudio")):
        return "qwen"
    return "none"


def resolve_dialect(
    model: str = "",
    base_url: str = "",
    explicit: str | None = None,
) -> ThinkingDialect:
    """确定最终方言：显式配置 > 自动推断。"""
    if explicit:
        exp = explicit.strip().lower()
        if exp in _DIALECT_PAYLOADS:
            return exp  # type: ignore[return-value]
    return detect_dialect(model, base_url)


def build_thinking_body(
    thinking: ThinkingMode,
    *,
    model: str = "",
    base_url: str = "",
    dialect: str | None = None,
    off_body: dict | None = None,
    on_body: dict | None = None,
) -> dict:
    """构造应写入 extra_body 的思考字段。

    off_body / on_body 是用户/模型配置里的完全自定义载荷，优先级最高。
    thinking=auto 时返回空（不干预模型默认行为）。
    """
    if thinking == "auto":
        return {}
    resolved = resolve_dialect(model, base_url, dialect)
    logger.debug("思考开关: mode=%s dialect=%s model=%s", thinking, resolved, model)

    # 自定义覆盖
    custom = off_body if thinking == "off" else on_body
    if custom:
        return dict(custom)

    resolved = resolve_dialect(model, base_url, dialect)
    off_payload, on_payload = _DIALECT_PAYLOADS[resolved]
    return dict(off_payload if thinking == "off" else on_payload)


def parse_json_body(value) -> dict | None:
    """把配置里的 JSON 字符串/字典统一成 dict。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            data = json.loads(value)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None
