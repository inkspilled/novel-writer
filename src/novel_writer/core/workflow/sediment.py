"""写后沉淀 — 从章节正文中提取记忆项、伏笔与追读力数据。"""
from __future__ import annotations

import re

from ..logger import get_logger
from ..memory import MemoryItem, MemoryScratchpad

logger = get_logger(__name__)


def extract_chapter_title(content: str, n: int) -> str:
    """从章节正文中提取标题（第一个 Markdown 标题）。"""
    for line in content.split("\n"):
        line = line.strip()
        m = re.match(r"^#{1,3}\s+(.+)$", line)
        if m:
            title = m.group(1).strip()
            # 清理标题中的特殊字符
            title = re.sub(r"[*_`#\[\]()]", "", title).strip()
            if title:
                return title[:50]  # 限制长度
    return f"第{n}章"


def sediment_chapter(project_dir, chapter: int, content: str):
    """写后沉淀：从章节正文中提取记忆项 + 追读力分析。"""
    mem = MemoryScratchpad(project_dir)

    # 提取可能的状态变化（X突破了/晋升为/受伤了/死了）
    state_patterns = [
        (r"(\w+)(突破|晋升|升级|进阶)", "实力变化"),
        (r"(\w+)(受伤|重伤|濒死|死亡|陨落)", "身体状态"),
        (r"(\w+)(到达|来到|离开|前往|回到)", "位置变化"),
    ]
    for pat, field_name in state_patterns:
        for m in re.finditer(pat, content):
            subj = m.group(1)
            if len(subj) >= 2 and len(subj) <= 6:
                mem.upsert(MemoryItem(
                    category="character_state",
                    subject=subj,
                    aspect=field_name,
                    value=m.group(0),
                    source_chapter=chapter,
                ))

    # 提取伏笔关键词（如果/竟然/原来/没想到）
    foreshadow_patterns = [
        r"(?:竟然|居然|原来|没想到|殊不知)([^。，！？]{5,40})",
        r"(?:如果|倘若|万一)([^。，！？]{5,40})",
    ]
    for pat in foreshadow_patterns:
        for m in re.finditer(pat, content):
            val = m.group(0)[:60]
            mem.upsert(MemoryItem(
                category="open_loops",
                subject=f"第{chapter}章伏笔",
                aspect="悬念",
                value=val,
                source_chapter=chapter,
                payload={"urgency": 0.6},
            ))

    # 记录章节事件
    title = extract_chapter_title(content, chapter)
    first_para = content.split("\n\n")[0][:100] if content else ""
    mem.upsert(MemoryItem(
        category="story_facts",
        subject=f"第{chapter}章 {title}",
        aspect="剧情",
        value=first_para,
        source_chapter=chapter,
    ))

    # 四维追踪：资源/情感/信息边界/支线
    try:
        from ..chapter_tracker import track_all
        track_all(mem, chapter, content)
    except Exception as e:
        logger.debug("章节四维追踪失败: %s", e)

    mem.compact()
    mem.save()

    # 追读力分析
    try:
        from ..reading_power import ReadingPowerTracker
        rp_tracker = ReadingPowerTracker(project_dir)
        rp = rp_tracker.analyze_chapter(content, chapter)
        rp_tracker.record(rp)
    except Exception as e:
        logger.debug("追读力分析失败: %s", e)
