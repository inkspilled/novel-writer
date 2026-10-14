from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

# 思考模式：off=强制关闭（工具类步骤省 token）；on=强制开启；auto=不干预模型默认行为
ThinkingMode = Literal["off", "on", "auto"]


@dataclass
class LLMMessage:
    # N-06 修复：使用 Literal 类型确保 role 安全
    role: Literal['system', 'user', 'assistant']
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str = ""
    # N-04 修复：明确 usage 类型
    usage: dict[str, int] = field(default_factory=dict)


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
    ) -> LLMResponse:
        """单次对话，返回完整响应。"""
        ...

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking: ThinkingMode = "auto",
    ) -> AsyncIterator[tuple[str, str]]:
        """流式对话，逐块返回 (kind, text)。

        kind: "thinking"（思考过程）| "text"（正式回答）
        分片不带任何包装标签，UI 自行按 kind 着色。
        """
        ...
