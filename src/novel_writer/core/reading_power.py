"""追读力系统 — 量化读者体验，追踪章节吸引力。

5 种钩子（Hook）：
- 危机钩（crisis）：危险逼近
- 悬念钩（mystery）：信息缺口
- 渴望钩（desire）：期待回报
- 情绪钩（emotion）：强烈情绪反应
- 选择钩（choice）：两难抉择

6 种爽点（Cool-point）：
- 打脸反转：被小看后实力碾压
- 底牌揭示：隐藏身份/实力曝光
- 逆袭胜利：弱者战胜强者
- 权威挑战：挑战上位者
- 反派落败：恶人受惩罚
- 甜蜜惊喜：意外的好事

微兑现（Micro-payoff）：章节级小奖励
- 信息/关系/能力/资源/认可/情感/伏笔回收

债务追踪（Debt）：软违规累积的阅读债务
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

READING_POWER_FILE = "reading_power.json"

HOOK_TYPES = ["crisis", "mystery", "desire", "emotion", "choice"]
HOOK_LABELS = {
    "crisis": "危机钩", "mystery": "悬念钩", "desire": "渴望钩",
    "emotion": "情绪钩", "choice": "选择钩",
}
COOL_POINTS = [
    "打脸反转", "底牌揭示", "逆袭胜利", "权威挑战", "反派落败", "甜蜜惊喜",
    "迪化误解", "身份掉马",
]
MICRO_PAYOFFS = [
    "信息", "关系", "能力", "资源", "认可", "情感", "伏笔回收",
]


@dataclass
class ChapterReadingPower:
    chapter: int = 0
    hook_type: str = ""          # 主要钩子类型
    hook_strength: str = "medium"  # strong / medium / weak
    cool_points: list[str] = field(default_factory=list)  # 爽点模式
    micro_payoffs: list[str] = field(default_factory=list)  # 微兑现
    hard_violations: list[str] = field(default_factory=list)  # 硬违规
    soft_suggestions: list[str] = field(default_factory=list)  # 软建议
    debt_balance: float = 0.0  # 当前债务余额
    score: float = 0.0  # 综合得分 0-100


class ReadingPowerTracker:
    """项目级追读力追踪器。"""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self._path = project_dir / READING_POWER_FILE
        self._data: list[dict] = []
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._data = []

    def save(self):
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def record(self, rp: ChapterReadingPower):
        """记录一章的追读力数据。"""
        # 去重
        self._data = [d for d in self._data if d.get("chapter") != rp.chapter]
        self._data.append(asdict(rp))
        self._data.sort(key=lambda x: x.get("chapter", 0))
        self.save()

    def get_recent(self, n: int = 5) -> list[dict]:
        """获取最近 n 章的追读力数据。"""
        return self._data[-n:]

    def get_hook_stats(self, window: int = 10) -> dict[str, int]:
        """统计最近 window 章的钩子类型分布。"""
        recent = self._data[-window:]
        stats: dict[str, int] = {h: 0 for h in HOOK_TYPES}
        for d in recent:
            ht = d.get("hook_type", "")
            if ht in stats:
                stats[ht] += 1
        return stats

    def get_cool_point_stats(self, window: int = 10) -> dict[str, int]:
        """统计最近 window 章的爽点使用频率。"""
        recent = self._data[-window:]
        stats: dict[str, int] = {}
        for d in recent:
            for cp in d.get("cool_points", []):
                stats[cp] = stats.get(cp, 0) + 1
        return stats

    def get_debt_total(self) -> float:
        """获取当前总债务。"""
        return sum(d.get("debt_balance", 0) for d in self._data)

    def build_guidance(self, next_chapter: int) -> str:
        """基于追读力数据生成下一章的写作指导。"""
        if not self._data:
            return ""

        lines = ["【追读力指导】"]
        recent = self._data[-3:]

        # 1. 钩子多样性检查
        hook_stats = self.get_hook_stats(10)
        total_hooks = sum(hook_stats.values())
        if total_hooks > 0:
            overused = [h for h, c in hook_stats.items() if c / total_hooks > 0.5]
            if overused:
                labels = [HOOK_LABELS.get(h, h) for h in overused]
                lines.append(f"- 钩子类型过于集中在「{'、'.join(labels)}」，本章尝试其他类型")

        # 2. 爽点节奏检查
        recent_with_cp = [d for d in recent if d.get("cool_points")]
        if len(recent) >= 3 and len(recent_with_cp) == 0:
            lines.append("- 最近3章没有爽点，本章需要安排至少一个爽点")

        # 3. 微兑现密度检查
        recent_with_mp = [d for d in recent if d.get("micro_payoffs")]
        if len(recent) >= 2 and len(recent_with_mp) == 0:
            lines.append("- 最近2章没有微兑现，本章需要给读者一些小回报")

        # 4. 债务警告
        debt = self.get_debt_total()
        if debt > 5:
            lines.append(f"- 当前阅读债务 {debt:.1f}，需要尽快安排回报")

        # 5. 得分趋势
        scores = [d.get("score", 0) for d in recent if d.get("score")]
        if len(scores) >= 2:
            trend = scores[-1] - scores[0]
            if trend < -10:
                lines.append("- 得分呈下降趋势，需要提升本章吸引力")
            elif trend > 10:
                lines.append("- 得分上升中，保持当前节奏")

        if len(lines) <= 1:
            return ""

        return "\n".join(lines)

    def analyze_chapter(self, content: str, chapter: int) -> ChapterReadingPower:
        """自动分析章节的追读力指标（基于关键词）。"""
        rp = ChapterReadingPower(chapter=chapter)

        # 钩子检测
        hook_keywords = {
            "crisis": ["危险", "死", "杀", "逃", "追", "攻击", "爆炸", "崩塌", "坠落"],
            "mystery": ["秘密", "真相", "谜", "奇怪", "诡异", "不可思议", "为什么", "到底"],
            "desire": ["想要", "渴望", "追求", "梦想", "希望", "等待", "期待"],
            "emotion": ["愤怒", "悲伤", "绝望", "感动", "心碎", "泪", "笑", "恨"],
            "choice": ["选择", "抉择", "两难", "要么", "必须", "不得不", "放弃"],
        }
        max_count = 0
        for hook_type, keywords in hook_keywords.items():
            count = sum(content.count(kw) for kw in keywords)
            if count > max_count:
                max_count = count
                rp.hook_type = hook_type
        if max_count >= 5:
            rp.hook_strength = "strong"
        elif max_count >= 2:
            rp.hook_strength = "medium"
        else:
            rp.hook_strength = "weak"

        # 爽点检测
        cool_keywords = {
            "打脸反转": ["打脸", "嘲讽", "不屑", "小看", "碾压"],
            "底牌揭示": ["底牌", "隐藏", "真正", "其实", "原来"],
            "逆袭胜利": ["逆袭", "翻盘", "反杀", "绝地", "逆转"],
            "反派落败": ["报应", "活该", "罪有应得", "落败", "崩溃"],
        }
        for cp, keywords in cool_keywords.items():
            if any(kw in content for kw in keywords):
                rp.cool_points.append(cp)

        # 微兑现检测
        micro_keywords = {
            "信息": ["得知", "发现", "明白", "了解", "知道"],
            "能力": ["学会", "掌握", "突破", "领悟", "觉醒"],
            "关系": ["信任", "结盟", "友谊", "和解", "认可"],
            "资源": ["获得", "得到", "收获", "宝物", "功法"],
        }
        for mp, keywords in micro_keywords.items():
            if any(kw in content for kw in keywords):
                rp.micro_payoffs.append(mp)

        # 计算得分
        score = 60  # 基准分
        if rp.hook_strength == "strong":
            score += 15
        elif rp.hook_strength == "medium":
            score += 8
        score += len(rp.cool_points) * 5
        score += len(rp.micro_payoffs) * 3
        rp.score = min(100, max(0, score))

        return rp
