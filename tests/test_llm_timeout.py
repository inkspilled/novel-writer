"""LLM 调用硬超时回归：HTTP 200 后服务端不吐字时不能无限挂住。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.novel_writer.core.llm import client as client_mod
from src.novel_writer.core.llm.base import LLMMessage
from src.novel_writer.core.llm.client import LLMClient


def _stub_llm() -> LLMClient:
    llm = LLMClient(model="stub", api_key="k", base_url="http://127.0.0.1:1/v1")
    return llm


def test_chat_hard_timeout_raises(monkeypatch):
    llm = _stub_llm()

    class SlowCompletions:
        async def create(self, **kwargs):
            await asyncio.sleep(10)

    class StubClient:
        class chat:
            completions = SlowCompletions()

    async def run():
        llm._bound_loop = asyncio.get_running_loop()
        llm.client = StubClient()
        with pytest.raises(asyncio.TimeoutError):
            await llm.chat([LLMMessage(role="user", content="hi")], max_retries=0)

    monkeypatch.setattr(client_mod, "LLM_REQUEST_TIMEOUT", 0.02)
    asyncio.run(run())


def test_stream_idle_timeout_raises(monkeypatch):
    llm = _stub_llm()

    class FakeStream:
        def __init__(self):
            self.sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.sent:
                self.sent = True
                return SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="第一段"))]
                )
            await asyncio.sleep(10)

        async def close(self):
            return None

    class StubCompletions:
        async def create(self, **kwargs):
            return FakeStream()

    class StubClient:
        class chat:
            completions = StubCompletions()

    async def run():
        llm._bound_loop = asyncio.get_running_loop()
        llm.client = StubClient()
        with pytest.raises(asyncio.TimeoutError):
            async for _ in llm.stream_chat(
                [LLMMessage(role="user", content="hi")], max_retries=0
            ):
                pass

    monkeypatch.setattr(client_mod, "LLM_STREAM_IDLE_TIMEOUT", 0.02)
    asyncio.run(run())


def test_embedding_hard_timeout(monkeypatch):
    """embedding 服务挂住时必须 fail-fast，让 RAG 回退 BM25。"""
    import time as _time

    from src.novel_writer.core import embeddings as emb_mod

    client = emb_mod.EmbeddingClient(model="stub", api_key="k", base_url="http://127.0.0.1:1/v1")

    class SlowEmbeddings:
        @staticmethod
        def create(**kwargs):
            _time.sleep(5)

    class StubClient:
        embeddings = SlowEmbeddings()

    client._client = StubClient()
    monkeypatch.setattr(emb_mod, "EMBED_TIMEOUT", 0.02)
    with pytest.raises(TimeoutError):
        client.embed(["测试"])
