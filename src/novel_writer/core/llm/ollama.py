from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import BaseLLM, LLMMessage, LLMResponse
from ..logger import get_logger

logger = get_logger(__name__)


def _extract_content(data: dict) -> str:
    """从 Ollama 响应中提取内容，兼容思考模型（如 qwen3.5）。"""
    msg = data.get("message", {})
    content = msg.get("content", "").strip()
    thinking = msg.get("thinking", "").strip()
    # 如果 content 为空但有 thinking，返回 thinking
    if not content and thinking:
        return thinking
    # 如果都有，拼接
    if content and thinking:
        return f"[思考过程]\n{thinking}\n\n[回答]\n{content}"
    return content


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

    async def chat(self, messages: list[LLMMessage], temperature: float = 0.7, max_tokens: int = 4096) -> LLMResponse:
        """使用共享客户端进行 HTTP 调用，避免频繁创建连接。"""
        try:
            resp = await self._client.post(
                f"{self.base}/api/chat",
                json={
                    "model": self.model,
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "stream": False,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                },
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
        self, messages: list[LLMMessage], temperature: float = 0.7, max_tokens: int = 4096
    ) -> AsyncIterator[str]:
        """流式输出，使用共享客户端。"""
        try:
            async with self._client.stream(
                "POST",
                f"{self.base}/api/chat",
                json={
                    "model": self.model,
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "stream": True,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            # M-02 修复：使用统一的 _extract_content 处理思考内容
                            text = _extract_content(data)
                            if text:
                                yield text
                        except json.JSONDecodeError as e:
                            logger.warning("流式响应 JSON 解析失败，跳过此行: %s", e)
                            continue
        except httpx.HTTPStatusError as e:
            logger.error("Ollama 流式 API 调用失败: %s, model=%s", e, self.model)
            raise
