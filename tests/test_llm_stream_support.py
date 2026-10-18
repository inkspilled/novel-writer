from src.novel_writer.core.llm.client import LLMClient


def test_all_openai_compatible_models_can_stream_content():
    c = LLMClient("mimo-v2.6-flash", "k", "https://token-plan-cn.xiaomimimo.com/v1")
    assert c.supports_content_stream is True
    c2 = LLMClient("deepseek-chat", "k", "https://api.deepseek.com/v1")
    assert c2.supports_content_stream is True
