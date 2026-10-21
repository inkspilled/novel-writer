"""梗密度 — 现代梗 / 职场梗的用量控制与体检。

长篇里「梗」是调味，不是主菜：少了没味，多了出戏，重复了尴尬。
本模块负责三件事：
1. `build_meme_constraint()` — 按项目「梗密度」生成写作硬约束（谁能在什么场合玩、玩多少、玩哪类）；
2. `measure_memes()` — 统计现代梗命中数与密度，供质量检查提示；
3. 预设与归一化 — 无 / 少 / 中 / 高 四档，未知值按默认「少」。

和 `narrative_voice` 的分工：那个管句子长短和书面腔，这个管「能不能玩现代梗、玩多少」。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

DEFAULT_MEME_LEVEL = "少"
MEME_LEVELS = ("无", "少", "中", "高")

_ALIASES = {
    "无": "无", "关": "无", "关闭": "无", "off": "无", "none": "无", "0": "无",
    "少": "少", "低": "少", "少量": "少", "低密度": "少", "low": "少",
    "中": "中", "适中": "中", "中等": "中", "medium": "中", "mid": "中",
    "高": "高", "多": "高", "高密度": "高", "high": "高",
}

# 题材偏古代/架空：现代梗只能当错位吐槽，不能成为世界内日常词
_ANCIENT_GENRES = ("仙侠", "武侠", "历史", "古言", "玄幻", "修真", "奇幻", "架空", "灵异")


@dataclass(frozen=True)
class MemeProfile:
    level: str
    per_chapter: tuple[int, int]  # 按 3000-4000 字一章的目标区间
    max_per_chapter: int
    max_per_k: float


_PROFILES: dict[str, MemeProfile] = {
    "无": MemeProfile(level="无", per_chapter=(0, 0), max_per_chapter=0, max_per_k=0.0),
    "少": MemeProfile(level="少", per_chapter=(1, 2), max_per_chapter=3, max_per_k=0.8),
    "中": MemeProfile(level="中", per_chapter=(3, 5), max_per_chapter=6, max_per_k=1.8),
    "高": MemeProfile(level="高", per_chapter=(6, 10), max_per_chapter=12, max_per_k=3.5),
}


def normalize_meme_level(value: str | None) -> str:
    """把项目里的自由文本归一到四档；未知值按默认「少」。"""
    text = str(value or "").strip().lower()
    if not text:
        return DEFAULT_MEME_LEVEL
    if text in _ALIASES:
        return _ALIASES[text]
    for alias, canonical in _ALIASES.items():
        if alias in text:
            return canonical
    return DEFAULT_MEME_LEVEL


def meme_profile(level: str | None = None) -> MemeProfile:
    return _PROFILES[normalize_meme_level(level)]


def _is_ancient(genre: str) -> bool:
    return any(g in (genre or "") for g in _ANCIENT_GENRES)


def build_meme_constraint(
    level: str | None = None,
    genre: str = "",
    voice: str = "",
) -> str:
    """生成「梗密度」写作约束；梗密度=无时返回明确的禁用指令。"""
    profile = meme_profile(level)
    parts = [f"=== 【梗密度 · {profile.level}】 ==="]

    if profile.level == "无":
        parts.extend([
            "本章不使用任何现代网络梗、职场梗或时代错位吐槽。",
            "现代词只保留故事设定必需的概念（如「仲裁」「合同」），不玩梗、不抖机灵、不解释梗。",
        ])
    else:
        low, high = profile.per_chapter
        parts.append(f"现代梗是调味，不是主菜：本章 {low}–{high} 个，最多 {profile.max_per_chapter} 个。")
        parts.extend([
            "1. 放谁嘴里：优先主角心理吐槽、旁白、熟人对话；长老/反派/正式场合不玩梗。",
            "2. 玩什么梗：优先制度类长期梗（996、KPI、画饼、摸鱼、内卷、躺平、打工人、"
            "社畜、背锅、裁员、团建、打卡），少用一年就过气的短视频梗。",
            "3. 怎么放：梗要贴着剧情长出来（仙门规矩↔职场规则），不硬塞段子，"
            "不解释「这就像现代的……」。",
            "4. 怎么收：同一个梗一章最多 1 次；连续两段不要都玩梗；"
            "死人/诀别/决战这类场合收住。",
            "5. 底线：梗不改变事件走向，不让人物说出不符合身份的话。",
        ])

    if genre:
        if _is_ancient(genre):
            parts.append(
                f"【题材提醒】本书是「{genre}」：现代梗只作为主角/旁白的错位吐槽，"
                "世界内人物不能把 996/KPI 当日常词，也不要出现手机、网络、APP 等具体现代物件"
                "（除非设定允许）。"
            )
        else:
            parts.append(
                f"【题材提醒】本书是「{genre}」：现代梗可以自然出现在对话和旁白里，但仍按上面的量。"
            )
    if voice and "文学" in str(voice):
        parts.append("【语感提醒】梗照常玩，但不要用文学腔去解释梗。")
    return "\n".join(parts)


# ── 体检：现代梗词表 ──
# 只收高信号词，避免把「合同/仲裁/法院」这类设定必需词误判成梗。
_MEME_TERMS = tuple(sorted({
    # 职场
    "996", "007", "KPI", "OKR", "HR", "PUA", "画饼", "摸鱼", "内卷",
    "躺平", "打工人", "社畜", "绩效", "年终奖", "加班费", "裁员", "优化", "毕业",
    "背锅", "甩锅", "福报", "狼性", "团建", "打卡", "钉钉",
    # 网络
    "破防", "绷不住", "整活", "开摆", "摆烂", "逆天", "抽象", "真香", "社死",
    "麻了", "急了", "草台班子", "班味", "显眼包", "搭子", "情绪价值", "上分",
    "氪金", "抽卡", "副本", "热搜", "弹幕", "朋友圈", "直播", "带货", "网红",
    "流量", "人设", "塌房", "吃瓜", "实锤", "洗白", "带节奏", "阴阳怪气",
    # 时代错位（在仙侠/历史里尤其显眼）
    "手机", "微信", "电脑", "网络", "视频", "监控", "录音", "系统提示", "面板",
    "复活币", "12345", "12333",
}, key=lambda w: (-len(w), w)))

_MEME_RE = re.compile("|".join(re.escape(w) for w in _MEME_TERMS))
_CN_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


@dataclass
class MemeMetrics:
    level: str = DEFAULT_MEME_LEVEL
    chars: int = 0
    count: int = 0
    per_k: float = 0.0
    hits: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "chars": self.chars,
            "count": self.count,
            "per_k": round(self.per_k, 1),
            "hits": dict(self.hits),
        }


def measure_memes(text: str, level: str | None = None, genre: str = "") -> MemeMetrics:
    """统计现代梗命中；短文本（<300 字）只算指标，不判超量。"""
    profile = meme_profile(level)
    cn_chars = len(_CN_CHAR_RE.findall(text or ""))
    metrics = MemeMetrics(level=profile.level, chars=cn_chars)
    if not text or cn_chars == 0:
        return metrics

    hits = _MEME_RE.findall(text)
    metrics.count = len(hits)
    metrics.hits = dict(Counter(hits).most_common(8))
    metrics.per_k = metrics.count * 1000.0 / cn_chars

    if cn_chars < 300:
        return metrics

    examples = "、".join(f"{k}×{v}" if v > 1 else k for k, v in list(metrics.hits.items())[:5])
    low, high = profile.per_chapter

    if profile.level == "无":
        if metrics.count:
            metrics.issues.append(
                f"项目梗密度=无，但检测到 {metrics.count} 个现代梗"
                + (f"：{examples}" if examples else "")
            )
        return metrics

    # 以 3000-4000 字/章为基准换算上下限，短章按比例放宽
    scale = max(0.5, min(1.5, cn_chars / 3500.0))
    min_target = max(1, int(round(low * scale)))
    max_target = max(1, int(round(high * scale)))
    target_text = (f"{min_target}–{max_target}" if min_target != max_target else f"约 {min_target}")
    # 「少」是可选调味，0 个不算问题；「中/高」才要求达到下限
    if metrics.count < min_target and profile.level in ("中", "高"):
        metrics.issues.append(
            f"现代梗偏少：{metrics.count} 个（{profile.level}档建议 {target_text} 个）"
        )
    elif metrics.count > max_target:
        metrics.issues.append(
            f"现代梗偏多：{metrics.count} 个（{profile.level}档建议 {target_text} 个），"
            "容易出戏"
        )
    repeated = [f"「{k}」{v} 次" for k, v in metrics.hits.items() if v >= 3]
    if repeated:
        metrics.issues.append("同一个梗重复太多：" + "、".join(repeated))
    return metrics


def meme_issues(text: str, level: str | None = None, genre: str = "") -> list[str]:
    return measure_memes(text, level, genre).issues
