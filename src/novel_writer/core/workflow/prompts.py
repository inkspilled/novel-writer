"""工作流提示词 — 节奏控制、角色锚定、内置步骤的指令模板。"""
from __future__ import annotations

import re

from ..character_sim import parse_characters


def build_pacing_context(n: int, total: int, for_update: bool = False) -> str:
    """生成节奏控制上下文，防止长篇小说节奏失控。

    Args:
        n: 当前章节号
        total: 目标总章节数
        for_update: True 时为反哺模式（强调收束），False 时为写作模式（强调节奏）
    """
    if total <= 0:
        return ""

    progress = n / total
    remaining = total - n
    pct = int(progress * 100)

    if progress < 0.05:
        phase = "开篇铺垫"
        phase_desc = "世界观初次展开阶段"
    elif progress < 0.15:
        phase = "初期建立"
        phase_desc = "核心角色登场、初始冲突建立"
    elif progress < 0.35:
        phase = "前期发展"
        phase_desc = "主线冲突逐步升级、支线铺开"
    elif progress < 0.55:
        phase = "中期推进"
        phase_desc = "核心矛盾深化、角色成长加速"
    elif progress < 0.75:
        phase = "后期高潮"
        phase_desc = "主要冲突进入白热化、伏笔回收密集期"
    elif progress < 0.90:
        phase = "收束阶段"
        phase_desc = "支线收拢、悬念解答、主线走向终局"
    else:
        phase = "终章阶段"
        phase_desc = "最终决战/核心矛盾解决、全文收尾"

    parts = [f"=== 【节奏控制 · {phase}】 ==="]
    parts.append(f"当前：第{n}章 / 共{total}章（进度{pct}%，剩余{remaining}章）")
    parts.append(f"阶段：{phase}（{phase_desc}）")
    parts.append("")

    if for_update:
        # 反哺模式：根据阶段控制更新方向
        if progress < 0.35:
            parts.append("【反哺要求】当前处于前期，可以补充支线和新伏笔，但新支线必须与主线有交汇点。")
        elif progress < 0.55:
            parts.append("【反哺要求】当前处于中期，停止新增支线。聚焦已有支线的推进和伏笔的逐步回收。")
        elif progress < 0.75:
            parts.append("【反哺要求】当前处于后期高潮，必须加速回收伏笔，不允许新增支线或新伏笔。所有已有悬念必须在剩余章节内解决。")
        else:
            parts.append("【反哺要求】当前处于收束/终章阶段，严禁新增任何支线、伏笔、新角色。只允许：1) 回收伏笔 2) 解决悬念 3) 推进主线结局。所有剧情线必须朝完结方向收拢。")
    else:
        # 写作模式：根据阶段控制写作方向
        if progress < 0.15:
            parts.append("【节奏要求】当前处于开篇阶段，重点是世界观展示和角色塑造，不要急于推进主线冲突。每章应有新信息或新角色登场，保持读者好奇心。")
        elif progress < 0.35:
            parts.append("【节奏要求】当前处于前期发展，可以适当展开支线，但每章必须推进主线至少一步。注意埋设伏笔，为后续剧情做铺垫。")
        elif progress < 0.55:
            parts.append("【节奏要求】当前处于中期推进，主线冲突必须持续升级，不要出现剧情停滞。支线应与主线交汇，避免游离于主线之外的独立剧情。")
        elif progress < 0.75:
            parts.append("【节奏要求】当前处于后期高潮，剧情密度要高，每章应有实质性转折或冲突。开始回收伏笔，不要新开支线。注意控制信息量，避免赶进度式写作。")
        elif progress < 0.90:
            parts.append("【节奏要求】当前处于收束阶段，所有支线必须收拢，伏笔必须回收。不要引入新角色或新冲突。剧情应朝结局稳步推进，每章都有明确的收束目标。")
        else:
            parts.append("【节奏要求】当前处于终章阶段，本章必须推进到核心矛盾的解决。不要拖延节奏，不要插入回忆或支线，直接推进主线结局。")

    parts.append(f"【重要】本小说目标{total}章，当前是第{n}章，还剩{remaining}章。请严格按照当前阶段的要求写作，不要提前进入下一阶段。")
    return "\n".join(parts)


def build_character_constraint(char_content: str) -> str:
    """从人物设定内容中提取角色信息，生成写作约束指令。

    角色解析统一走 character_sim.parse_characters（跳过名单唯一来源）。
    """
    profiles = parse_characters(char_content)
    if not profiles:
        return ""

    characters = [p.name for p in profiles]
    protagonist = next((p.name for p in profiles if p.is_protagonist), "")
    # 未显式标记主角时取第一个角色
    if not protagonist:
        protagonist = characters[0]

    parts = ["=== 【写作约束 · 角色锚定】 ==="]
    parts.append(f"主角：{protagonist}")
    parts.append(f"已登场角色：{', '.join(characters)}")
    parts.append("")
    parts.append("【强制规则】：")
    parts.append(f"1. 本小说主角是「{protagonist}」，全文必须以他/她为核心视角展开，不得中途更换主角。")
    parts.append("2. 严格使用人物设定中的角色名，不得擅自改名、拆分、合并角色。")
    parts.append("3. 未在人物设定中出现的新角色，不得突然作为重要角色登场。如需引入新角色，必须先铺垫，且不能喧宾夺主。")
    parts.append("4. 每个角色的性格、说话方式、行为必须与其人物设定一致，不得前后矛盾。")
    parts.append(f"5. 除「{protagonist}」外，其他角色不得替代主角推动主线剧情。")

    return "\n".join(parts)


def build_title_constraint(old_title: str) -> str:
    """章节写作时注入已有标题约束，防止 LLM 每次生成不同标题。"""
    return f"\n\n【标题约束】本章标题必须是「{old_title}」，正文第一行用 # {old_title}，不得更改标题。"


def build_fix_titles_prompt(excerpts: list[str]) -> str:
    """标题校准：LLM 根据正文开头内容为每章重新拟标题。"""
    return (
        "你是小说编辑。以下是各章的开头内容和当前标题。\n"
        "当前标题可能重复或不准确，请根据正文内容为每章重新拟定一个2-6字的精准标题。\n"
        "【重要】只输出标题的后半部分，不要包含「第X章」前缀。例如：\n"
        "- 当前标题「第一章：寸铁」→ 输出「铁入学堂」\n"
        "- 当前标题「第二章：月照铁光」→ 输出「夜试寸铁」\n\n"
        "严格输出 JSON 格式，不要输出其他内容：\n"
        '{"1": "新标题", "2": "新标题", ...}\n\n'
        + "\n\n".join(excerpts)
    )


def build_world_state_prompt(n: int, gs_summary: str, content: str) -> str:
    """大世界状态更新：LLM 输出人物状态变化 JSON。

    正文全文注入：状态变化可能发生在章节后半段（截断会漏采）。
    """
    return (
        f"你是大世界状态追踪器。根据第{n}章正文，输出人物状态变化的 JSON。\n\n"
        f"当前状态：\n{gs_summary[:6000]}\n\n"
        f"第{n}章正文：\n{content}\n\n"
        f"严格输出 JSON，不要输出其他内容：\n"
        f'{{\n'
        f'  "characters": {{"角色名": {{"gold": 数字, "cultivation": {{"level": "炼气", "sub_level": "三层"}}, "location": "地点", "hp": 数字, "sp": 数字, "inventory": [{{"name": "物品名", "type": "类型"}}]}}, ...}},\n'
        f'  "items": {{"new": [{{"name": "物品名", "type": "类型", "effect": "效果"}}], "give": [{{"character": "角色名", "item": "物品名"}}], "remove": [{{"character": "角色名", "item": "物品名"}}]}},\n'
        f'  "timeline": [{{"event": "事件描述", "location": "地点"}}],\n'
        f'  "date": "时间"\n'
        f'}}\n\n'
        f"只输出本章发生变化的部分，没变化的字段不要输出。"
    )


def build_memory_sediment_prompt(n: int, active_loops: list[dict], content: str) -> str:
    """写后记忆沉淀：LLM 从最终正文提取结构化事实（主通道，正则只作兜底）。

    与 world_state_update 互补：那个管世界状态快照（境界/物品/位置），
    这个管叙事记忆（状态变化/伏笔开关/读者承诺/关系/世界规则）。
    """
    loops_text = ""
    if active_loops:
        lines = [
            f"- {item.get('subject', '')}：{str(item.get('value', ''))[:40]}"
            for item in active_loops[:15]
        ]
        loops_text = (
            "当前未解伏笔列表（若本章剧情已回收其中某条，把它的 subject "
            "原样放进 resolved 数组；没回收的不要动）：\n" + "\n".join(lines) + "\n\n"
        )
    return (
        f"你是小说记忆沉淀器。根据第{n}章正文，提取本章**真正发生**的事实变化，输出 JSON。\n"
        "【铁律】只记录确定发生的事。假设、设想、心理活动、角色口头的「如果…就…」不是事实，不得记录。\n\n"
        f"{loops_text}"
        f"第{n}章正文：\n{content}\n\n"
        "严格输出 JSON，不要输出其他内容：\n"
        "{\n"
        '  "character_state": [{"subject": "角色名", "aspect": "实力|身体|位置|身份", "value": "变化描述"}],\n'
        '  "open_loops": [{"value": "本章新埋设的伏笔/悬念描述"}],\n'
        '  "resolved": ["本章已回收的伏笔 subject（须与上面列表逐字一致）"],\n'
        '  "reader_promises": [{"subject": "承诺主题", "value": "对读者许下的承诺（复仇誓言/目标宣言/重要约定）"}],\n'
        '  "relationships": [{"subject": "角色A↔角色B", "value": "关系变化描述"}],\n'
        '  "world_rules": [{"subject": "规则名", "value": "本章新揭示的世界规则/力量体系/禁忌"}]\n'
        "}\n\n"
        "只输出本章实际发生的部分，没有变化的数组输出空数组。value 要简短（40 字内）。"
    )


def build_chapter_summary_prompt(n: int, ch_title: str, content: str) -> str:
    """每章结构化概要生成。"""
    return (
        f"请为以下章节生成结构化概要，严格按此格式输出：\n\n"
        f"## 第{n}章 {ch_title} 概要\n"
        f"- **地点**：主要场景\n"
        f"- **出场人物**：人物列表\n"
        f"- **核心事件**：本章发生的关键事情（2-3句）\n"
        f"- **伏笔/悬念**：埋设或回收的伏笔\n"
        f"- **情绪走向**：如 惊疑→震怒→决心\n"
        f"- **承接点**：本章结尾留给下一章的悬念（1句）\n\n"
        f"正文（全文）：\n{content}"
    )


def build_proofread_batch_prompt(batch: list[tuple[int, str]], batch_idx: int, total: int) -> str:
    """全书校对（分批）：校对一批章节，按章节列出问题。"""
    parts = [
        f"你是小说校对编辑。正在分批校对全书，当前是第 {batch_idx}/{total} 批。",
        "逐章校对错别字、语法、标点问题，按章节分组列出发现的问题和修改建议。",
        "没有问题的章节不用提及；只列问题，不要复述原文。\n",
    ]
    for num, text in batch:
        parts.append(f"=== 第{num}章 ===\n{text}")
    return "\n\n".join(parts)


def build_review_batch_prompt(
    batch: list[tuple[int, str]],
    planning_summary: str,
    batch_idx: int,
    total: int,
) -> str:
    """全书审核（分批）：审查一批章节的剧情逻辑、人物一致性、节奏。"""
    parts = [
        f"你是小说审稿编辑。正在分批审核全书，当前是第 {batch_idx}/{total} 批。",
        "审查本批各章的剧情逻辑、人物一致性、节奏问题，按章节列出发现（问题 + 修改建议）。",
        "只报告实际问题，不要泛泛而谈；没有问题的章节不用提及。\n",
    ]
    if planning_summary:
        parts.append(f"=== 规划参考（摘要） ===\n{planning_summary}\n")
    for num, text in batch:
        parts.append(f"=== 第{num}章 ===\n{text}")
    return "\n\n".join(parts)


def build_review_aggregate_prompt(findings: list[str], total_chapters: int) -> str:
    """全书审核（汇总）：把各批次的问题清单聚合成最终报告。"""
    return (
        f"你是小说审稿主编。以下是分批审核全部 {total_chapters} 章后各批次的问题清单。\n"
        "请汇总为一份最终审核报告，严格按此结构输出：\n\n"
        "## 总体评价\n（2-3句）\n\n"
        "## 重要问题\n（影响剧情逻辑/人物一致性的，按严重度排序）\n\n"
        "## 一般问题\n（文笔/标点/节奏等）\n\n"
        "## 修改建议\n（可操作的建议清单）\n\n"
        + "\n\n".join(f[:3000] for f in findings)
    )
