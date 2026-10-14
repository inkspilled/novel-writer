from __future__ import annotations

from typing import AsyncIterator

from .base import BaseLLM, LLMMessage, LLMResponse, ThinkingMode
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
        # 显式超时：httpx 默认 5s 对 LLM 生成太短
        http_client = httpx.AsyncClient(verify=certifi.where(), timeout=httpx.Timeout(300.0, connect=15.0))
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

    async def chat(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking: ThinkingMode = "auto",
    ) -> LLMResponse:
        # M-05 修复：多条 system 消息拼接，不静默丢失
        system_parts = []
        chat_messages = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            else:
                chat_messages.append({"role": m.role, "content": m.content})
        system_msg = "\n\n".join(system_parts) if system_parts else ""

        create_kwargs: dict = dict(
            model=self.model,
            system=system_msg,
            messages=chat_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # Claude 的 extended thinking 是 opt-in；off/auto 都不传即关闭
        if thinking == "on":
            create_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 4096}

        resp = await self.client.messages.create(**create_kwargs)
        text_parts = [block.text for block in resp.content if getattr(block, "type", "") == "text"]
        return LLMResponse(
            content="\n".join(text_parts),
            model=resp.model,
            usage={"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
        )

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking: ThinkingMode = "auto",
    ) -> AsyncIterator[tuple[str, str]]:
        # M-05 修复：多条 system 消息拼接，不静默丢失
        system_parts = []
        chat_messages = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            else:
                chat_messages.append({"role": m.role, "content": m.content})
        system_msg = "\n\n".join(system_parts) if system_parts else ""

        create_kwargs: dict = dict(
            model=self.model,
            system=system_msg,
            messages=chat_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if thinking == "on":
            create_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 4096}

        async with self.client.messages.stream(**create_kwargs) as stream:
            async for event in stream:
                # Claude: thinking 块与 text 块分开
                etype = getattr(event, "type", "") or ""
                if etype == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    block_type = getattr(delta, "type", "") or ""
                    text = getattr(delta, "text", None) or getattr(delta, "thinking", None) or ""
                    if text:
                        kind = "thinking" if "thinking" in block_type else "text"
                        yield kind, text
                else:
                    # 兼容只推 text_stream 的旧路径
                    text = getattr(event, "text", None)
                    if text:
                        yield "text", text
