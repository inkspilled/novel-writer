from src.novel_writer.core.llm.client import LLMClient


def test_mimo_disables_content_stream():
    c = LLMClient("mimo-v2.6-flash", "k", "https://token-plan-cn.xiaomimimo.com/v1")
    assert c.supports_content_stream is False
    c2 = LLMClient("deepseek-chat", "k", "https://api.deepseek.com/v1")
    assert c2.supports_content_stream is True
