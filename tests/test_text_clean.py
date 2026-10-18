"""模型思考过程清理。"""
from src.novel_writer.core.text_clean import clean_model_text


def test_strips_reasoning_before_markdown_heading():
    raw = (
        "The user wants a complete outline for a 500-chapter novel.\n\n"
        "Let me design the volumes first. I need to keep it consistent.\n\n"
        "# 《测试》大纲\n\n## 卷一\n内容。"
    )
    assert clean_model_text(raw, "outline").startswith("# 《测试》大纲")
    assert "The user wants" not in clean_model_text(raw, "outline")


def test_strips_english_prefix_without_heading():
    raw = "I need to think about this.\nThe user wants a chapter.\n\n顾言醒来，发现自己躺在药圃里。"
    assert clean_model_text(raw, "chapter").startswith("顾言醒来")


def test_keeps_json_untouched():
    raw = '{"scene":"药圃","events":[]}'
    assert clean_model_text(raw, "percept_extract") == raw


def test_long_output_auto_continues_on_length():
    import asyncio
    from src.novel_writer.core.agents.base import AgentConfig, BaseAgent
    from src.novel_writer.core.llm.base import BaseLLM, LLMResponse

    class FakeLLM(BaseLLM):
        def __init__(self):
            super().__init__(model="fake")
            self.responses = [
                LLMResponse(content="第一段，", finish_reason="length"),
                LLMResponse(content="第二段。", finish_reason="stop"),
            ]
            self.calls = 0

        async def chat(self, messages, temperature=0.7, max_tokens=4096,
                       thinking="auto", tools=None):
            self.calls += 1
            return self.responses.pop(0)

        async def stream_chat(self, *a, **k):
            if False:
                yield "", ""

    llm = FakeLLM()
    agent = BaseAgent(AgentConfig(name="a", role="a", title="a", system_prompt="s"), llm)
    resp = asyncio.run(agent.run("写长文", max_tokens=8192))
    assert resp.content == "第一段，第二段。"
    assert llm.calls == 2
