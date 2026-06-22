from __future__ import annotations

from typing import AsyncIterator

from .base import BaseLLM, LLMMessage, LLMResponse


def _get_async_openai():
    """懒加载 AsyncOpenAI，避免与 PySide6 shiboken 导入冲突。"""
    from openai import AsyncOpenAI
    return AsyncOpenAI


class OpenAICompatLLM(BaseLLM):
    """OpenAI 兼容接口，支持 DeepSeek / Kimi / GLM / 通义千问等。"""

    def __init__(self, model: str, api_key: str, base_url: str = "https://api.openai.com/v1", **kwargs):
        super().__init__(model, api_key, base_url, **kwargs)
        AsyncOpenAI = _get_async_openai()
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(self, messages: list[LLMMessage], temperature: float = 0.7, max_tokens: int = 4096) -> LLMResponse:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        choice = resp.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            usage={"prompt_tokens": resp.usage.prompt_tokens, "completion_tokens": resp.usage.completion_tokens} if resp.usage else {},
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
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
