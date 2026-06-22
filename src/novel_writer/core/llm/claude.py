from __future__ import annotations

from typing import AsyncIterator

from .base import BaseLLM, LLMMessage, LLMResponse
from ..logger import get_logger

logger = get_logger(__name__)


def _get_anthropic():
    """懒加载 anthropic，避免与 PySide6 shiboken 导入冲突。"""
    import anthropic
    return anthropic


class ClaudeLLM(BaseLLM):
    """Anthropic Claude 模型接口。"""

    def __init__(self, model: str = "claude-sonnet-4-20250514", api_key: str = "", **kwargs):
        super().__init__(model, api_key, **kwargs)
        anthropic = _get_anthropic()
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        logger.info("Claude LLM 初始化: model=%s", model)

    async def chat(self, messages: list[LLMMessage], temperature: float = 0.7, max_tokens: int = 4096) -> LLMResponse:
        system_msg = ""
        chat_messages = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                chat_messages.append({"role": m.role, "content": m.content})

        resp = await self.client.messages.create(
            model=self.model,
            system=system_msg,
            messages=chat_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return LLMResponse(
            content=resp.content[0].text if resp.content else "",
            model=resp.model,
            usage={"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
        )

    async def stream_chat(
        self, messages: list[LLMMessage], temperature: float = 0.7, max_tokens: int = 4096
    ) -> AsyncIterator[str]:
        system_msg = ""
        chat_messages = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                chat_messages.append({"role": m.role, "content": m.content})

        async with self.client.messages.stream(
            model=self.model,
            system=system_msg,
            messages=chat_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ) as stream:
            async for text in stream.text_stream:
                yield text
