"""不同厂商的 content/reasoning 字段归一化。"""
from src.novel_writer.core.llm.client import _message_parts


class Msg:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_content_list_and_reasoning_content():
    msg = Msg(content=[{"type": "text", "text": "正文"}], reasoning_content="思考")
    thinking, text = _message_parts(msg)
    assert thinking == "思考"
    assert text == "正文"


def test_analysis_only_is_not_promoted_to_content():
    msg = Msg(content="", analysis="只有分析")
    thinking, text = _message_parts(msg)
    assert thinking == "只有分析"
    assert text == ""


def test_model_extra_reasoning():
    msg = Msg(content="正文", model_extra={"reasoning": "extra 思考"})
    thinking, text = _message_parts(msg)
    assert thinking == "extra 思考"
    assert text == "正文"
