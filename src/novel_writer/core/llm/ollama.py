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
        logger.info("Ollama LLM 初始化: model=%s, base_url=%s", model, base_url)

    async def chat(self, messages: list[LLMMessage], temperature: float = 0.7, max_tokens: int = 4096) -> LLMResponse:
        # 大模型首次加载可能很慢，设 10 分钟超时
        timeout = httpx.Timeout(600.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
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

    async def stream_chat(
        self, messages: list[LLMMessage], temperature: float = 0.7, max_tokens: int = 4096
    ) -> AsyncIterator[str]:
        timeout = httpx.Timeout(600.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
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
                        data = json.loads(line)
                        msg = data.get("message", {})
                        # 优先取 content，没有则取 thinking
                        text = msg.get("content", "") or msg.get("thinking", "")
                        if text:
                            yield text
