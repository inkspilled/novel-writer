"""统一 LLM 客户端，支持所有 OpenAI 兼容接口（含 Ollama /v1 端点）。

只需 api_key + base_url + model 即可工作。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import time
from typing import AsyncIterator

from .base import BaseLLM, LLMMessage, LLMResponse, ThinkingMode
from .thinking import build_thinking_body, parse_json_body
from ..logger import get_logger

logger = get_logger(__name__)

# 在模块级别禁用 shiboken 的特性检查，避免与 openai 导入冲突
os.environ['SHIBOKEN_DISABLE_FEATURE_IMPORT_CHECK'] = '1'


def _get_retryable_errors():
    """懒加载可重试的异常类型，避免 PySide6 shiboken 导入冲突。"""
    from openai import RateLimitError, APITimeoutError, APIConnectionError
    return (RateLimitError, APITimeoutError, APIConnectionError)


# ── 全局限流器：默认不人为拖延；仅 429 后进入冷却 ──
class _RateLimiter:
    """自适应限流：正常调用零等待；收到 429 后冷却数秒。

    固定 min_interval 会把工作流每一步都拖慢（曾设 1.2s，串行 100 章白等 15 分钟+）。
    用 threading.Lock：调用方各自 new_event_loop，asyncio.Lock 跨 loop 会死锁。
    """

    def __init__(self, min_interval: float = 0.15):
        self._lock = threading.Lock()
        self._last_request_time: float = 0.0
        self._min_interval = min_interval
        self._cooldown_until: float = 0.0

    async def acquire(self):
        """等到可以发请求：正常只隔 min_interval；限流冷却中则等到冷却结束。"""
        while True:
            with self._lock:
                now = time.monotonic()
                start = max(self._last_request_time, self._cooldown_until)
                wait = start + self._min_interval - now
                if wait <= 0:
                    self._last_request_time = now
                    return
            logger.debug("限流器：等待 %.2f 秒", wait)
            await asyncio.sleep(min(wait, 2.0))

    def penalize(self, seconds: float = 6.0):
        """429 后进入冷却，避免连续撞限流。"""
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)
        logger.warning("触发限流冷却 %.1f 秒", seconds)


_global_limiter = _RateLimiter(min_interval=0.15)

# N-01 修复：使用模块级缓存实现真正的单次懒加载
_async_openai_cls = None


def _get_async_openai():
    """懒加载 AsyncOpenAI，避免 PySide6 shiboken 导入链冲突。"""
    global _async_openai_cls
    if _async_openai_cls is None:
        from openai import AsyncOpenAI
        _async_openai_cls = AsyncOpenAI
    return _async_openai_cls


def _is_invalid_request(e: Exception) -> bool:
    """是否为参数不被接受（可去掉思考/tools 字段重试）。"""
    name = type(e).__name__
    if name in ("BadRequestError", "UnprocessableEntityError", "InvalidRequestError"):
        return True
    text = str(e).lower()
    return any(s in text for s in (
        "extra_body", "enable_thinking", "reasoning_effort", "thinking",
        "unknown field", "unrecognized", "tool_choice", "tools is not supported",
        "does not support tools", "function calling",
    ))


def _coerce_text(value) -> str:
    """兼容 content 为 str / list[parts] / dict 的几种 OpenAI 方言。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if isinstance(value, dict):
        text = value.get("text") or value.get("content")
        return text if isinstance(text, str) else ""
    return ""


def _reasoning_text(message) -> str:
    """不同厂商的思考字段：reasoning_content / reasoning / analysis / thinking。"""
    for attr in ("reasoning_content", "reasoning", "analysis", "thinking"):
        value = getattr(message, attr, None)
        if value:
            return _coerce_text(value)
    extra = getattr(message, "model_extra", None) or {}
    if isinstance(extra, dict):
        for key in ("reasoning_content", "reasoning", "analysis", "thinking"):
            value = extra.get(key)
            if value:
                return _coerce_text(value)
    return ""


def _serialize_messages(messages: list[LLMMessage]) -> list[dict]:
    """把内部消息转成 OpenAI 请求体（保留 tool_calls / tool_call_id）。"""
    out: list[dict] = []
    for m in messages:
        item: dict = {"role": m.role, "content": m.content or ""}
        if m.tool_calls:
            item["tool_calls"] = m.tool_calls
            if not m.content:
                item["content"] = None
        if m.tool_call_id:
            item["tool_call_id"] = m.tool_call_id
        out.append(item)
    return out


def _as_arguments(value) -> str:
    """OpenAI 协议要求 arguments 是 JSON 字符串。"""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def _serialize_tool_calls(raw_calls) -> list[dict]:
    """把 SDK 的 tool_calls 规整成可回填历史的 dict。"""
    calls: list[dict] = []
    for i, tc in enumerate(raw_calls or []):
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            calls.append({
                "id": tc.get("id") or f"call_{i}",
                "type": "function",
                "function": {
                    "name": fn.get("name", ""),
                    "arguments": _as_arguments(fn.get("arguments", "{}")),
                },
            })
            continue
        fn = getattr(tc, "function", None)
        calls.append({
            "id": getattr(tc, "id", "") or f"call_{i}",
            "type": "function",
            "function": {
                "name": getattr(fn, "name", "") if fn else "",
                "arguments": _as_arguments(getattr(fn, "arguments", "{}") if fn else "{}"),
            },
        })
    return calls


def _delta_parts(delta) -> tuple[str, str]:
    """从流式/非流式 delta 提取 (kind, text)。

    content 是正式回答；reasoning_content/reasoning 是思考过程。
    不包任何标签，由 UI 按 kind 着色——标签既脏 UI 又浪费 token。
    """
    content = _coerce_text(getattr(delta, "content", None))
    if content:
        return "text", content
    reasoning = _reasoning_text(delta)
    return ("thinking", reasoning) if reasoning else ("text", "")


def _message_parts(message) -> tuple[str, str]:
    """非流式：拆 (thinking, text)。

    思考字段绝不升格为正文：只有 reasoning 的响应应由上层判空/重试，
    不能把“思考过程”写进大纲或章节。
    """
    content = _coerce_text(getattr(message, "content", None))
    thinking = _reasoning_text(message)
    return thinking, content


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
        name = (model or "").lower()
        url = (base_url or "").lower()
        # MiMo 的流式只推 reasoning_content，正文要到非流式 final 才有；
        # 工作流正文若强行流式会拿到空内容，因此标记为不支持正文流式。
        self.supports_content_stream = not ("mimo" in name or "milai.com" in url)
        logger.info("LLM 客户端初始化: model=%s, base_url=%s, stream_text=%s",
                    model, base_url, self.supports_content_stream)

    def _thinking_body(self, thinking: ThinkingMode) -> dict:
        """按模型方言构造思考字段；支持配置覆盖。"""
        return build_thinking_body(
            thinking,
            model=self.model,
            base_url=self.base_url,
            dialect=self.extra.get("thinking_dialect"),
            off_body=parse_json_body(self.extra.get("thinking_off_body")),
            on_body=parse_json_body(self.extra.get("thinking_on_body")),
        )

    def _build_client(self):
        AsyncOpenAI = _get_async_openai()
        # 用 certifi 证书路径创建 httpx 客户端，避免 Windows 系统证书库卡顿
        # 超时显式设为 300s：httpx 默认 5s 对 LLM 生成太短，会触发无谓重试
        import ssl
        import httpx, certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        http_client = httpx.AsyncClient(verify=ssl_ctx, timeout=httpx.Timeout(300.0, connect=15.0))
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
        thinking: ThinkingMode = "auto",
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        self._sync_loop()
        last_exc: Exception | None = None
        extra = self._thinking_body(thinking)
        for attempt in range(max_retries + 1):
            try:
                await _global_limiter.acquire()
                create_kwargs: dict = dict(
                    model=self.model,
                    messages=_serialize_messages(messages),
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if extra:
                    create_kwargs["extra_body"] = extra
                if tools:
                    create_kwargs["tools"] = tools
                    create_kwargs["tool_choice"] = "auto"
                try:
                    resp = await self.client.chat.completions.create(**create_kwargs)
                except Exception as e:
                    if attempt < max_retries and _is_invalid_request(e):
                        # 思考字段不被该 API 认可时降级重试（不带 extra_body）
                        if extra:
                            logger.warning("思考开关被 %s 拒绝，去掉后重试: %s", self.base_url, e)
                            extra = {}
                            continue
                        # 模型/网关不支持工具调用：退化为纯文本一次性调用
                        if tools:
                            logger.warning("工具调用被 %s 拒绝，退化为纯文本: %s", self.base_url, e)
                            tools = None
                            continue
                    raise
                choice = resp.choices[0]
                _think, content = _message_parts(choice.message)
                return LLMResponse(
                    content=content,
                    model=resp.model,
                    usage={
                        "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                        "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
                    },
                    tool_calls=_serialize_tool_calls(getattr(choice.message, "tool_calls", None)),
                    finish_reason=getattr(choice, "finish_reason", "") or "",
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
                    _global_limiter.penalize(base_wait)  # 冷却，别再怼
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
        thinking: ThinkingMode = "auto",
        tools: list[dict] | None = None,
    ) -> AsyncIterator[tuple[str, str]]:
        # 流式 + 工具调用语义复杂，自由对话保持纯文本；工作流工具走 chat()
        self._sync_loop()
        last_exc: Exception | None = None
        extra = self._thinking_body(thinking)
        for attempt in range(max_retries + 1):
            yielded_any = False
            try:
                await _global_limiter.acquire()
                create_kwargs: dict = dict(
                    model=self.model,
                    messages=_serialize_messages(messages),
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                )
                if extra:
                    create_kwargs["extra_body"] = extra
                try:
                    stream = await self.client.chat.completions.create(**create_kwargs)
                except Exception as e:
                    if extra and not yielded_any and _is_invalid_request(e):
                        logger.warning("思考开关被 %s 拒绝（流式），去掉后重试: %s", self.base_url, e)
                        extra = {}
                        continue
                    raise
                try:
                    async for chunk in stream:
                        try:
                            if chunk.choices and chunk.choices[0].delta:
                                kind, content = _delta_parts(chunk.choices[0].delta)
                                if content:
                                    yielded_any = True
                                    yield kind, content
                        except (AttributeError, IndexError):
                            logger.debug("跳过空的流式响应 chunk")
                            continue
                finally:
                    # 必须 await 关流：openai AsyncStream.close() 是协程，
                    # 不 await 会留下 Response.aiter_bytes 幽灵 aclose
                    try:
                        await stream.close()
                    except Exception as e:
                        logger.debug("关闭流式连接异常（忽略）: %s", e)
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
                    _global_limiter.penalize(base_wait)  # 冷却，别再怼
                wait = base_wait + random.uniform(0, 1)
                logger.warning("流式 LLM 调用遇到可重试错误 (%s)，第 %d 次重试，等待 %.1f 秒...", e, attempt + 1, wait)
                await asyncio.sleep(wait)
        raise last_exc  # type: ignore[misc]
