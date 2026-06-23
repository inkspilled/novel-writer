from __future__ import annotations

from typing import AsyncIterator

from .base import BaseLLM, LLMMessage, LLMResponse
from ..logger import get_logger

logger = get_logger(__name__)

# N-02 修复：使用模块级缓存实现真正的单次懒加载
_anthropic_module = None


def _get_anthropic():
    """懒加载 anthropic，避免与 PySide6 shiboken 导入冲突。"""
    global _anthropic_module
    if _anthropic_module is None:
        import anthropic
        _anthropic_module = anthropic
    return _anthropic_module


class ClaudeLLM(BaseLLM):
    """Anthropic Claude 模型接口。"""

    def __init__(self, model: str = "claude-sonnet-4-20250514", api_key: str = "", **kwargs):
        super().__init__(model, api_key, **kwargs)
        anthropic = _get_anthropic()
        import httpx, certifi
        http_client = httpx.AsyncClient(verify=certifi.where())
        self.client = anthropic.AsyncAnthropic(api_key=api_key, http_client=http_client)
        logger.info("Claude LLM 初始化: model=%s", model)

    async def __aenter__(self):
        """支持 async with 语法。"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出时关闭客户端。"""
        await self.close()

    async def close(self):
        """关闭 HTTP 客户端连接。"""
        if self.client:
            await self.client.close()
            logger.debug("Claude HTTP 客户端已关闭")

    async def chat(self, messages: list[LLMMessage], temperature: float = 0.7, max_tokens: int = 4096) -> LLMResponse:
        # M-05 修复：多条 system 消息拼接，不静默丢失
        system_parts = []
        chat_messages = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            else:
                chat_messages.append({"role": m.role, "content": m.content})
        system_msg = "\n\n".join(system_parts) if system_parts else ""

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
        # M-05 修复：多条 system 消息拼接，不静默丢失
        system_parts = []
        chat_messages = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            else:
                chat_messages.append({"role": m.role, "content": m.content})
        system_msg = "\n\n".join(system_parts) if system_parts else ""

        async with self.client.messages.stream(
            model=self.model,
            system=system_msg,
            messages=chat_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ) as stream:
            async for text in stream.text_stream:
                yield text
