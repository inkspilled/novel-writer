"""Embedding 客户端 — OpenAI 兼容 /embeddings 接口的同步封装。

语义检索（RAG 向量路径）使用。独立于异步 LLMClient：
- 检索发生在 ContextBuilder.build 的同步路径里（工作流线程内，短阻塞不影响 UI）；
- 用同步 SDK + 短超时 + 少重试，失败由调用方（RAGRetriever）回退 BM25。
"""
from __future__ import annotations

import os

from ..logger import get_logger

logger = get_logger(__name__)

_sync_openai_cls = None


def _get_sync_openai():
    """懒加载同步 OpenAI，避免 PySide6 shiboken 导入链冲突（同 llm/client.py）。"""
    global _sync_openai_cls
    if _sync_openai_cls is None:
        os.environ.setdefault("SHIBOKEN_DISABLE_FEATURE_IMPORT_CHECK", "1")
        from openai import OpenAI
        _sync_openai_cls = OpenAI
    return _sync_openai_cls


class EmbeddingClient:
    """OpenAI 兼容 embeddings 客户端（同步）。

    base_url/api_key 通常与主模型相同（如硅基流动、ModelScope、one-api 网关），
    但部分供应商（如 DeepSeek）不提供 embeddings 端点，因此配置独立存放。
    """

    def __init__(self, model: str, api_key: str = "", base_url: str = ""):
        import httpx
        import certifi

        OpenAI = _get_sync_openai()
        http_client = httpx.Client(
            verify=certifi.where(),
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        self.model = model
        self._client = OpenAI(
            api_key=api_key or "none",
            base_url=base_url or None,
            max_retries=1,
            http_client=http_client,
        )
        logger.info("Embedding 客户端初始化: model=%s, base_url=%s", model, base_url)

    def close(self) -> None:
        """关闭底层 HTTP 连接（配置变更重建客户端时必须调用，否则泄漏）。"""
        try:
            self._client.close()
        except Exception as e:
            logger.debug("Embedding 客户端 close 异常（忽略）: %s", e)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化。单次调用不宜超过 ~64 条（多数网关的批量上限）。"""
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in resp.data]
