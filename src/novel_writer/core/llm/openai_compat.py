from __future__ import annotations

from typing import AsyncIterator

from .base import BaseLLM, LLMMessage, LLMResponse, ThinkingMode
from .thinking import build_thinking_body, parse_json_body
from .client import _delta_parts
from ..logger import get_logger

logger = get_logger(__name__)

# N-03 修复：使用模块级缓存实现真正的单次懒加载
_async_openai_cls = None


def _get_async_openai():
    """懒加载 AsyncOpenAI，避免与 PySide6 shiboken 导入冲突。"""
    global _async_openai_cls
    if _async_openai_cls is None:
        from openai import AsyncOpenAI
        _async_openai_cls = AsyncOpenAI
    return _async_openai_cls


class OpenAICompatLLM(BaseLLM):
    """OpenAI 兼容接口，支持 DeepSeek / Kimi / GLM / 通义千问等。"""

    def __init__(self, model: str, api_key: str, base_url: str = "https://api.openai.com/v1", **kwargs):
        super().__init__(model, api_key, base_url, **kwargs)
        AsyncOpenAI = _get_async_openai()
        import ssl
        import httpx, certifi
        # httpx 默认 5s 对 LLM 生成太短
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        http_client = httpx.AsyncClient(
            verify=ssl_ctx, timeout=httpx.Timeout(300.0, connect=15.0)
        )
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
        logger.info("OpenAI 兼容客户端初始化: model=%s, base_url=%s", model, base_url)

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
            logger.debug("OpenAI 兼容客户端已关闭")

    async def chat(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking: ThinkingMode = "auto",
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        create_kwargs: dict = dict(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        extra = build_thinking_body(
            thinking,
            model=self.model,
            base_url=self.base_url,
            dialect=self.extra.get("thinking_dialect"),
            off_body=parse_json_body(self.extra.get("thinking_off_body")),
            on_body=parse_json_body(self.extra.get("thinking_on_body")),
        )
        if extra:
            create_kwargs["extra_body"] = extra
        resp = await self.client.chat.completions.create(**create_kwargs)
        choice = resp.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            usage={"prompt_tokens": resp.usage.prompt_tokens, "completion_tokens": resp.usage.completion_tokens} if resp.usage else {},
        )

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking: ThinkingMode = "auto",
        tools: list[dict] | None = None,
    ) -> AsyncIterator[tuple[str, str]]:
        create_kwargs: dict = dict(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        extra = build_thinking_body(
            thinking,
            model=self.model,
            base_url=self.base_url,
            dialect=self.extra.get("thinking_dialect"),
            off_body=parse_json_body(self.extra.get("thinking_off_body")),
            on_body=parse_json_body(self.extra.get("thinking_on_body")),
        )
        if extra:
            create_kwargs["extra_body"] = extra
        stream = await self.client.chat.completions.create(**create_kwargs)
        try:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta:
                    kind, text = _delta_parts(chunk.choices[0].delta)
                    if text:
                        yield kind, text
        finally:
            try:
                await stream.close()
            except Exception:
                pass
