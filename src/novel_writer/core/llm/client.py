"""统一 LLM 客户端，支持所有 OpenAI 兼容接口（含 Ollama /v1 端点）。

只需 api_key + base_url + model 即可工作。
"""

from __future__ import annotations

from typing import AsyncIterator

from .base import BaseLLM, LLMMessage, LLMResponse
from ..logger import get_logger

logger = get_logger(__name__)


def _get_async_openai():
    """懒加载 AsyncOpenAI，避免 PySide6 shiboken 与 openai 导入链冲突。"""
    from openai import AsyncOpenAI
    return AsyncOpenAI


class LLMClient(BaseLLM):
    """统一 LLM 客户端，通过 OpenAI 兼容协议连接所有模型。"""

    def __init__(self, model: str, api_key: str = "", base_url: str = "https://api.openai.com/v1", **kwargs):
        super().__init__(model, api_key, base_url, **kwargs)
        AsyncOpenAI = _get_async_openai()
        self.client = AsyncOpenAI(api_key=api_key or "ollama", base_url=base_url)
        logger.info("LLM 客户端初始化: model=%s, base_url=%s", model, base_url)

    async def chat(self, messages: list[LLMMessage], temperature: float = 0.7, max_tokens: int = 4096) -> LLMResponse:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        choice = resp.choices[0]
        content = choice.message.content or ""
        if not content and hasattr(choice.message, 'reasoning') and choice.message.reasoning:
            content = choice.message.reasoning
        return LLMResponse(
            content=content,
            model=resp.model,
            usage={
                "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
            },
        )

    async def stream_chat(
        self, messages: list[LLMMessage], temperature: float = 0.7, max_tokens: int = 4096
    ) -> AsyncIterator[str]:
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            try:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
            except (AttributeError, IndexError):
                continue
