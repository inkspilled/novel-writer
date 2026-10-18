from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

# 思考模式：off=强制关闭（工具类步骤省 token）；on=强制开启；auto=不干预模型默认行为
ThinkingMode = Literal["off", "on", "auto"]

# OpenAI 工具 schema（只传结构，不绑定 SDK 类型）
ToolSchema = dict[str, Any]


@dataclass
class LLMMessage:
    # N-06 修复：使用 Literal 类型确保 role 安全
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    # assistant 发起工具调用时携带（OpenAI 格式），tool 角色消息用 tool_call_id 回填
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str = ""


@dataclass
class LLMResponse:
    content: str
    model: str = ""
    # N-04 修复：明确 usage 类型
    usage: dict[str, int] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""  # stop / length / tool_calls ...


class BaseLLM(ABC):
    """LLM 抽象基类，所有模型实现需继承此类。"""

    def __init__(self, model: str, api_key: str = "", base_url: str = "", **kwargs):
        # M-03 修复：添加参数验证
        if not model or not model.strip():
            raise ValueError("model 不能为空")
        self.model = model.strip()
        self.api_key = api_key
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.extra = kwargs

    @abstractmethod
    async def chat(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking: ThinkingMode = "auto",
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        """单次对话，返回完整响应。tools=None 表示纯文本调用。"""
        ...

    @abstractmethod
    def stream_chat(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking: ThinkingMode = "auto",
        tools: list[ToolSchema] | None = None,
    ) -> AsyncIterator[tuple[str, str]]:
        """流式对话，逐块返回 (kind, text)。

        kind: "thinking"（思考过程）| "text"（正式回答）
        分片不带任何包装标签，UI 自行按 kind 着色。
        工具调用为一次性语义，流式驱动可忽略 tools。
        """
        ...
