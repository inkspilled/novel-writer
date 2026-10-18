from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import BaseLLM, LLMMessage, LLMResponse, ThinkingMode
from ..logger import get_logger

logger = get_logger(__name__)


def _extract_content(data: dict) -> str:
    """从 Ollama 响应中提取正式回答。

    优先 content；为空才回退 thinking。思考过程不再拼进正文——
    否则会污染历史/落盘文件，并白白计入后续调用的上下文。
    """
    msg = data.get("message", {})
    content = msg.get("content", "").strip()
    if content:
        return content
    thinking = msg.get("thinking", "").strip()
    if thinking:
        logger.warning("模型未返回正式回答，回退使用思考过程内容")
        return thinking
    return ""


def _ollama_options(thinking: ThinkingMode) -> dict:
    """Ollama 的 think 开关：off/on 显式下发，auto 不传。"""
    if thinking == "off":
        return {"think": False}
    if thinking == "on":
        return {"think": True}
    return {}


class OllamaLLM(BaseLLM):
    """Ollama 本地模型接口。"""

    def __init__(self, model: str = "qwen2.5", base_url: str = "http://localhost:11434", **kwargs):
        super().__init__(model, api_key="", base_url=base_url, **kwargs)
        self.base = base_url.rstrip("/")
        # 创建共享的 HTTP 客户端，避免每次调用都创建新连接
        timeout = httpx.Timeout(600.0, connect=10.0)
        self._client = httpx.AsyncClient(timeout=timeout)
        logger.info("Ollama LLM 初始化: model=%s, base_url=%s", model, base_url)

    async def __aenter__(self):
        """支持 async with 语法。"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出时关闭客户端。"""
        await self.close()

    async def close(self):
        """关闭 HTTP 客户端连接。"""
        if self._client:
            await self._client.aclose()
            logger.debug("Ollama HTTP 客户端已关闭")

    def _build_payload(
        self,
        messages: list[LLMMessage],
        temperature: float,
        max_tokens: int,
        stream: bool,
        thinking: ThinkingMode,
    ) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": stream,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        payload.update(_ollama_options(thinking))
        return payload

    async def chat(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking: ThinkingMode = "auto",
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """使用共享客户端进行 HTTP 调用，避免频繁创建连接。"""
        try:
            resp = await self._client.post(
                f"{self.base}/api/chat",
                json=self._build_payload(messages, temperature, max_tokens, False, thinking),
            )
            resp.raise_for_status()
            data = resp.json()
            return LLMResponse(
                content=_extract_content(data),
                model=self.model,
                usage={
                    "prompt_tokens": data.get("prompt_eval_count", 0),
                    "completion_tokens": data.get("eval_count", 0),
                },
            )
        except httpx.HTTPStatusError as e:
            logger.error("Ollama API 调用失败: %s, model=%s", e, self.model)
            raise
        except json.JSONDecodeError as e:
            logger.error("Ollama 响应 JSON 解析失败: %s", e)
            raise

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking: ThinkingMode = "auto",
        tools: list[dict] | None = None,
    ) -> AsyncIterator[tuple[str, str]]:
        """流式输出，逐块返回 (kind, text)。"""
        try:
            async with self._client.stream(
                "POST",
                f"{self.base}/api/chat",
                json=self._build_payload(messages, temperature, max_tokens, True, thinking),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            msg = data.get("message", {}) or {}
                            content = (msg.get("content") or "").strip()
                            thinking_txt = (msg.get("thinking") or "").strip()
                            if thinking_txt:
                                yield "thinking", thinking_txt
                            if content:
                                yield "text", content
                        except json.JSONDecodeError as e:
                            logger.warning("流式响应 JSON 解析失败，跳过此行: %s", e)
                            continue
        except httpx.HTTPStatusError as e:
            logger.error("Ollama 流式 API 调用失败: %s, model=%s", e, self.model)
            raise
