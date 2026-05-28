"""反模式追踪 — 审稿发现的 AI 味问题自动反馈到写作约束。

工作流：
1. 审稿 agent 发现 ai_flavor 问题 → 写入 anti_patterns.json
2. 下次写章节时 → 读取反模式 → 注入写作约束
3. 问题解决后 → 自动清理已过期的反模式
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

ANTI_PATTERNS_FILE = "anti_patterns.json"

# AI 味常见模式
DEFAULT_ANTI_PATTERNS = [
    "避免使用「不禁」「忍不住」「竟然」「居然」等 AI 高频词",
    "避免每段结尾都用省略号或感叹号收束",
    "避免对话全是信息倾倒，要有言外之意和潜台词",
    "避免心理描写用「他心想」「他暗想」等直白开头",
    "避免场景描写用「映入眼帘」「放眼望去」等模板句",
    "避免情绪标签化（「他感到一阵愤怒」），要用动作和细节传达",
    "避免节奏均匀，要有快慢松紧的变化",
]


@dataclass
class AntiPattern:
    pattern: str          # 反模式描述
    severity: str = "medium"  # low / medium / high
    source_chapter: int = 0   # 来源章节
    evidence: str = ""        # 原文证据
    category: str = "ai_flavor"  # ai_flavor / pacing / character / logic


class AntiPatternTracker:
    """项目级反模式追踪器。"""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self._path = project_dir / ANTI_PATTERNS_FILE
        self._patterns: list[dict] = []
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                self._patterns = json.loads(self._path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._patterns = []

    def save(self):
        self._path.write_text(
            json.dumps(self._patterns, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add(self, pattern: AntiPattern):
        """添加反模式，去重。"""
        for p in self._patterns:
            if p.get("pattern") == pattern.pattern:
                # 更新证据和来源
                p["source_chapter"] = pattern.source_chapter
                p["evidence"] = pattern.evidence
                self.save()
                return
        self._patterns.append(asdict(pattern))
        self.save()

    def add_from_review(self, review_text: str, chapter: int):
        """从审稿报告中提取 AI 味问题并记录。"""
        # 提取 ai_flavor 相关的问题
        lines = review_text.split("\n")
        current_issue = ""
        for line in lines:
            line = line.strip()
            if not line:
                if current_issue:
                    self.add(AntiPattern(
                        pattern=current_issue,
                        severity="medium",
                        source_chapter=chapter,
                        category="ai_flavor",
                    ))
                    current_issue = ""
                continue
            # 匹配审稿报告中的问题描述
            if any(kw in line for kw in ["AI感", "ai_flavor", "AI味", "模板化", "机械化", "不自然"]):
                current_issue = line.lstrip("- •·0123456789.、）)")
        if current_issue:
            self.add(AntiPattern(
                pattern=current_issue,
                severity="medium",
                source_chapter=chapter,
                category="ai_flavor",
            ))

    def get_constraint_text(self, max_items: int = 8) -> str:
        """生成反模式约束文本，注入写作上下文。"""
        if not self._patterns:
            # 使用默认反模式
            lines = ["【写作禁忌 · AI 味检查】"]
            for p in DEFAULT_ANTI_PATTERNS:
                lines.append(f"- {p}")
            return "\n".join(lines)

        lines = ["【写作禁忌 · 基于审稿反馈】"]
        # 按严重度排序
        severity_order = {"high": 0, "medium": 1, "low": 2}
        sorted_patterns = sorted(
            self._patterns,
            key=lambda x: severity_order.get(x.get("severity", "medium"), 1),
        )
        for p in sorted_patterns[:max_items]:
            ch = p.get("source_chapter", 0)
            ev = p.get("evidence", "")
            desc = p.get("pattern", "")
            if ev:
                lines.append(f"- {desc}（第{ch}章例：{ev[:50]}）")
            else:
                lines.append(f"- {desc}")

        # 补充默认反模式
        existing = {p.get("pattern", "") for p in self._patterns}
        for dp in DEFAULT_ANTI_PATTERNS:
            if not any(dp[:10] in e for e in existing):
                lines.append(f"- {dp}")

        return "\n".join(lines)

    def clear_resolved(self, chapter: int):
        """清理来源章节距今超过 10 章的低严重度反模式。"""
        self._patterns = [
            p for p in self._patterns
            if p.get("severity") == "high"
            or chapter - p.get("source_chapter", 0) <= 10
        ]
        self.save()
