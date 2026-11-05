"""重排序客户端 — 对召回候选做二次打分，把真正符合查询意图的块排到前面。

小说检索的典型问题：问「主角第一次用剑」，向量/BM25 会召回一堆带「剑」的段落，
但真正描写初次拔剑的那段可能排在第 8 位。重排序模型（Qwen3-Reranker / BGE /
Cohere Rerank 等，模型名由配置决定，不写死）同时看查询和候选全文，能把这部分
意图差异区分开。

兼容常见的三类 ``/rerank`` 协议（配置项只有 model / base_url / api_key）：

- Cohere / Jina / vLLM / Xinference：``{model, query, documents, top_n}``
- HuggingFace TEI：``{query, texts}``（首次被 4xx 拒绝时自动换这套 payload）
- DashScope 原生：``{model, input: {query, documents}, parameters: {top_n}}``

任何失败都返回空列表，由 ``RAGRetriever`` 保留召回阶段的排序，不影响写作流程。
"""
from __future__ import annotations

from .logger import get_logger

logger = get_logger(__name__)

# 应用级配置（UI 设置后经 configure_rerank 注入）
_RERANK_CFG: dict | None = None


def configure_rerank(cfg: dict | None) -> None:
    """注入重排序配置（{model, api_key, base_url}）；None/无 model = 不启用。"""
    global _RERANK_CFG
    _RERANK_CFG = dict(cfg) if cfg and cfg.get("model") else None


def rerank_configured() -> bool:
    return bool(_RERANK_CFG and _RERANK_CFG.get("model"))


def rerank_config() -> dict | None:
    """返回当前重排序配置的副本（未配置为 None）。"""
    return dict(_RERANK_CFG) if _RERANK_CFG else None


def _endpoint(base_url: str) -> str:
    """把用户填的 API 地址规范成 rerank 端点。

    - 已经带 ``rerank`` 的完整地址：原样使用；
    - DashScope：补 ``/services/rerank/text-rerank/text-rerank``；
    - 其余（Cohere/Jina/vLLM/TEI）：补 ``/rerank``（``/v1`` 结尾不重复加 v1）。
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    lower = base.lower()
    if "rerank" in lower:
        return base
    if "dashscope.aliyuncs.com" in lower:
        return base + "/services/rerank/text-rerank/text-rerank"
    return base + "/rerank"


def _iter_rows(data) -> list[dict]:
    """从各协议不同的响应里找结果数组。"""
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("results", "data"):
        rows = data.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    output = data.get("output")
    if isinstance(output, dict):
        for key in ("results", "data"):
            rows = output.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def parse_rerank_response(data) -> list[tuple[int, float]]:
    """解析 ``[{index, relevance_score|score}]``，按分数降序返回。"""
    parsed: list[tuple[int, float]] = []
    for row in _iter_rows(data):
        index = row.get("index")
        score = row.get("relevance_score", row.get("score", row.get("relevance")))
        if index is None or score is None:
            continue
        try:
            parsed.append((int(index), float(score)))
        except (TypeError, ValueError):
            continue
    parsed.sort(key=lambda item: item[1], reverse=True)
    return parsed


class RerankClient:
    """同步 HTTP 客户端（检索发生在工作流线程内，短阻塞可接受）。"""

    def __init__(self, model: str, api_key: str = "", base_url: str = ""):
        import ssl

        import certifi
        import httpx

        self.model = model
        self.endpoint = _endpoint(base_url)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            verify=ssl.create_default_context(cafile=certifi.where()),
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers=headers,
        )
        logger.info("Rerank 客户端初始化: model=%s, endpoint=%s", model, self.endpoint)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception as e:  # pragma: no cover - close 失败不影响检索
            logger.debug("Rerank 客户端 close 异常（忽略）: %s", e)

    def _payload(self, query: str, documents: list[str], top_n: int) -> dict:
        if "text-rerank" in self.endpoint or "/services/rerank" in self.endpoint:
            return {
                "model": self.model,
                "input": {"query": query, "documents": documents},
                "parameters": {"top_n": top_n, "return_documents": False},
            }
        return {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
        }

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
    ) -> list[tuple[int, float]]:
        """返回 ``(doc_index, score)`` 降序列表；失败抛异常，由调用方兜底。"""
        if not documents or not self.endpoint:
            return []
        top_n = max(1, min(top_n or len(documents), len(documents)))
        response = self._client.post(
            self.endpoint,
            json=self._payload(query, documents, top_n),
        )
        if response.status_code in (400, 422):
            # TEI 只认 texts；部分网关不接受 top_n/return_documents
            response = self._client.post(
                self.endpoint,
                json={"query": query, "texts": documents, "truncate": True},
            )
        response.raise_for_status()
        return parse_rerank_response(response.json())[:top_n]
