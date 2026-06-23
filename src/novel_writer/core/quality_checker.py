"""质量检查模块 — 自动评估章节质量。

评估维度：
- 篇幅检查：字数是否在合理范围内
- 结构检查：是否有开头、发展、结尾
- 对话检查：对话是否生动，是否有对话标签多样性
- 场景检查：是否有五感描写
- 节奏检查：是否有情节转折
- 钩子检查：是否有吸引读者的钩子
- 爽点检查：是否有读者爽点
- 微兑现检查：是否有小回报
- 一致性检查：角色名、人称是否一致
- 句式检查：是否有重复句式
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)


@dataclass
class QualityReport:
    """质量检查报告。"""
    chapter: int = 0
    total_score: float = 0.0  # 总分 0-100
    word_count: int = 0  # 字数
    word_count_score: float = 0.0  # 篇幅得分
    structure_score: float = 0.0  # 结构得分
    dialogue_score: float = 0.0  # 对话得分
    scene_score: float = 0.0  # 场景得分
    rhythm_score: float = 0.0  # 节奏得分
    hook_score: float = 0.0  # 钩子得分
    cool_point_score: float = 0.0  # 爽点得分
    micro_payoff_score: float = 0.0  # 微兑现得分
    consistency_score: float = 0.0  # 一致性得分
    sentence_variety_score: float = 0.0  # 句式多样性得分
    issues: list[str] = field(default_factory=list)  # 问题列表
    suggestions: list[str] = field(default_factory=list)  # 建议列表


class QualityChecker:
    """质量检查器。"""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir

    def check_chapter(self, content: str, chapter: int) -> QualityReport:
        """检查章节质量，返回报告。"""
        report = QualityReport(chapter=chapter)
        
        # 篇幅检查
        self._check_word_count(content, report)
        
        # 结构检查
        self._check_structure(content, report)
        
        # 对话检查
        self._check_dialogue(content, report)
        
        # 场景检查
        self._check_scene(content, report)
        
        # 节奏检查
        self._check_rhythm(content, report)
        
        # 钩子检查
        self._check_hook(content, report)
        
        # 爽点检查
        self._check_cool_points(content, report)
        
        # 微兑现检查
        self._check_micro_payoffs(content, report)
        
        # 一致性检查
        self._check_consistency(content, report)
        
        # 句式多样性检查
        self._check_sentence_variety(content, report)
        
        # 计算总分
        self._calculate_total_score(report)
        
        return report

    def _check_word_count(self, content: str, report: QualityReport):
        """检查字数。"""
        # 统计中文字符和英文单词
        cn_chars = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
        en_words = len(re.findall(r'[a-zA-Z]+', content))
        report.word_count = cn_chars + en_words
        
        # 评分：3000-4000字为最佳
        if 3000 <= report.word_count <= 4000:
            report.word_count_score = 100
        elif 2500 <= report.word_count < 3000 or 4000 < report.word_count <= 4500:
            report.word_count_score = 80
        elif 2000 <= report.word_count < 2500 or 4500 < report.word_count <= 5000:
            report.word_count_score = 60
        else:
            report.word_count_score = 40
            if report.word_count < 2000:
                report.issues.append(f"字数过少：{report.word_count}字（建议3000-4000字）")
            else:
                report.issues.append(f"字数过多：{report.word_count}字（建议3000-4000字）")

    def _check_structure(self, content: str, report: QualityReport):
        """检查结构。"""
        lines = content.split('\n')
        non_empty_lines = [line.strip() for line in lines if line.strip()]
        
        # 检查是否有标题
        has_title = any(line.startswith('#') for line in non_empty_lines[:5])
        
        # 检查段落数量
        paragraphs = [p for p in content.split('\n\n') if p.strip()]
        
        # 评分
        score = 60  # 基准分
        if has_title:
            score += 10
        if len(paragraphs) >= 10:
            score += 15
        elif len(paragraphs) >= 5:
            score += 10
        else:
            report.issues.append("段落过少，结构不清晰")
        
        # 检查是否有明显的开头、发展、结尾
        if len(content) > 1000:
            first_part = content[:len(content)//3]
            middle_part = content[len(content)//3:2*len(content)//3]
            last_part = content[2*len(content)//3:]
            
            # 检查开头是否有吸引力
            if any(kw in first_part for kw in ['？', '?', '！', '!', '突然', '忽然']):
                score += 10
            
            # 检查结尾是否有钩子
            if any(kw in last_part for kw in ['？', '?', '！', '!', '然而', '但是', '却']):
                score += 10
        
        report.structure_score = min(100, score)

    def _check_dialogue(self, content: str, report: QualityReport):
        """检查对话质量。"""
        # 统计对话数量
        dialogues = re.findall(r'["「"](.*?)["」"]', content)
        dialogue_count = len(dialogues)
        
        # 统计对话标签
        dialogue_tags = re.findall(r'(说|道|问|答|喊|叫|笑|哭|怒|惊|叹)', content)
        
        # 统计动作描写
        action_descriptions = re.findall(r'(他|她|我|你|他们|她们|我们|你们)(.{2,10}(?:着|了|过|起来|下去|上来|下来|出去|进来|出来|进去))', content)
        
        score = 60  # 基准分
        
        # 对话数量评分
        if dialogue_count >= 10:
            score += 15
        elif dialogue_count >= 5:
            score += 10
        elif dialogue_count < 3:
            report.issues.append("对话过少，建议增加对话")
        
        # 对话标签多样性
        unique_tags = set(dialogue_tags)
        if len(unique_tags) >= 5:
            score += 15
        elif len(unique_tags) >= 3:
            score += 10
        else:
            report.suggestions.append("对话标签过于单一，建议使用更多样的表达")
        
        # 动作描写
        if len(action_descriptions) >= 5:
            score += 10
        
        report.dialogue_score = min(100, score)

    def _check_scene(self, content: str, report: QualityReport):
        """检查场景描写。"""
        # 五感描写关键词
        visual_keywords = ['看', '见', '望', '视', '瞧', '盯', '瞪', '瞅']
        auditory_keywords = ['听', '闻', '声', '音', '响', '鸣', '叫']
        tactile_keywords = ['触', '摸', '感', '觉', '冷', '热', '软', '硬']
        olfactory_keywords = ['香', '臭', '味', '气', '息']
        gustatory_keywords = ['甜', '苦', '酸', '辣', '咸']
        
        # 统计各感官描写
        visual_count = sum(content.count(kw) for kw in visual_keywords)
        auditory_count = sum(content.count(kw) for kw in auditory_keywords)
        tactile_count = sum(content.count(kw) for kw in tactile_keywords)
        olfactory_count = sum(content.count(kw) for kw in olfactory_keywords)
        gustatory_count = sum(content.count(kw) for kw in gustatory_keywords)
        
        # 计算感官多样性
        sensory_types = sum(1 for count in [visual_count, auditory_count, tactile_count, olfactory_count, gustatory_count] if count > 0)
        
        score = 60  # 基准分
        
        # 感官多样性评分
        if sensory_types >= 4:
            score += 20
        elif sensory_types >= 3:
            score += 15
        elif sensory_types >= 2:
            score += 10
        else:
            report.suggestions.append("场景描写角度单一，建议使用多种感官描写")
        
        # 描写密度
        total_descriptions = visual_count + auditory_count + tactile_count + olfactory_count + gustatory_count
        if total_descriptions >= 20:
            score += 20
        elif total_descriptions >= 10:
            score += 15
        elif total_descriptions >= 5:
            score += 10
        
        report.scene_score = min(100, score)

    def _check_rhythm(self, content: str, report: QualityReport):
        """检查节奏。"""
        # 检查情节转折
        transition_keywords = ['突然', '忽然', '然而', '但是', '却', '竟然', '居然', '没想到', '殊不知']
        transition_count = sum(content.count(kw) for kw in transition_keywords)
        
        # 检查紧张感
        tension_keywords = ['危险', '死', '杀', '逃', '追', '攻击', '战斗', '冲突']
        tension_count = sum(content.count(kw) for kw in tension_keywords)
        
        score = 60  # 基准分
        
        # 转折点评分
        if transition_count >= 3:
            score += 20
        elif transition_count >= 2:
            score += 15
        elif transition_count >= 1:
            score += 10
        else:
            report.suggestions.append("情节转折较少，建议增加意外事件")
        
        # 紧张感评分
        if tension_count >= 5:
            score += 20
        elif tension_count >= 3:
            score += 15
        elif tension_count >= 1:
            score += 10
        
        report.rhythm_score = min(100, score)

    def _check_hook(self, content: str, report: QualityReport):
        """检查钩子。"""
        # 钩子关键词
        hook_keywords = {
            'crisis': ['危险', '死', '杀', '逃', '追', '攻击', '爆炸', '崩塌', '坠落'],
            'mystery': ['秘密', '真相', '谜', '奇怪', '诡异', '不可思议', '为什么', '到底'],
            'desire': ['想要', '渴望', '追求', '梦想', '希望', '等待', '期待'],
            'emotion': ['愤怒', '悲伤', '绝望', '感动', '心碎', '泪', '笑', '恨'],
            'choice': ['选择', '抉择', '两难', '要么', '必须', '不得不', '放弃'],
        }
        
        # 统计钩子类型
        hook_counts = {}
        for hook_type, keywords in hook_keywords.items():
            count = sum(content.count(kw) for kw in keywords)
            if count > 0:
                hook_counts[hook_type] = count
        
        score = 60  # 基准分
        
        # 钩子多样性评分
        if len(hook_counts) >= 3:
            score += 20
        elif len(hook_counts) >= 2:
            score += 15
        elif len(hook_counts) >= 1:
            score += 10
        else:
            report.suggestions.append("缺少吸引读者的钩子，建议增加悬念或冲突")
        
        # 钩子强度
        max_hook_count = max(hook_counts.values()) if hook_counts else 0
        if max_hook_count >= 5:
            score += 20
        elif max_hook_count >= 3:
            score += 15
        elif max_hook_count >= 1:
            score += 10
        
        report.hook_score = min(100, score)

    def _check_cool_points(self, content: str, report: QualityReport):
        """检查爽点。"""
        cool_keywords = {
            '打脸反转': ['打脸', '嘲讽', '不屑', '小看', '碾压'],
            '底牌揭示': ['底牌', '隐藏', '真正', '其实', '原来'],
            '逆袭胜利': ['逆袭', '翻盘', '反杀', '绝地', '逆转'],
            '反派落败': ['报应', '活该', '罪有应得', '落败', '崩溃'],
        }
        
        found_cool_points = []
        for cp, keywords in cool_keywords.items():
            if any(kw in content for kw in keywords):
                found_cool_points.append(cp)
        
        score = 60  # 基准分
        
        # 爽点评分
        if len(found_cool_points) >= 2:
            score += 25
        elif len(found_cool_points) >= 1:
            score += 15
        else:
            report.suggestions.append("缺少爽点，建议安排读者期待的情节")
        
        report.cool_point_score = min(100, score)

    def _check_micro_payoffs(self, content: str, report: QualityReport):
        """检查微兑现。"""
        micro_keywords = {
            '信息': ['得知', '发现', '明白', '了解', '知道'],
            '能力': ['学会', '掌握', '突破', '领悟', '觉醒'],
            '关系': ['信任', '结盟', '友谊', '和解', '认可'],
            '资源': ['获得', '得到', '收获', '宝物', '功法'],
        }
        
        found_micro_payoffs = []
        for mp, keywords in micro_keywords.items():
            if any(kw in content for kw in keywords):
                found_micro_payoffs.append(mp)
        
        score = 60  # 基准分
        
        # 微兑现评分
        if len(found_micro_payoffs) >= 2:
            score += 25
        elif len(found_micro_payoffs) >= 1:
            score += 15
        else:
            report.suggestions.append("缺少微兑现，建议给读者一些小回报")
        
        report.micro_payoff_score = min(100, score)

    def _check_consistency(self, content: str, report: QualityReport):
        """检查一致性。"""
        # 提取角色名
        character_names = set()
        # 从对话中提取
        for match in re.finditer(r'["「"](.*?)["」"]', content):
            dialogue = match.group(1)
            # 简单提取：如果对话中有"我"、"你"等，可能是角色
            if '我' in dialogue:
                character_names.add('我')
            if '你' in dialogue:
                character_names.add('你')
        
        # 检查人称一致性
        first_person_count = content.count('我')
        third_person_count = content.count('他') + content.count('她')
        
        score = 60  # 基准分
        
        # 人称一致性
        if first_person_count > 0 and third_person_count > 0:
            # 混合人称可能有问题
            ratio = min(first_person_count, third_person_count) / max(first_person_count, third_person_count)
            if ratio > 0.3:
                report.issues.append("人称使用不一致，建议统一人称")
                score -= 10
        
        # 检查角色名一致性
        # 这里简化处理，实际应该更复杂
        report.consistency_score = min(100, max(0, score))

    def _check_sentence_variety(self, content: str, report: QualityReport):
        """检查句式多样性。"""
        # 提取句子
        sentences = re.split(r'[。！？]', content)
        sentences = [s.strip() for s in sentences if s.strip()]
        
        if len(sentences) < 5:
            report.sentence_variety_score = 60
            return
        
        # 检查句式开头
        sentence_starts = []
        for sentence in sentences[:20]:  # 只检查前20句
            if len(sentence) >= 2:
                sentence_starts.append(sentence[:2])
        
        # 计算重复率
        if sentence_starts:
            unique_starts = set(sentence_starts)
            variety_ratio = len(unique_starts) / len(sentence_starts)
        else:
            variety_ratio = 0.5
        
        score = 60  # 基准分
        
        # 多样性评分
        if variety_ratio >= 0.8:
            score += 25
        elif variety_ratio >= 0.6:
            score += 15
        elif variety_ratio >= 0.4:
            score += 10
        else:
            report.issues.append("句式开头过于重复，建议增加变化")
        
        # 检查高频词汇
        high_freq_words = ['突然', '不禁', '竟然', '居然', '忽然']
        for word in high_freq_words:
            count = content.count(word)
            if count > 3:
                report.suggestions.append(f"「{word}」使用过多（{count}次），建议替换")
                score -= 5
        
        report.sentence_variety_score = min(100, max(0, score))

    def _calculate_total_score(self, report: QualityReport):
        """计算总分。"""
        # 各维度权重
        weights = {
            'word_count': 0.1,
            'structure': 0.1,
            'dialogue': 0.15,
            'scene': 0.1,
            'rhythm': 0.15,
            'hook': 0.1,
            'cool_point': 0.1,
            'micro_payoff': 0.05,
            'consistency': 0.1,
            'sentence_variety': 0.05,
        }
        
        # 计算加权总分
        total = (
            report.word_count_score * weights['word_count'] +
            report.structure_score * weights['structure'] +
            report.dialogue_score * weights['dialogue'] +
            report.scene_score * weights['scene'] +
            report.rhythm_score * weights['rhythm'] +
            report.hook_score * weights['hook'] +
            report.cool_point_score * weights['cool_point'] +
            report.micro_payoff_score * weights['micro_payoff'] +
            report.consistency_score * weights['consistency'] +
            report.sentence_variety_score * weights['sentence_variety']
        )
        
        report.total_score = min(100, max(0, total))

    def generate_report_text(self, report: QualityReport) -> str:
        """生成报告文本。"""
        lines = [
            f"## 第{report.chapter}章 质量检查报告",
            "",
            f"**总分：{report.total_score:.1f}/100**",
            "",
            "### 各维度得分",
            f"- 篇幅：{report.word_count_score:.1f}（{report.word_count}字）",
            f"- 结构：{report.structure_score:.1f}",
            f"- 对话：{report.dialogue_score:.1f}",
            f"- 场景：{report.scene_score:.1f}",
            f"- 节奏：{report.rhythm_score:.1f}",
            f"- 钩子：{report.hook_score:.1f}",
            f"- 爽点：{report.cool_point_score:.1f}",
            f"- 微兑现：{report.micro_payoff_score:.1f}",
            f"- 一致性：{report.consistency_score:.1f}",
            f"- 句式多样性：{report.sentence_variety_score:.1f}",
        ]
        
        if report.issues:
            lines.append("")
            lines.append("### 问题")
            for issue in report.issues:
                lines.append(f"- {issue}")
        
        if report.suggestions:
            lines.append("")
            lines.append("### 建议")
            for suggestion in report.suggestions:
                lines.append(f"- {suggestion}")
        
        return "\n".join(lines)
