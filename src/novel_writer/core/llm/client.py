"""统一 LLM 客户端，支持所有 OpenAI 兼容接口（含 Ollama /v1 端点）。

只需 api_key + base_url + model 即可工作。
"""

from __future__ import annotations

import asyncio
import os
import random
import threading
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
    """异步全局限流器，所有 LLMClient 实例共享。

    用 threading.Lock 而非 asyncio.Lock：调用方（AgentWorker / WorkflowThread 等）
    各自创建独立事件循环，asyncio.Lock 会绑定首次使用的 loop，跨 loop 竞争即死锁。
    """

    def __init__(self, min_interval: float = 0.3):
        self._lock = threading.Lock()
        self._last_request_time: float = 0.0
        self._min_interval = min_interval

    async def acquire(self):
        """等待直到距离上次请求超过 min_interval 秒。"""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_request_time
                if elapsed >= self._min_interval:
                    self._last_request_time = now
                    return
                wait = self._min_interval - elapsed
            logger.debug("限流器：等待 %.2f 秒", wait)
            await asyncio.sleep(wait)

_global_limiter = _RateLimiter(min_interval=0.3)

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
    """统一 LLM 客户端，通过 OpenAI 兼容协议连接所有模型。

    HTTP 客户端绑定创建时的事件循环；调用方（AgentWorker/WorkflowThread）各自
    new_event_loop()，故每次调用前同步循环，循环变化时重建客户端，避免
    "attached to a different event loop" / "Event loop is closed"。
    """

    def __init__(self, model: str, api_key: str = "", base_url: str = "https://api.openai.com/v1", **kwargs):
        super().__init__(model, api_key, base_url, **kwargs)
        self._bound_loop: asyncio.AbstractEventLoop | None = None
        self.client = self._build_client()
        logger.info("LLM 客户端初始化: model=%s, base_url=%s", model, base_url)

    def _build_client(self):
        AsyncOpenAI = _get_async_openai()
        # 用 certifi 证书路径创建 httpx 客户端，避免 Windows 系统证书库卡顿
        # 超时显式设为 300s：httpx 默认 5s 对 LLM 生成太短，会触发无谓重试
        import httpx, certifi
        http_client = httpx.AsyncClient(verify=certifi.where(), timeout=httpx.Timeout(300.0, connect=15.0))
        return AsyncOpenAI(
            api_key=self.api_key or "ollama",
            base_url=self.base_url or "https://api.openai.com/v1",
            max_retries=0,
            http_client=http_client,
        )

    def _sync_loop(self):
        """确保 HTTP 客户端绑定在当前运行中的事件循环上。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._bound_loop is loop:
            return
        old = self.client
        old_loop = self._bound_loop
        self.client = self._build_client()
        self._bound_loop = loop
        if old is not None:
            # 旧 loop 还活着就异步关掉；已关闭则丢弃（连接随进程回收）
            try:
                if old_loop is not None and not old_loop.is_closed() and old_loop is not loop:
                    old_loop.call_soon_threadsafe(lambda: old_loop.create_task(old.close()))
                else:
                    loop.create_task(old.close())
            except Exception as e:
                logger.debug("关闭旧 LLM 客户端失败（忽略）: %s", e)

    async def __aenter__(self):
        """支持 async with 语法。"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出时关闭客户端。"""
        await self.close()

    async def close(self):
        """关闭 HTTP 客户端连接。"""
        if self.client:
            try:
                await self.client.close()
                logger.debug("OpenAI 兼容客户端已关闭")
            except Exception as e:
                logger.debug("LLM 客户端 close 异常（忽略）: %s", e)
            self.client = None

    async def chat(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        max_retries: int = 3,
    ) -> LLMResponse:
        self._sync_loop()
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
                # content 为空时回退到 reasoning（部分模型将回答放在 reasoning 字段）
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
                base_wait = min(2 ** attempt, 15)
                from openai import RateLimitError
                if isinstance(e, RateLimitError):
                    base_wait = max(base_wait + attempt * 2, 8)
                wait = base_wait + random.uniform(0, 1)
                logger.warning("LLM 调用遇到可重试错误 (%s)，第 %d 次重试，等待 %.1f 秒...", e, attempt + 1, wait)
                await asyncio.sleep(wait)
        raise last_exc  # type: ignore[misc]

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        max_retries: int = 3,
    ) -> AsyncIterator[str]:
        self._sync_loop()
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            yielded_any = False
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
                                yielded_any = True
                                yield content
                    except (AttributeError, IndexError):
                        logger.debug("跳过空的流式响应 chunk")
                        continue
                return  # 流式正常结束
            except _get_retryable_errors() as e:
                last_exc = e
                # 已产出部分内容时绝不能从头重试——上游会把重复文本拼进正文
                if yielded_any:
                    logger.error("流式 LLM 调用中途失败（已产出内容，不再重试）: %s", e)
                    raise
                if attempt >= max_retries:
                    logger.error("流式 LLM 调用重试 %d 次后仍然失败: %s", attempt + 1, e)
                    raise
                base_wait = min(2 ** attempt, 15)
                from openai import RateLimitError
                if isinstance(e, RateLimitError):
                    base_wait = max(base_wait + attempt * 2, 8)
                wait = base_wait + random.uniform(0, 1)
                logger.warning("流式 LLM 调用遇到可重试错误 (%s)，第 %d 次重试，等待 %.1f 秒...", e, attempt + 1, wait)
                await asyncio.sleep(wait)
        raise last_exc  # type: ignore[misc]
