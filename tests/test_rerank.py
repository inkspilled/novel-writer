"""重排序客户端回归测试：端点规范、请求形态、响应解析（不联网）。"""
from src.novel_writer.core.rerank import (
    RerankClient,
    _endpoint,
    configure_rerank,
    parse_rerank_response,
    rerank_configured,
)


def test_endpoint_normalization():
    assert _endpoint("http://localhost:8000/v1") == "http://localhost:8000/v1/rerank"
    assert _endpoint("https://api.jina.ai/v1/rerank/") == "https://api.jina.ai/v1/rerank"
    assert _endpoint("https://dashscope.aliyuncs.com/api/v1") == (
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    )


def test_parse_cohere_style_response():
    data = {"results": [
        {"index": 1, "relevance_score": 0.2},
        {"index": 0, "relevance_score": 0.9},
    ]}
    assert parse_rerank_response(data) == [(0, 0.9), (1, 0.2)]


def test_parse_dashscope_style_response():
    data = {"output": {"results": [
        {"index": 0, "relevance_score": 0.4},
        {"index": 2, "relevance_score": 0.8},
    ]}}
    assert parse_rerank_response(data) == [(2, 0.8), (0, 0.4)]


def test_parse_tei_style_response():
    data = [
        {"index": 0, "score": 0.3},
        {"index": 1, "score": 0.7},
    ]
    assert parse_rerank_response(data) == [(1, 0.7), (0, 0.3)]


def test_payload_for_dashscope_native_endpoint():
    client = RerankClient(
        "qwen3-reranker-0.6b",
        "k",
        "https://dashscope.aliyuncs.com/api/v1",
    )
    try:
        payload = client._payload("主角第一次用剑", ["段落一", "段落二"], 2)
    finally:
        client.close()
    assert payload["input"]["query"] == "主角第一次用剑"
    assert payload["parameters"]["top_n"] == 2
    assert "input" in payload and "query" not in payload


def test_payload_for_generic_endpoint():
    client = RerankClient("bge-reranker", "", "http://localhost:8000/v1")
    try:
        payload = client._payload("q", ["a", "b"], 2)
    finally:
        client.close()
    assert payload["model"] == "bge-reranker"
    assert payload["documents"] == ["a", "b"]
    assert payload["top_n"] == 2


def test_configure_toggle():
    configure_rerank(None)
    assert not rerank_configured()
    configure_rerank({"model": "qwen3-reranker-0.6b"})
    assert rerank_configured()
    configure_rerank(None)
