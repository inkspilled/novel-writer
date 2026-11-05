"""短剧上下文切片 — 按预算和「有效戏」挑材料，不把整本塞进提示词。

经验规则（按优先级）：

1. **预算内不切**：文本没超预算就原样给，不做无谓的信息损失。
2. **按场景切**：超预算时复用 RAG 的场景分块（``rag_chunking.chunk_chapter``），
   一场戏不会被拦腰截断——短剧改编最怕把「拔出刀」和「人倒了」分到两次调用里。
3. **冲突密度优先**：冲突/反转/决定类词、对白行、出场人物越多，这块越值得送。
   铺陈、抒情、设定说明天然得分低，超预算时先被丢掉。
4. **首尾必留**：开场块和最后一块承载「入戏」与「集尾钩子」，先占预算。
5. **原序拼接**：选中的块按原文顺序还原，块间标「（略）」，让模型知道有删减，
   不要自己脑补被略过的剧情。

改编圣经和人物档案按 Markdown 小节切：优先送「红线 / 节奏 / 钩子 / 视觉锚点 /
本次出场人物」相关小节，其余按预算截断。
"""
from __future__ import annotations

import re

from .rag_chunking import chunk_chapter

# ── 预算（字符数，粗略按中文 1 字 ≈ 1 token 的 2/3 估） ──
BIBLE_BUDGET = 6000          # 改编圣经一次送入上限
ADAPT_DOC_BUDGET = 2600      # 改编圣经任务里，单份规划文档上限
PLAN_CHAPTER_BUDGET = 2000   # 分集策划：单章摘录上限
PLAN_WINDOW_BUDGET = 9000    # 分集策划：整个阅读窗口上限
SCRIPT_BUDGET = 12000        # 单集剧本：原文上限
STORYBOARD_BUDGET = 12000    # 分镜：剧本上限
PREV_TAIL_BUDGET = 900       # 上一集结尾上限
RECAP_EPISODES = 3           # 前情提要回看的集数

_OMIT = "\n\n（略）\n\n"

# 有效戏：冲突、反转、决定、生死、交换——短剧的核心燃料
CONFLICT_WORDS = (
    "杀", "死", "血", "刀", "剑", "枪", "打", "砸", "摔", "追", "逃", "逼",
    "威胁", "跪", "吼", "怒", "哭", "惊", "怕", "疼", "伤", "背叛", "出卖",
    "发现", "原来", "竟然", "居然", "真相", "秘密", "身份", "证据", "底牌",
    "交换", "条件", "除非", "否则", "必须", "赌", "输", "赢", "抢", "夺",
    "离婚", "分手", "退婚", "开除", "破产", "欠", "还钱", "救", "护",
)
TURN_WORDS = ("却", "但是", "然而", "没想到", "不料", "突然", "忽然", "就在这时", "正在这时")
_DIALOGUE_RE = re.compile(r"[“「\"].{1,60}[”」\"]|^[^\n：:]{1,8}[：:].+$", re.MULTILINE)
_SECTION_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)


def _score(text: str, known_names: list[str]) -> float:
    """一块文本的「值得送」程度：冲突词 > 转折词 > 对白 > 出场人物。"""
    hits = sum(text.count(word) for word in CONFLICT_WORDS)
    turns = sum(text.count(word) for word in TURN_WORDS)
    dialogue = len(_DIALOGUE_RE.findall(text))
    cast = sum(1 for name in known_names if name and name in text)
    return hits * 6.0 + turns * 4.0 + min(dialogue, 12) * 1.0 + cast * 1.5


def _join_blocks(blocks: list[str]) -> str:
    return _OMIT.join(blocks)


def slice_chapter(text: str, budget: int = SCRIPT_BUDGET,
                  known_names: list[str] | None = None) -> str:
    """按预算取一章里最有效的场景块；预算内原样返回。"""
    body = (text or "").strip()
    if not body:
        return ""
    if len(body) <= budget:
        return body

    chunks = chunk_chapter(body, known_names=known_names or [])
    if len(chunks) <= 1:
        return _head_tail(body, budget)

    scored = [(_score(chunk.text, known_names or []), index, chunk.text)
              for index, chunk in enumerate(chunks)]
    keep: dict[int, str] = {}
    used = 0
    overhead = len(_OMIT)

    # 首尾先占位：开场入戏、结尾钩子，不能因为「冲突词少」被丢。
    # 首/尾块本身超预算时给截断版，保证「有头有尾」。
    head_room = max(120, min(300, budget // 3))
    first = chunks[0].text
    if len(first) <= budget:
        keep[0] = first
        used += len(first) + overhead
    else:
        keep[0] = first[:head_room]
        used += head_room + overhead

    last_index = len(chunks) - 1
    if last_index not in keep:
        last = chunks[last_index].text
        tail_room = max(120, min(260, budget // 4))
        if used + len(last) <= budget:
            keep[last_index] = last
            used += len(last) + overhead
        else:
            keep[last_index] = last[-tail_room:]
            used += tail_room + overhead

    # 其余按得分从高到低填，直到预算用尽
    for score, index, piece in sorted(scored, key=lambda item: (-item[0], item[1])):
        if index in keep or score <= 0:
            continue
        if used + len(piece) > budget:
            continue
        keep[index] = piece
        used += len(piece) + overhead

    picked = [_join_blocks([keep[i] for i in sorted(keep)])]
    note = (f"（本章原文 {len(body)} 字，按冲突密度选取 {len(keep)}/{len(chunks)} 段，"
            f"约 {used} 字；被略过的段落未提供，不要据此虚构剧情）")
    picked.insert(0, note)
    return "\n\n".join(picked)


def _head_tail(text: str, budget: int) -> str:
    """切不动（整章一个巨块）时的兜底：开头 + 结尾，中间留省略标记。"""
    if len(text) <= budget:
        return text
    head = max(200, int(budget * 0.6))
    tail = max(120, budget - head - len(_OMIT))
    return text[:head] + _OMIT + text[-tail:]


def plan_digest(text: str, budget: int = PLAN_CHAPTER_BUDGET,
                known_names: list[str] | None = None) -> str:
    """分集策划用的单章摘录：只看「有没有戏、够不够一集」，不需要全文。"""
    return slice_chapter(text, budget=budget, known_names=known_names)


def _split_sections(text: str) -> list[tuple[str, str]]:
    """按 Markdown 标题切小节，返回 (标题, 含标题的整段)。无标题则整篇一节。"""
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        return [("", text)]
    sections: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        head = text[:matches[0].start()].strip()
        if head:
            sections.append(("", head))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((match.group(2).strip(), text[match.start():end].strip()))
    return sections


def slice_bible(text: str, keys: tuple[str, ...] | list[str] = (),
                budget: int = BIBLE_BUDGET) -> str:
    """改编圣经按相关小节送：优先红线/节奏/钩子等，其余按预算截断。"""
    body = (text or "").strip()
    if not body:
        return ""
    if len(body) <= budget:
        return body

    sections = _split_sections(body)
    scored: list[tuple[float, int, str]] = []
    for index, (heading, block) in enumerate(sections):
        score = sum(3.0 for key in keys if key and key in heading)
        score += sum(1.0 for key in keys if key and key in block)
        if any(word in heading for word in ("红线", "规则", "节奏", "钩子")):
            score += 4.0
        if index == 0:
            score += 2.0  # 开头的总述/卖点通常要用
        scored.append((score, index, block))

    keep: dict[int, str] = {}
    used = 0
    for _score_value, index, block in sorted(scored, key=lambda item: (-item[0], item[1])):
        if used + len(block) > budget and keep:
            continue
        keep[index] = block[: max(0, budget - used)]
        used += len(keep[index])
        if used >= budget:
            break

    out = "\n\n".join(keep[i] for i in sorted(keep))
    if len(keep) < len(sections):
        out += f"\n\n（圣经已按预算裁剪：送入 {len(keep)}/{len(sections)} 节，约 {len(out)} 字）"
    return out


def parse_character_names(profile_md: str, limit: int = 40) -> list[str]:
    """从「人物设定.md」的小节标题里取角色名（``## 顾言``）。"""
    names: list[str] = []
    for heading, _block in _split_sections(profile_md or ""):
        name = re.sub(r"^[（(].*?[）)]", "", heading).strip()
        name = re.split(r"[·:：\s（(]", name)[0].strip()
        if 2 <= len(name) <= 6 and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def filter_character_sections(profile_md: str, names: list[str],
                              budget: int = ADAPT_DOC_BUDGET) -> str:
    """人物档案只留本章/本集出场的人；一个都没匹配上就退回按预算切片。"""
    body = (profile_md or "").strip()
    if not body or not names:
        return slice_bible(body, ("主角", "关系", "动机"), budget)
    hits = [block for heading, block in _split_sections(body)
            if any(name and name in heading for name in names)]
    if not hits:
        return slice_bible(body, ("主角", "关系", "动机"), budget)
    text = "\n\n".join(hits)
    if len(text) > budget:
        text = text[:budget] + "\n\n（人物档案已按预算截断）"
    return text


def compress_script(script: str, budget: int = STORYBOARD_BUDGET) -> str:
    """分镜用剧本：预算内原样；超了就「保场次标题 + 保台词」，压缩动作描述。"""
    body = (script or "").strip()
    if len(body) <= budget:
        return body
    lines = body.splitlines()
    kept: list[str] = []
    deferred: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or _DIALOGUE_RE.match(stripped):
            kept.append(line)
        else:
            deferred.append(line)
    text = "\n".join(kept)
    room = budget - len(text)
    if room > 0:
        fill: list[str] = []
        for line in deferred:
            if room - len(line) - 1 < 0:
                break
            fill.append(line)
            room -= len(line) + 1
        # 动作行插回台词之后会乱序，这里统一附在末尾并标注
        if fill:
            text += "\n\n（以下为未按场次排入的动作描述节选）\n" + "\n".join(fill)
    return text[:budget] + "\n\n（剧本已压缩：保全部场次标题与台词，动作描述按预算截断）"


def recap_lines(episodes: list[dict], before_number: int, limit: int = RECAP_EPISODES) -> str:
    """前情提要：最近几集的「集名 / 目标 / 结尾钩子」，比整段回看便宜得多。"""
    picked = [ep for ep in episodes if int(ep.get("number") or 0) < before_number]
    picked = sorted(picked, key=lambda ep: int(ep.get("number") or 0))[-limit:]
    lines = []
    for ep in picked:
        bits = [f"第{ep.get('number')}集"]
        if ep.get("title"):
            bits.append(str(ep["title"]))
        text = " · ".join(bits)
        if ep.get("goal"):
            text += f"｜目标：{ep['goal']}"
        if ep.get("hook"):
            text += f"｜钩子：{ep['hook']}"
        lines.append(text)
    return "\n".join(lines)


def truncate(text: str, budget: int) -> str:
    """简单截断（上一集结尾这类本来就不该长）。"""
    body = (text or "").strip()
    if len(body) <= budget:
        return body
    return body[:budget].rstrip() + "…"
