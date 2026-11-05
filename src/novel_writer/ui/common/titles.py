"""章节标题显示格式化。

模型/FixTitles 产出的标题经常自带「第四十章：」前缀，直接拼成
「第40章 第四十章：第九人来过」会重复。这里只处理**展示层**，不改文件名、
不改 meta，避免影响章节正则与导出。
"""
from __future__ import annotations

import re

# 「第40章：」「第四十章 · 」「第 12 章、」等前缀
_CHAPTER_PREFIX_RE = re.compile(
    r"^\s*第\s*[0-9零〇一二三四五六七八九十百千两]+\s*章\s*[:：、.．\-—·\s]*"
)


def clean_chapter_title(title: str) -> str:
    """去掉标题里的「第X章」前缀。

    只有前缀（如 title 就是「第40章」）时返回空串，表示「没有题名」，
    避免展示成「第40章 · 第40章」。
    """
    text = (title or "").strip()
    if not text:
        return ""
    stripped = _CHAPTER_PREFIX_RE.sub("", text, count=1).strip()
    if stripped:
        return stripped
    return "" if _CHAPTER_PREFIX_RE.match(text) else text


def chapter_display_title(number: int, title: str) -> str:
    """统一显示为「第40章 · 第九人来过」；题名缺失时只显示章号。"""
    clean = clean_chapter_title(title)
    if not clean:
        return f"第{number}章"
    return f"第{number}章 · {clean}"
