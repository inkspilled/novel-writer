"""写后沉淀 — 从章节最终正文提取记忆项、伏笔与追读力数据。

双通道：LLM 语义提取为主（runner._llm_sediment 产出 JSON），
正则提取为兜底（无可用 Agent 或 LLM 失败时）。
"""
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


def sediment_chapter(project_dir, chapter: int, content: str, llm_data: dict | None = None):
    """写后沉淀：从章节正文（应为润色后的最终落盘文本）提取记忆项 + 追读力分析。

    双通道：
    - llm_data 非空（LLM 沉淀成功）：语义提取为主——状态变化/伏笔开关/读者承诺/
      关系/世界规则，伏笔回收按 subject 精确关闭；不跑正则状态/伏笔抽取。
    - llm_data 为空（无可用 Agent 或 LLM 失败）：回退到正则提取（噪声大但零成本）。

    章节事件、四维追踪、追读力两路共用。
    """
    mem = MemoryScratchpad(project_dir)

    if llm_data:
        apply_llm_sediment(mem, chapter, llm_data)
    else:
        _extract_state_changes(mem, chapter, content)
        _extract_foreshadows(mem, chapter, content)

    # 记录章节事件（两路共用，正则足够）
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

    # 伏笔自动回收：LLM 路径由 resolved 按 subject 精确关闭，正则检测只在兜底路径跑
    if not llm_data:
        _auto_close_loops(mem, chapter, content)

    mem.compact()
    mem.save()

    # 过期反模式清理（距今超过 10 章的低严重度项）
    try:
        from ..anti_patterns import AntiPatternTracker
        AntiPatternTracker(project_dir).clear_resolved(chapter)
    except Exception as e:
        logger.debug("反模式过期清理失败: %s", e)

    # 追读力分析
    try:
        from ..reading_power import ReadingPowerTracker
        rp_tracker = ReadingPowerTracker(project_dir)
        rp = rp_tracker.analyze_chapter(content, chapter)
        rp_tracker.record(rp)
    except Exception as e:
        logger.debug("追读力分析失败: %s", e)


def _clean(value, max_len: int) -> str:
    """防御性清洗 LLM 输出字段：非字符串/超长一律规整。"""
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_len]


def apply_llm_sediment(mem, chapter: int, data: dict):
    """把 LLM 沉淀 JSON 写入记忆桶（LLM 输出不可信，逐条防御性校验）。"""
    for item in data.get("character_state") or []:
        if not isinstance(item, dict):
            continue
        subj = _clean(item.get("subject"), 20)
        val = _clean(item.get("value"), 80)
        if not subj or not val:
            continue
        mem.upsert(MemoryItem(
            category="character_state",
            subject=subj,
            aspect=_clean(item.get("aspect"), 12) or "状态",
            value=val,
            source_chapter=chapter,
            payload={"source": "llm"},
        ))

    for i, item in enumerate(data.get("open_loops") or [], 1):
        val = _clean(item.get("value"), 60) if isinstance(item, dict) else _clean(item, 60)
        if not val:
            continue
        # subject 含片段与序号，与正则通道同约定：同章多伏笔互不覆盖
        mem.upsert(MemoryItem(
            category="open_loops",
            subject=f"第{chapter}章伏笔{i}·{val[:24]}",
            aspect="悬念",
            value=val,
            source_chapter=chapter,
            payload={"urgency": 0.6, "source": "llm"},
        ))

    for subj in data.get("resolved") or []:
        if isinstance(subj, str) and subj.strip():
            mem.close_loop(subj.strip()[:80], chapter)

    for item in data.get("reader_promises") or []:
        if not isinstance(item, dict):
            continue
        subj = _clean(item.get("subject"), 30)
        val = _clean(item.get("value"), 80)
        if not subj or not val:
            continue
        mem.upsert(MemoryItem(
            category="reader_promises",
            subject=subj,
            aspect="承诺",
            value=val,
            source_chapter=chapter,
            payload={"source": "llm"},
        ))

    for item in data.get("relationships") or []:
        if not isinstance(item, dict):
            continue
        subj = _clean(item.get("subject"), 40)
        val = _clean(item.get("value"), 80)
        if not subj or not val:
            continue
        mem.upsert(MemoryItem(
            category="relationships",
            subject=subj,
            aspect="关系",
            value=val,
            source_chapter=chapter,
            payload={"source": "llm"},
        ))

    for item in data.get("world_rules") or []:
        if not isinstance(item, dict):
            continue
        subj = _clean(item.get("subject"), 30)
        val = _clean(item.get("value"), 80)
        if not subj or not val:
            continue
        mem.upsert(MemoryItem(
            category="world_rules",
            subject=subj,
            aspect="规则",
            value=val,
            source_chapter=chapter,
            payload={"source": "llm"},
        ))


def _extract_state_changes(mem, chapter: int, content: str):
    """正则兜底：状态变化提取（噪声大，仅 LLM 沉淀不可用时使用）。"""
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


def _extract_foreshadows(mem, chapter: int, content: str):
    """正则兜底：伏笔关键词提取（噪声大，仅 LLM 沉淀不可用时使用）。"""
    foreshadow_patterns = [
        r"(?:竟然|居然|原来|没想到|殊不知)([^。，！？]{5,40})",
        r"(?:如果|倘若|万一)([^。，！？]{5,40})",
    ]
    loop_seq = 0
    for pat in foreshadow_patterns:
        for m in re.finditer(pat, content):
            val = m.group(0)[:60]
            loop_seq += 1
            # subject 含片段与序号，避免同章多伏笔在 open_loops 去重键下互相覆盖
            mem.upsert(MemoryItem(
                category="open_loops",
                subject=f"第{chapter}章伏笔{loop_seq}·{val[:24]}",
                aspect="悬念",
                value=val,
                source_chapter=chapter,
                payload={"urgency": 0.6},
            ))


# 伏笔回收信号词：出现这些词暗示之前的悬念被揭开了
_LOOP_CLOSE_SIGNALS = [
    r"原来如此", r"原来是你", r"真相(?:大白|水落石出|揭开)", r"终于明白",
    r"终于知道", r"谜底(?:揭开|揭晓|揭开)", r"答案(?:是|揭晓|浮出)",
    r"原来.{2,10}(?:就是|便是|正是)", r"竟然(?:就)?是",
    r"所谓的(.{2,10})(?:其实|不过|根本)",
]


def _auto_close_loops(mem, chapter: int, content: str):
    """伏笔自动回收检测：本章出现回收信号词时，尝试关闭相关的 open_loops。

    匹配策略：
    1. 若本章命中回收信号词 → 拿到信号词附近的关键词（名词短语）
    2. 与 open_loops 的 subject / value 做子串匹配
    3. 命中则调 close_loop 标记回收

    这是粗粒度的规则匹配，漏报可接受（伏笔在注意力尾部持续提醒），
    误报会被 close_loop 的审计链保留可追溯。
    """
    import re as _re

    # 检测回收信号词是否出现
    has_signal = False
    signal_contexts = []
    for pat in _LOOP_CLOSE_SIGNALS:
        for m in _re.finditer(pat, content):
            has_signal = True
            start = max(0, m.start() - 30)
            end = min(len(content), m.end() + 30)
            signal_contexts.append(content[start:end])

    if not has_signal:
        return

    # 取出当前活跃的未解伏笔
    loops = mem.get_active("open_loops")
    if not loops:
        return

    # 对每个伏笔，检查其 subject / value 的关键词是否出现在信号上下文中
    for item in loops:
        subject = item.get("subject", "")
        value = item.get("value", "")
        closed = False

        # 提取伏笔描述中的关键名词（2-4 字片段）
        keywords = set()
        for text in (subject, value):
            # 去掉「第N章伏笔K·」前缀，取实际描述
            clean = _re.sub(r"第\d+章伏笔\d*[·：:]", "", text).strip()
            # 提取 2-4 字中文片段作为关键词
            for kw in _re.findall(r"[\u4e00-\u9fff]{2,4}", clean):
                if kw not in ("悬念", "伏笔", "发现", "原来", "如果"):
                    keywords.add(kw)

        # 检查关键词是否在回收信号上下文中出现
        for ctx in signal_contexts:
            for kw in keywords:
                if kw in ctx:
                    closed = True
                    break
            if closed:
                break

        if closed:
            logger.debug("伏笔自动回收: 第%d章 → %s", chapter, subject)
            mem.close_loop(subject, chapter)
