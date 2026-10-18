from src.novel_writer.core.llm.thinking import build_thinking_body


def test_mimo_off_disables_reasoning_effort():
    body = build_thinking_body("off", model="mimo-v2.6-flash",
                               base_url="https://token-plan-cn.xiaomimimo.com/v1")
    assert body["enable_thinking"] is False
    assert body["reasoning_effort"] == "none"


def test_deepseek_off_uses_reasoning_effort_none():
    body = build_thinking_body("off", model="deepseek-chat",
                               base_url="https://api.deepseek.com/v1")
    assert body["reasoning_effort"] == "none"
