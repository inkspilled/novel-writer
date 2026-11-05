from src.novel_writer.core.llm.client import LLMClient


def test_all_openai_compatible_models_can_stream_content():
    c = LLMClient("mimo-v2.6-flash", "k", "https://token-plan-cn.xiaomimimo.com/v1")
    assert c.supports_content_stream is True
    c2 = LLMClient("deepseek-chat", "k", "https://api.deepseek.com/v1")
    assert c2.supports_content_stream is True


def test_5xx_is_retryable():
    """503「Service is too busy」必须自动重试，否则整个规划步骤直接被打挂。"""
    from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

    from src.novel_writer.core.llm.client import _get_retryable_errors

    retryable = _get_retryable_errors()
    assert InternalServerError in retryable
    assert RateLimitError in retryable
    assert APITimeoutError in retryable
    assert APIConnectionError in retryable
