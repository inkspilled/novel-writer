"""思考方言映射 + JSON 稳健提取。"""
from __future__ import annotations

from novel_writer.core.json_extract import extract_json_object
from novel_writer.core.llm.thinking import (
    build_thinking_body,
    detect_dialect,
    resolve_dialect,
)


class TestThinkingDialect:
    def test_detect_by_base_url(self):
        assert detect_dialect("whatever", "https://api.milai.com/v1") == "mimo"
        assert detect_dialect("glm-4", "https://open.bigmodel.cn/api/paas/v4") == "glm"
        assert detect_dialect("deepseek-chat", "https://api.deepseek.com/v1") == "deepseek"
        assert detect_dialect("moonshot-v1", "https://api.moonshot.cn/v1") == "kimi"
        assert detect_dialect("qwen-max", "https://dashscope.aliyuncs.com/compatible-mode/v1") == "qwen"
        assert detect_dialect("gpt-4o", "https://api.openai.com/v1") == "openai"

    def test_ollama_url_wins_over_model_name(self):
        # 模型名带 qwen，但是 Ollama 本地：不走 extra_body 方言
        assert detect_dialect("qwen3:8b", "http://localhost:11434") == "none"
        assert detect_dialect("qwen3:8b", "http://127.0.0.1:11434") == "none"

    def test_mimo_payload(self):
        off = build_thinking_body("off", model="mimo-7b", base_url="https://api.milai.com/v1")
        on = build_thinking_body("on", model="mimo-7b", base_url="https://api.milai.com/v1")
        assert off == {"enable_thinking": False}
        assert on == {"enable_thinking": True}
        assert build_thinking_body("auto", model="mimo-7b", base_url="https://api.milai.com/v1") == {}

    def test_glm_payload(self):
        off = build_thinking_body("off", model="glm-4.5", base_url="https://open.bigmodel.cn/api/paas/v4")
        assert off == {"thinking": {"type": "disabled"}}

    def test_deepseek_payload(self):
        off = build_thinking_body("off", model="deepseek-chat", base_url="https://api.deepseek.com/v1")
        assert off == {"reasoning_effort": "none"}

    def test_explicit_dialect_override(self):
        off = build_thinking_body(
            "off", model="x", base_url="https://api.milai.com/v1", dialect="glm"
        )
        assert off == {"thinking": {"type": "disabled"}}
        assert resolve_dialect("x", "https://api.milai.com/v1", "glm") == "glm"

    def test_custom_body_override(self):
        off = build_thinking_body(
            "off",
            model="mimo-7b",
            base_url="https://api.milai.com/v1",
            off_body={"custom_flag": 1},
        )
        assert off == {"custom_flag": 1}


class TestJsonExtract:
    def test_plain(self):
        assert extract_json_object('{"a": 1}') == {"a": 1}

    def test_nested_and_trailing_comma(self):
        assert extract_json_object('前缀 {"a": {"b": 1},} 后缀') == {"a": {"b": 1}}

    def test_code_fence(self):
        text = '结果：\n```json\n{"x": [1, 2],}\n```'
        assert extract_json_object(text) == {"x": [1, 2]}

    def test_brace_in_string(self):
        assert extract_json_object('{"s": "a{b}c", "n": 3}') == {"s": "a{b}c", "n": 3}

    def test_no_json(self):
        assert extract_json_object("没有结构化结果") is None

    def test_think_noise(self):
        open_t, close_t = "\u003cthink\u003e", "\u003c/think\u003e"
        text = f"{open_t}先想想{close_t}输出：{{\"ok\": true}}"
        assert extract_json_object(text) == {"ok": True}
