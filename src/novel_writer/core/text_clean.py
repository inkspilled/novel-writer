"""清理模型输出中混入的思考过程。

有些 OpenAI 兼容模型即使 thinking=off，仍会把英文 reasoning 放进 content，
或把 reasoning 和最终文档拼在一起。写盘前统一剥掉：
- <thought>/<thinking>/<reasoning> 标签块；
- 第一个 Markdown 标题之前的英文/元认知前缀；
- 没有标题时，开头的 “The user wants / Let me / I need...” 段落。
JSON 输出不动，交给 extract_json_object 处理。
"""
from __future__ import annotations

import re

_TAG_RE = re.compile(
    r"<(thought|thinking|reasoning|analysis)>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_META_RE = re.compile(
    r"^\s*(The user|Let me|I need|I should|I will|I'll|We need|We should|"
    r"Okay|Sure|Here'?s|Here is|Based on|First,|First of all|"
    r"As an|To (build|design|create|write)|This (is|outline|world)|"
    r"The (outline|story|world|user)|Now I|Next,|Finally,|"
    r"用户(要求|需要|想要)|我(需要|应该|先|来)|让我|首先|好的)",
    re.IGNORECASE,
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_META_ANY_RE = re.compile(
    r"(正文.*(不可|无法)(读取|获取)|我将以|作为唯一剧情依据|暂不可读|"
    r"上一章.*(缺失|不可读)|由于.*(缺失|不可读)|为了保证.*衔接)",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r"(?m)^#{1,6}\s+\S")


def _ascii_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isascii()) / len(letters)


def _looks_like_reasoning(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    if _META_RE.match(text):
        return True
    # 大段英文、且不是中文正文
    return _ascii_ratio(text) > 0.45 and not _CJK_RE.search(text)


def clean_model_text(text: str, step_id: str = "") -> str:
    """返回剥掉思考前缀的正文；已经是 JSON 的内容原样返回。"""
    if not text:
        return ""
    text = text.replace("\ufeff", "").strip()
    text = _TAG_RE.sub("", text).strip()
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        return text

    # 1) 有 Markdown 标题：丢掉标题前的 reasoning
    match = _HEADING_RE.search(text)
    if match and match.start() > 0:
        prefix = text[: match.start()]
        if _looks_like_reasoning(prefix) or _META_ANY_RE.search(prefix):
            text = text[match.start():].lstrip()

    # 2) 没有标题 / 标题前还有英文：逐行丢掉元认知开头
    lines = text.splitlines()
    while lines:
        first = lines[0].strip()
        if not first:
            lines.pop(0)
            continue
        if _META_RE.match(first) or (
            _ascii_ratio(first) > 0.55 and not _CJK_RE.search(first)
        ):
            lines.pop(0)
            continue
        break
    text = "\n".join(lines).strip()

    # 3) 去掉模型常见的结尾客套
    text = re.sub(
        r"\n+(?:(?:Let me know|I hope this helps|This should|以上是|希望对你有帮助).*)$",
        "", text, flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    return text
