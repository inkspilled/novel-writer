"""叙述语言（语言质感）— 长篇小说的大白话约束与体检。

长篇靠的是「读得下去」，不是单句漂亮。默认「大白话」：
短句、口语词、少成语、少比喻；文采是上限，不是目标。

本模块提供两件事：
1. `build_voice_constraint()` — 按项目设定生成写作硬约束，注入正文/润色上下文；
2. `measure_voice()` — 统计成语密度、书面腔、比喻、凑字副词、句长，供质量检查提示。

设计原则：约束要具体到能执行（给阈值和反例），而不是「文笔自然」这类空话。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .meme_style import normalize_meme_level

DEFAULT_VOICE = "大白话"
VOICE_OPTIONS = ("大白话", "平实", "文学")

_VOICE_ALIASES = {
    "大白话": DEFAULT_VOICE,
    "口语": DEFAULT_VOICE,
    "口语化": DEFAULT_VOICE,
    "plain": DEFAULT_VOICE,
    "平实": "平实",
    "朴素": "平实",
    "克制": "平实",
    "normal": "平实",
    "文学": "文学",
    "文采": "文学",
    "文艺": "文学",
    "literary": "文学",
}


@dataclass(frozen=True)
class VoiceProfile:
    """一档语言质感的阈值（密度均按每千字计）。"""
    name: str
    summary: str
    max_idiom_per_k: float
    max_bookish_per_k: float
    max_simile_per_k: float
    max_filler_per_k: float
    max_sentence_len: float


_PROFILES: dict[str, VoiceProfile] = {
    "大白话": VoiceProfile(
        name="大白话",
        summary="短句、口语词、少成语、少比喻；宁可平，不要文绉绉。",
        max_idiom_per_k=3,
        max_bookish_per_k=2,
        max_simile_per_k=1,
        max_filler_per_k=2,
        max_sentence_len=18,
    ),
    "平实": VoiceProfile(
        name="平实",
        summary="正常书面语，克制修辞；能直说就不绕。",
        max_idiom_per_k=8,
        max_bookish_per_k=5,
        max_simile_per_k=3,
        max_filler_per_k=5,
        max_sentence_len=22,
    ),
    "文学": VoiceProfile(
        name="文学",
        summary="允许修辞和节奏经营，但反对堆砌；文采服务于人物和情境。",
        max_idiom_per_k=15,
        max_bookish_per_k=10,
        max_simile_per_k=6,
        max_filler_per_k=8,
        max_sentence_len=28,
    ),
}


def normalize_voice(value: str | None) -> str:
    """把项目里的自由文本归一到三档之一；未知值按默认大白话处理。"""
    text = str(value or "").strip().lower()
    if not text:
        return DEFAULT_VOICE
    # 先匹配完整值，再匹配包含关系（兼容「大白话（推荐）」这类带注释的旧值）
    if text in _VOICE_ALIASES:
        return _VOICE_ALIASES[text]
    for alias, canonical in _VOICE_ALIASES.items():
        if alias in text:
            return canonical
    return DEFAULT_VOICE


def voice_profile(voice: str | None = None) -> VoiceProfile:
    return _PROFILES[normalize_voice(voice)]


def build_voice_constraint(
    voice: str | None = None,
    style: str = "",
    meme_level: str = "",
) -> str:
    """生成注入写作上下文的语言硬约束。

    Args:
        voice: 项目叙述语言（大白话/平实/文学，未知值按大白话）
        style: 项目题材风格（史诗宏大/轻松幽默等），用于提醒「风格不等于辞藻」
        meme_level: 梗密度；开了梗就不禁用现代梗，只提醒别超量
    """
    profile = voice_profile(voice)
    meme_on = normalize_meme_level(meme_level) != "无"
    parts = [f"=== 【语言质感 · {profile.name}（硬约束）】 ==="]

    if profile.name == "大白话":
        parts.append("长篇靠读得下去，不靠单句漂亮。文采是上限，不是目标。")
        parts.extend([
            "1. 句子短：一句说一件事，平均不超过 18 字；长句拆开，少用「的」字套娃。",
            "2. 用口语词：说「走进来」不说「步入」，说「害怕」不说「心生惧意」，"
            "说「马上」不说「旋即」。口语化不等于网络梗："
            + ("现代梗按【梗密度】控制，不要超量。"
               if meme_on else
               "仙侠/历史题材不用现代流行语，把书面腔换成日常说法即可。"),
            "3. 少用成语：每千字不超过 3 个；能拆成大白话就拆"
            "（「胸有成竹」→「心里有底」）。",
            "4. 少打比方：比喻每千字不超过 1 处，不写「像……一般」「仿佛……似的」的模板句。",
            "5. 不堆修饰：一个名词最多一个修饰语；删掉「淡淡的/微微的/缓缓地」这类凑字词。",
            "6. 对话像人说话：允许短句、口头语、答非所问、被打断；人物不要出口成章、句句成语。",
            "7. 心理直说：「他有点慌」，不写「一丝不易察觉的慌乱掠过心头」。",
            "8. 不用书面腔：少用「然而/因而/与此同时/此刻/须臾」，改用「可/所以/这时候」。",
            "9. 不憋金句：本章不刻意写排比、对仗、警句；把事讲清楚比写漂亮重要。",
            "10. 写完后，把最「漂亮」的三句话删掉或改土；读着顺，就够了。",
        ])
    elif profile.name == "平实":
        parts.extend([
            "正常书面语，克制修辞；能直说就不绕。",
            "1. 以中短句为主，平均不超过 22 字。",
            "2. 成语/四字词每千字不超过 8 个，不连用三个以上。",
            "3. 比喻每千字不超过 3 处，不用「像……一般」的模板句。",
            "4. 少用「淡淡的/微微/缓缓」这类凑字副词。",
            "5. 对话按人物身份说话，允许口语和停顿，不写书面发言稿。",
            "6. 心理活动直说，不堆「难以言喻/涌上心头」。",
            "7. 不追求金句和排比；情节清楚优先。",
        ])
    else:
        parts.extend([
            "允许修辞和节奏经营，但仍然反对堆砌；文采服务于人物和情境。",
            "1. 句子长短要有变化，平均不超过 28 字。",
            "2. 成语/四字词每千字不超过 15 个，避免连用。",
            "3. 比喻每千字不超过 6 处，比喻要贴着人物此刻的处境，不用陈词。",
            "4. 形容词和副词要克制，删掉不承担信息的修饰。",
            "5. 对话不要变成散文朗诵，人物仍要像人在说话。",
            "6. 不写与冲突无关的风景和抒情；再美的句子，不为剧情服务就删。",
        ])

    if style:
        parts.append(
            f"【风格提醒】本书标称「{style}」。风格管题材调性和故事规模，"
            "不等于句子要文绉绉：要宏大，用事件规模和代价去写，不靠形容词；"
            "要深沉，用选择去写，不靠排比。"
        )
    return "\n".join(parts)


# ── 体检：把「文绉绉」变成可统计的信号 ──

# 高频书面成语/四字词（不追求全，只抓 AI 爱用的那批）
_IDIOMS = tuple(sorted({
    "一丝不苟", "一动不动", "一言不发", "一声不吭", "一五一十", "一清二楚", "一目了然",
    "一如既往", "一反常态", "不慌不忙", "不动声色", "不由自主", "情不自禁", "不假思索",
    "不约而同", "不置可否", "不容置疑", "不可思议", "不知所措", "不寒而栗", "不翼而飞",
    "不胫而走", "不以为然", "不闻不问", "无动于衷", "无可奈何", "无所适从", "无影无踪",
    "无缘无故", "若无其事", "若有所思", "意味深长", "欲言又止", "似笑非笑", "轻描淡写",
    "漫不经心", "高深莫测", "深不可测", "胸有成竹", "处变不惊", "波澜不惊", "井然有序",
    "显而易见", "不言而喻", "理所当然", "顺理成章", "水到渠成", "天经地义", "毋庸置疑",
    "目瞪口呆", "瞠目结舌", "呆若木鸡", "哭笑不得", "如释重负", "如坐针毡", "如履薄冰",
    "如鱼得水", "如梦初醒", "如出一辙", "心如刀绞", "心如死灰", "心照不宣", "心领神会",
    "心血来潮", "心平气和", "心安理得", "心有余悸", "心惊肉跳", "心猿意马", "面不改色",
    "面无表情", "面如死灰", "眉开眼笑", "眉飞色舞", "愁眉不展", "垂头丧气", "兴高采烈",
    "兴致勃勃", "喜出望外", "大惊失色", "大惊小怪", "大失所望", "大快人心", "大彻大悟",
    "恍然大悟", "茅塞顿开", "醍醐灌顶", "豁然开朗", "耳目一新", "翻天覆地", "惊天动地",
    "排山倒海", "势如破竹", "摧枯拉朽", "所向披靡", "一败涂地", "一蹶不振", "一鼓作气",
    "再接再厉", "循序渐进", "千方百计", "千钧一发", "千载难逢", "千变万化", "千丝万缕",
    "万无一失", "万众瞩目", "万念俱灰", "万籁俱寂", "半信半疑", "半途而废", "反败为胜",
    "如火如荼", "如日中天", "刻不容缓", "刻骨铭心", "历历在目", "周而复始", "味同嚼蜡",
    "呼之欲出", "命悬一线", "咬紧牙关", "咫尺天涯", "嗤之以鼻", "固执己见", "图穷匕见",
    "娓娓道来", "字斟句酌", "循规蹈矩", "战战兢兢", "小心翼翼", "提心吊胆", "泰然自若",
    "气定神闲", "神清气爽", "神魂颠倒", "精疲力竭", "筋疲力尽", "仓皇失措", "手足无措",
    "油然而生", "涌上心头", "难以言喻", "不可名状", "映入眼帘", "尽收眼底", "一览无余",
}))

# 书面腔连接词/副词（多音节优先，避免子串误伤）
_BOOKISH_WORDS = tuple(sorted({
    "然而", "因而", "因此", "旋即", "须臾", "霎时", "顷刻", "倏然", "蓦地",
    "陡然", "良久", "方才", "未几", "片刻", "颇为", "极为", "甚是", "尤为", "与此同时",
    "此刻", "此时", "之际", "不由得", "不禁", "心中暗道", "心道", "暗道", "沉声道",
    "淡淡道", "默不作声", "若有若无", "似有似无", "不知为何", "说不清", "难以言喻",
    "无法形容", "不可名状", "油然而生", "涌上心头", "映入眼帘", "放眼望去", "尽收眼底",
    "片刻之后", "不多时", "良久之后", "紧接着", "随后",
}, key=lambda w: (-len(w), w)))

# 凑字副词/量词（「淡淡的」「一丝」这类，删了不影响信息）
_FILLER_WORDS = tuple(sorted({
    "淡淡的", "微微", "缓缓", "轻轻", "悄然", "静静", "幽幽", "徐徐", "淡淡地",
    "微微地", "缓缓地", "轻轻地", "下意识", "不自觉", "莫名", "一丝", "一抹", "一缕",
    "几分", "些许", "略微", "稍稍", "略略",
}, key=lambda w: (-len(w), w)))

_BOOKISH_RE = re.compile("|".join(re.escape(w) for w in _BOOKISH_WORDS))
_FILLER_RE = re.compile("|".join(re.escape(w) for w in _FILLER_WORDS))
_SIMILE_RE = re.compile(
    r"仿佛|宛如|犹如|好似|如同|恰似|像[^。！？!?\n]{0,25}?(?:一样|一般|似的|般)"
)
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?…]+")
_DIALOGUE_RE = re.compile(r"[“\"]([^”\"]{1,400})[”\"]")
_CN_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


@dataclass
class VoiceMetrics:
    """文本的语言质感统计。"""
    voice: str = DEFAULT_VOICE
    chars: int = 0
    sentence_count: int = 0
    avg_sentence_len: float = 0.0
    idiom_per_k: float = 0.0
    bookish_per_k: float = 0.0
    simile_per_k: float = 0.0
    filler_per_k: float = 0.0
    dialogue_ratio: float = 0.0
    idiom_hits: list[str] = field(default_factory=list)
    bookish_hits: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "voice": self.voice,
            "chars": self.chars,
            "avg_sentence_len": round(self.avg_sentence_len, 1),
            "idiom_per_k": round(self.idiom_per_k, 1),
            "bookish_per_k": round(self.bookish_per_k, 1),
            "simile_per_k": round(self.simile_per_k, 1),
            "filler_per_k": round(self.filler_per_k, 1),
            "dialogue_ratio": round(self.dialogue_ratio, 3),
        }


def _per_k(count: int, chars: int) -> float:
    return count * 1000.0 / chars if chars else 0.0


def measure_voice(text: str, voice: str | None = None) -> VoiceMetrics:
    """统计语言质感；短文本（<300 字）只算指标，不判超标。"""
    profile = voice_profile(voice)
    cn_chars = len(_CN_CHAR_RE.findall(text or ""))
    metrics = VoiceMetrics(voice=profile.name, chars=cn_chars)
    if not text or cn_chars == 0:
        return metrics

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if len(s.strip()) >= 2]
    metrics.sentence_count = len(sentences)
    if sentences:
        metrics.avg_sentence_len = sum(len(s) for s in sentences) / len(sentences)

    idiom_hits = [w for w in _IDIOMS if w in text]
    bookish_hits = [w for w in _BOOKISH_WORDS if w in text]
    idiom_count = sum(text.count(w) for w in idiom_hits)
    bookish_count = len(_BOOKISH_RE.findall(text))
    simile_count = len(_SIMILE_RE.findall(text))
    filler_count = len(_FILLER_RE.findall(text))
    dialogue_chars = sum(len(m) for m in _DIALOGUE_RE.findall(text))

    metrics.idiom_hits = idiom_hits[:8]
    metrics.bookish_hits = bookish_hits[:8]
    metrics.idiom_per_k = _per_k(idiom_count, cn_chars)
    metrics.bookish_per_k = _per_k(bookish_count, cn_chars)
    metrics.simile_per_k = _per_k(simile_count, cn_chars)
    metrics.filler_per_k = _per_k(filler_count, cn_chars)
    metrics.dialogue_ratio = min(1.0, dialogue_chars / cn_chars)

    if cn_chars < 300:
        return metrics

    if metrics.idiom_per_k > profile.max_idiom_per_k:
        examples = "、".join(metrics.idiom_hits[:5])
        metrics.issues.append(
            f"成语/四字词偏多：{metrics.idiom_per_k:.1f} 个/千字"
            f"（{profile.name}建议 ≤{profile.max_idiom_per_k:g}）"
            + (f"，例：{examples}" if examples else "")
        )
    if metrics.bookish_per_k > profile.max_bookish_per_k:
        examples = "、".join(metrics.bookish_hits[:5])
        metrics.issues.append(
            f"书面腔偏重：{metrics.bookish_per_k:.1f} 个/千字"
            f"（建议 ≤{profile.max_bookish_per_k:g}）"
            + (f"，例：{examples}" if examples else "")
        )
    if metrics.simile_per_k > profile.max_simile_per_k:
        metrics.issues.append(
            f"比喻偏密：{metrics.simile_per_k:.1f} 处/千字"
            f"（建议 ≤{profile.max_simile_per_k:g}），别用「像……一般」凑画面"
        )
    if metrics.filler_per_k > profile.max_filler_per_k:
        metrics.issues.append(
            f"凑字副词偏多：{metrics.filler_per_k:.1f} 个/千字"
            f"（建议 ≤{profile.max_filler_per_k:g}），"
            "「淡淡的/微微/缓缓/一丝」这类能删就删"
        )
    if metrics.avg_sentence_len > profile.max_sentence_len:
        metrics.issues.append(
            f"句子偏长：平均 {metrics.avg_sentence_len:.0f} 字"
            f"（建议 ≤{profile.max_sentence_len:g}），拆成短句更顺"
        )
    return metrics


def voice_issues(text: str, voice: str | None = None) -> list[str]:
    """只取问题清单，便于调用方直接拼报告。"""
    return measure_voice(text, voice).issues
