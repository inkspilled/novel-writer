"""统一 LLM 客户端，支持所有 OpenAI 兼容接口（含 Ollama /v1 端点）。

只需 api_key + base_url + model 即可工作。
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from typing import AsyncIterator

from .base import BaseLLM, LLMMessage, LLMResponse
from ..logger import get_logger

logger = get_logger(__name__)

# 在模块级别禁用 shiboken 的特性检查，避免与 openai 导入冲突
os.environ['SHIBOKEN_DISABLE_FEATURE_IMPORT_CHECK'] = '1'


def _get_retryable_errors():
    """懒加载可重试的异常类型，避免 PySide6 shiboken 导入冲突。"""
    from openai import RateLimitError, APITimeoutError, APIConnectionError
    return (RateLimitError, APITimeoutError, APIConnectionError)


# ── 全局限流器：保证两次 API 调用之间有最小间隔 ──
class _RateLimiter:
    """异步全局限流器，所有 LLMClient 实例共享。"""

    def __init__(self, min_interval: float = 2.0):
        self._lock = asyncio.Lock()
        self._last_request_time: float = 0.0
        self._min_interval = min_interval

    async def acquire(self):
        """等待直到距离上次请求超过 min_interval 秒。"""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                wait = self._min_interval - elapsed
                logger.debug("限流器：等待 %.2f 秒", wait)
                await asyncio.sleep(wait)
            self._last_request_time = time.monotonic()

_global_limiter = _RateLimiter(min_interval=3.0)

# N-01 修复：使用模块级缓存实现真正的单次懒加载
_async_openai_cls = None


def _get_async_openai():
    """懒加载 AsyncOpenAI，避免 PySide6 shiboken 导入链冲突。"""
    global _async_openai_cls
    if _async_openai_cls is None:
        from openai import AsyncOpenAI
        _async_openai_cls = AsyncOpenAI
    return _async_openai_cls


class LLMClient(BaseLLM):
    """统一 LLM 客户端，通过 OpenAI 兼容协议连接所有模型。"""

    def __init__(self, model: str, api_key: str = "", base_url: str = "https://api.openai.com/v1", **kwargs):
        super().__init__(model, api_key, base_url, **kwargs)
        AsyncOpenAI = _get_async_openai()
        self.client = AsyncOpenAI(api_key=api_key or "ollama", base_url=base_url, max_retries=0)
        logger.info("LLM 客户端初始化: model=%s, base_url=%s", model, base_url)

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
        max_retries: int = 5,
    ) -> LLMResponse:
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                await _global_limiter.acquire()
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
            except _get_retryable_errors() as e:
                last_exc = e
                if attempt >= max_retries:
                    logger.error("LLM 调用重试 %d 次后仍然失败: %s", attempt + 1, e)
                    raise
                # 指数退避 + 随机抖动，429 限流时等待更久
                base_wait = 2 ** attempt
                from openai import RateLimitError
                if isinstance(e, RateLimitError):
                    base_wait = max(base_wait + attempt * 2, 8)
                wait = base_wait + random.uniform(0, 2)
                logger.warning("LLM 调用遇到可重试错误 (%s)，第 %d 次重试，等待 %.1f 秒...", e, attempt + 1, wait)
                await asyncio.sleep(wait)
        raise last_exc  # type: ignore[misc]

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        max_retries: int = 5,
    ) -> AsyncIterator[str]:
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                await _global_limiter.acquire()
                stream = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": m.role, "content": m.content} for m in messages],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                )
                async for chunk in stream:
                    try:
                        if chunk.choices and chunk.choices[0].delta:
                            delta = chunk.choices[0].delta
                            content = delta.content or ""
                            if not content and hasattr(delta, 'reasoning') and delta.reasoning:
                                content = delta.reasoning
                            if content:
                                yield content
                    except (AttributeError, IndexError):
                        logger.debug("跳过空的流式响应 chunk")
                        continue
                return  # 流式正常结束
            except _get_retryable_errors() as e:
                last_exc = e
                if attempt >= max_retries:
                    logger.error("流式 LLM 调用重试 %d 次后仍然失败: %s", attempt + 1, e)
                    raise
                base_wait = 2 ** attempt
                from openai import RateLimitError
                if isinstance(e, RateLimitError):
                    base_wait = max(base_wait + attempt * 2, 8)
                wait = base_wait + random.uniform(0, 2)
                logger.warning("流式 LLM 调用遇到可重试错误 (%s)，第 %d 次重试，等待 %.1f 秒...", e, attempt + 1, wait)
                await asyncio.sleep(wait)
        raise last_exc  # type: ignore[misc]
