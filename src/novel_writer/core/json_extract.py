"""从 LLM 文本中稳健提取 JSON 对象。

思考模型常把 JSON 包在闲聊、Markdown 代码块或 <think>…</think> 里，
简单 r"\\{[\\s\\S]+\\}" 会匹配到残缺对象导致解析失败。
"""
from __future__ import annotations

import json
import re

from .logger import get_logger

logger = get_logger(__name__)

# 剥离 think 标签与 Ollama 思考前缀（用 unicode 转义避免源码被二次解析）
_THINK_OPEN = "\u003cthink\u003e"
_THINK_CLOSE = "\u003c/think\u003e"
_THINK_RE = re.compile(
    _THINK_OPEN + r"[\s\S]*?" + _THINK_CLOSE
    + r"|\[思考过程\][\s\S]*?(?=\[回答\]|$)"
)
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def _strip_noise(text: str) -> str:
    text = _THINK_RE.sub("", text)
    fences = _CODE_FENCE_RE.findall(text)
    if fences:
        # 优先取代码块内容（思考模型常把 JSON 放进 ```json）
        text = "\n".join(fences)
    return text.strip()


def _find_balanced_objects(text: str) -> list[str]:
    """按花括号配对提取候选 JSON 对象（跳过字符串内的括号）。"""
    objects: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    objects.append(text[start : i + 1])
                    start = -1
    return objects


def _try_load(candidate: str) -> dict | None:
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    # 容错：去掉尾逗号 / 单引号等常见模型笔误
    cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
    cleaned = cleaned.replace("'", '"')
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def extract_json_object(text: str) -> dict | None:
    """从模型输出提取 JSON 对象；失败返回 None。"""
    if not text or not text.strip():
        return None
    cleaned = _strip_noise(text)

    # 整段就是 JSON
    direct = _try_load(cleaned)
    if direct is not None:
        return direct
    logger.debug("JSON 非整段可解析，走括号匹配: len=%d", len(cleaned))

    for obj in _find_balanced_objects(cleaned):
        data = _try_load(obj)
        if data is not None:
            return data

    # 最后一搏：在原始文本里找（未剥噪声可能误伤，但比直接失败好）
    for obj in _find_balanced_objects(text):
        data = _try_load(obj)
        if data is not None:
            return data
    logger.debug("JSON 提取失败（预期回退路径）: len=%d preview=%r", len(text or ""), (text or "")[:80])
    return None
