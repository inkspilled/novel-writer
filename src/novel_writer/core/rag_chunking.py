"""叙事单元分块 — 按场景边界切块，而不是按固定字数硬切。

小说检索失败最常见的原因是切块把情节拦腰截断（「他拔出了剑」和
「对方应声倒地」落在两个块里）。本模块按叙事结构切分：

1. 先按空行切段落（无空行的老稿退化为按单换行）；
2. 用分隔符 / 时间跳转 / 视角与地点切换等信号合并成「场景」；
3. 场景不超过 ``max_tokens`` 时整场一块；
4. 超长场景只在场景内部按自然段切，并保留 10%–20% 的尾部重叠。

每个块同时产出元数据：场景序号、场景类型（对话/战斗/日常/回忆）、
出场人物、时间线位置，供检索阶段做元数据过滤与重排。

> token 估算与 ``workflow/context.estimate_tokens`` 同口径（中文≈1.5/字，
> 英文单词≈1.3）；这里本地实现是为了避免 rag → workflow → rag 的循环导入。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# 单个场景块的 token 上限；超过后在场景内部按自然段切
DEFAULT_MAX_CHUNK_TOKENS = 1500
# 场景内部切分时的尾部重叠比例（10%–20% 是常用区间）
DEFAULT_OVERLAP_RATIO = 0.15
# 过短段落（目录行、分隔符残留）不进索引
MIN_CHUNK_CHARS = 20
# 每个块最多记录的角色数，避免元数据膨胀
MAX_CHARACTERS_PER_CHUNK = 6

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")

# 场景分隔符：***、---、===、◇◇◇ 等独占一行的装饰
_SEPARATOR_RE = re.compile(r"^(?:[*＊#=＝~～\-—─_＿◇◆※·•\s]{3,})$")

# 场景切换信号（只认段首，避免正文中「三天后」误切）：
# 时间跳转、地点/视角切换、章节内小标题
_SCENE_BOUNDARY_RE = re.compile(
    r"^(?:"
    r"次[日天]|翌[日天]|第[二三四五六七八九十0-9]+[天日]|"
    r"[一二三四五六七八九十百千万0-9]+(?:天|日|夜|年|个月|月|个时辰|时辰|刻钟|炷香|盏茶)后|"
    r"与此同时|同一时间|另一边|另一头|而在|回到|镜头一转|"
    r"走出|走进|离开|进入|踏入|搬进|登上|来到|赶往|返回|"
    r"当夜|当晚|入夜|深夜|黎明|清晨|黄昏|"
    r"第[一二三四五六七八九十百千0-9]+[章卷回]"
    r")"
)

# 短段落里的场景提示词：网文常用「内门后山。」「同一夜。」「赵无极的书房还亮着。」
# 这类独占一段的舞台指示当场景边界，但不能把短对话（引号开头）误判成边界。
_SCENE_CUE_RE = re.compile(
    r"(?:清晨|黎明|黄昏|入夜|深夜|子时|午时|辰时|酉时|同一夜|当夜|当晚|"
    r"书房|厢房|卧房|客房|洞府|石室|大殿|偏殿|山门|后山|院中|院落|"
    r"楼上|阁楼|城门口|街口|宫门|府邸|演武场|练武场|擂台|客栈|酒楼|药铺|"
    r"执事房|执事堂|藏书阁|藏经阁|广场)"
)
_DIALOGUE_START_RE = re.compile(r"^[“\"「『]")

# 场景类型关键词（只做轻量规则分类，不调 LLM）
_BATTLE_WORDS = (
    "剑", "刀", "枪", "拳", "掌", "血", "杀", "斩", "劈", "刺",
    "轰", "爆", "冲", "闪避", "格挡", "交手", "厮杀", "战斗", "攻击",
    "招式", "灵力", "法术", "神通", "怒吼", "惨叫", "受伤", "倒地",
)
_MEMORY_WORDS = (
    "回忆", "想起", "记得", "当年", "曾经", "往事", "童年", "梦里",
    "恍惚", "记忆中", "多年前", "昔日", "旧事", "回忆里", "回想",
)
_DIALOGUE_RE = re.compile(r"[“\"「『](.*?)[”\"」』]", re.DOTALL)
_SENTENCE_RE = re.compile(r"(?<=[。！？!?…；;])\s*")


@dataclass
class ChunkDraft:
    """一个待入索引的叙事块（与 rag.Chunk 解耦，避免循环导入）。"""

    text: str
    scene: int = 0
    scene_type: str = "日常"
    characters: list[str] = field(default_factory=list)
    timeline: str = ""


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（与 workflow.context.estimate_tokens 同口径）。"""
    if not text:
        return 0
    cn_chars = len(_CJK_RE.findall(text))
    en_words = len(_WORD_RE.findall(text))
    return int(cn_chars * 1.5 + en_words * 1.3)


def _split_paragraphs(content: str) -> list[str]:
    """按空行切自然段；没有空行的老稿退化为按单换行切。"""
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    if re.search(r"\n\s*\n", text):
        raw = re.split(r"\n\s*\n", text)
    elif text.count("\n") >= 3:
        raw = text.split("\n")
    else:
        raw = [text]
    return [p.strip() for p in raw if p.strip()]


def _is_separator(paragraph: str) -> bool:
    return bool(_SEPARATOR_RE.match(paragraph.strip()))


def _is_scene_boundary(paragraph: str) -> bool:
    text = paragraph.strip()
    if _SCENE_BOUNDARY_RE.match(text):
        return True
    if (len(text) <= 24 and not _DIALOGUE_START_RE.match(text)
            and _SCENE_CUE_RE.search(text)):
        return True
    return False


def _group_scenes(paragraphs: list[str]) -> list[list[str]]:
    """把段落合并成场景：分隔符/时间跳转/地点切换处断开。"""
    scenes: list[list[str]] = []
    current: list[str] = []
    for para in paragraphs:
        if _is_separator(para):
            if current:
                scenes.append(current)
                current = []
            continue
        if current and _is_scene_boundary(para):
            scenes.append(current)
            current = []
        current.append(para)
    if current:
        scenes.append(current)
    return scenes


def _merge_short_scenes(scenes: list[list[str]]) -> list[list[str]]:
    """把过短的场景并进相邻场景，避免「内门后山。」这类单句转场被 20 字下限丢掉。

    带场景提示词/切换信号的短句归入**下一场**（它是下一场的舞台指示）；
    其余短句（如被分隔符切开的一句话）归入上一场。
    """
    merged: list[list[str]] = []
    pending: list[str] = []
    for scene in scenes:
        text = "\n\n".join(scene).strip()
        if len(text) < MIN_CHUNK_CHARS:
            is_stage_direction = (
                len(text) <= 12
                and (_SCENE_CUE_RE.search(text) or _SCENE_BOUNDARY_RE.match(text))
            )
            if is_stage_direction:
                pending.extend(scene)
            elif pending:
                pending.extend(scene)
            elif merged:
                merged[-1].extend(scene)
            else:
                pending.extend(scene)
            continue
        if pending:
            scene = pending + scene
            pending = []
        merged.append(list(scene))
    if pending:
        if merged:
            merged[-1].extend(pending)
        else:
            merged.append(pending)
    return merged


def _hard_wrap(text: str, max_tokens: int) -> list[str]:
    """极端长段（无句号）兜底：按字符硬切，保证单块不超上限。"""
    max_chars = max(1, int(max_tokens / 1.5))
    return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]


def _split_paragraph(paragraph: str, max_tokens: int) -> list[str]:
    """把超长自然段按句末标点切成不超过 max_tokens 的片段。"""
    if estimate_tokens(paragraph) <= max_tokens:
        return [paragraph]
    sentences = [s for s in _SENTENCE_RE.split(paragraph) if s.strip()]
    if not sentences:
        return _hard_wrap(paragraph, max_tokens)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if estimate_tokens(sentence) > max_tokens:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(_hard_wrap(sentence, max_tokens))
            continue
        if current and estimate_tokens(current + sentence) > max_tokens:
            pieces.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        pieces.append(current)
    return pieces


def _tail_overlap(paragraphs: list[str], budget: int) -> list[str]:
    """取窗口尾部不超过 budget 的内容做重叠；整段放不下时退到句级。"""
    if budget <= 0:
        return []
    tail: list[str] = []
    tail_tokens = 0
    for old in reversed(paragraphs):
        old_tokens = estimate_tokens(old)
        if tail_tokens + old_tokens > budget:
            break
        tail.insert(0, old)
        tail_tokens += old_tokens
    if tail:
        return tail

    # 最后一段比重叠预算还长：取它的尾部句子，避免整段重复
    last = paragraphs[-1]
    sentences = [s for s in _SENTENCE_RE.split(last) if s.strip()]
    picked: list[str] = []
    picked_tokens = 0
    for sentence in reversed(sentences):
        sentence_tokens = estimate_tokens(sentence)
        if picked_tokens + sentence_tokens > budget:
            break
        picked.insert(0, sentence)
        picked_tokens += sentence_tokens
    return ["".join(picked)] if picked else []


def _split_scene(
    scene: list[str],
    max_tokens: int,
    overlap_ratio: float,
) -> list[list[str]]:
    """场景超长时在场景内部切分；相邻窗口保留尾部重叠，防止情节被切断。"""
    total = sum(estimate_tokens(p) for p in scene)
    if total <= max_tokens:
        return [scene]

    paragraphs: list[str] = []
    for para in scene:
        paragraphs.extend(_split_paragraph(para, max_tokens))

    overlap_budget = max(1, int(max_tokens * max(0.0, min(overlap_ratio, 0.5))))
    windows: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0
    for para in paragraphs:
        para_tokens = estimate_tokens(para)
        if current and current_tokens + para_tokens > max_tokens:
            windows.append(current)
            tail = _tail_overlap(current, overlap_budget)
            current = list(tail)
            current_tokens = sum(estimate_tokens(p) for p in current)
        current.append(para)
        current_tokens += para_tokens
    if current:
        windows.append(current)
    return windows


def detect_scene_type(text: str) -> str:
    """轻量场景分类：战斗 / 回忆 / 对话 / 日常（四选一）。

    用关键词密度与引号内文本占比打分，不调用 LLM，保证离线可用。
    """
    if not text:
        return "日常"
    cjk_chars = max(1, sum(1 for c in text if "\u4e00" <= c <= "\u9fff"))

    battle_hits = sum(text.count(word) for word in _BATTLE_WORDS)
    memory_hits = sum(text.count(word) for word in _MEMORY_WORDS)
    quoted_chars = sum(len(m.group(1)) for m in _DIALOGUE_RE.finditer(text))

    # 战斗要同时满足「至少几个战斗词」和「密度够高」：
    # 否则一句「你的剑呢？」就会被短场景的密度公式放大成战斗。
    battle_rate = battle_hits / cjk_chars * 100
    battle_score = min(1.0, battle_hits / 3.0) * min(1.0, battle_rate / 2.0)
    memory_score = min(1.0, memory_hits / 3.0)
    dialogue_score = min(1.0, (quoted_chars / max(1, len(text))) / 0.3)

    scores = [
        ("战斗", battle_score),
        ("回忆", memory_score),
        ("对话", dialogue_score),
    ]
    best_type, best_score = max(scores, key=lambda item: item[1])
    return best_type if best_score >= 0.5 else "日常"


def extract_characters(text: str, known_names: Iterable[str]) -> list[str]:
    """从块文本中找出已知道角色名（不猜未知人名，避免误报）。"""
    found: list[str] = []
    for name in sorted(
        {n.strip() for n in known_names if n and n.strip()},
        key=lambda n: (-len(n), n),
    ):
        if name in text:
            found.append(name)
            if len(found) >= MAX_CHARACTERS_PER_CHUNK:
                break
    return found


def chunk_chapter(
    content: str,
    chapter: int = 0,
    known_names: Iterable[str] = (),
    max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
) -> list[ChunkDraft]:
    """把一章正文切成叙事块，并附带检索元数据。

    返回的块按章节内顺序排列；``timeline`` 形如 ``第3章·场景2``。
    """
    paragraphs = _split_paragraphs(content)
    if not paragraphs:
        return []

    names = list(known_names)
    drafts: list[ChunkDraft] = []
    for scene_index, scene in enumerate(_merge_short_scenes(_group_scenes(paragraphs))):
        scene_text = "\n\n".join(scene).strip()
        if not scene_text:
            continue
        scene_type = detect_scene_type(scene_text)
        characters = extract_characters(scene_text, names)
        timeline = f"第{chapter}章·场景{scene_index + 1}" if chapter else f"场景{scene_index + 1}"
        for window in _split_scene(scene, max_tokens, overlap_ratio):
            text = "\n\n".join(window).strip()
            # 短句也可能承载关键事实（例如“钥匙在李四手里。”），不能按长度丢弃。
            # 标题和装饰分隔符已在段落/场景层处理。
            if not re.sub(r"^\s*#{1,6}\s+.*$", "", text, flags=re.MULTILINE).strip():
                continue
            drafts.append(ChunkDraft(
                text=text,
                scene=scene_index,
                scene_type=scene_type,
                characters=characters,
                timeline=timeline,
            ))
    return drafts
