"""reading_power 模块的单元测试。"""
import pytest
import json
from pathlib import Path
from src.novel_writer.core.reading_power import (
    ReadingPowerTracker, ChapterReadingPower,
    HOOK_TYPES, HOOK_LABELS, COOL_POINTS, MICRO_PAYOFFS
)


@pytest.fixture
def tracker(tmp_path):
    """创建临时目录的追踪器。"""
    return ReadingPowerTracker(tmp_path)


@pytest.fixture
def sample_chapter():
    """示例章节内容。"""
    return """凌尘握紧青霜剑，心中涌起一股莫名的危机感。

"小心！"他大喝一声，推开身边的师姐。

就在这一瞬间，一道黑影从暗处窜出，带着凌厉的杀意直扑而来。

凌尘本能地挥剑格挡，剑身与黑影碰撞，发出刺耳的金属声。他被震得倒退数步，虎口发麻。

"好强的力量！"他心中暗惊。

黑影在空中盘旋一圈，再次袭来。这次凌尘看清了，那是一只通体漆黑的巨蟒，双眼泛着血红色的光芒。

"孽畜！休得猖狂！"师姐祭出法宝，一道金光射向巨蟒。

巨蟒灵活闪避，尾巴横扫，将师姐击飞出去。

"师姐！"凌尘大喝一声，体内灵力疯狂运转。青霜剑爆发出耀眼的蓝色光芒，剑气纵横，将巨蟒逼退。

就在这时，他突然发现了巨蟒的破绽——它的眼睛！

"就是现在！"凌尘抓住机会，一剑刺向巨蟒的左眼。

"啊——"巨蟒发出惨叫，鲜血飞溅。

原来，这只巨蟒的弱点就是眼睛！凌尘心中一喜，趁胜追击。

经过一番激战，巨蟒终于倒下。凌尘单膝跪地，大口喘息着。

"你...你做到了！"师姐难以置信地看着他，"你竟然打败了它！"

凌尘感受着体内全新的力量，嘴角露出一丝微笑："是的，我做到了。"

这场战斗，让他领悟了一个道理：真正的力量，来自于对敌人的了解和对时机的把握。
"""


class TestReadingPowerTracker:
    """ReadingPowerTracker 测试类。"""

    def test_record(self, tracker):
        """测试记录功能。"""
        rp = ChapterReadingPower(
            chapter=1,
            hook_type="crisis",
            hook_strength="strong",
            cool_points=["逆袭胜利"],
            micro_payoffs=["能力"],
            score=85.0
        )
        tracker.record(rp)
        assert len(tracker._data) == 1
        assert tracker._data[0]["chapter"] == 1
        assert tracker._data[0]["score"] == 85.0

    def test_record_dedup(self, tracker):
        """测试去重功能。"""
        rp1 = ChapterReadingPower(chapter=1, score=80.0)
        rp2 = ChapterReadingPower(chapter=1, score=90.0)
        tracker.record(rp1)
        tracker.record(rp2)
        assert len(tracker._data) == 1
        assert tracker._data[0]["score"] == 90.0

    def test_get_recent(self, tracker):
        """测试获取最近记录。"""
        for i in range(10):
            rp = ChapterReadingPower(chapter=i+1, score=float(i*10))
            tracker.record(rp)
        recent = tracker.get_recent(3)
        assert len(recent) == 3
        assert recent[0]["chapter"] == 8
        assert recent[1]["chapter"] == 9
        assert recent[2]["chapter"] == 10

    def test_get_hook_stats(self, tracker):
        """测试钩子统计。"""
        rp1 = ChapterReadingPower(chapter=1, hook_type="crisis")
        rp2 = ChapterReadingPower(chapter=2, hook_type="mystery")
        rp3 = ChapterReadingPower(chapter=3, hook_type="crisis")
        tracker.record(rp1)
        tracker.record(rp2)
        tracker.record(rp3)
        stats = tracker.get_hook_stats(10)
        assert stats["crisis"] == 2
        assert stats["mystery"] == 1

    def test_get_cool_point_stats(self, tracker):
        """测试爽点统计。"""
        rp1 = ChapterReadingPower(chapter=1, cool_points=["逆袭胜利", "打脸反转"])
        rp2 = ChapterReadingPower(chapter=2, cool_points=["逆袭胜利"])
        tracker.record(rp1)
        tracker.record(rp2)
        stats = tracker.get_cool_point_stats(10)
        assert stats["逆袭胜利"] == 2
        assert stats["打脸反转"] == 1

    def test_get_debt_total(self, tracker):
        """测试债务总计。"""
        rp1 = ChapterReadingPower(chapter=1, debt_balance=2.5)
        rp2 = ChapterReadingPower(chapter=2, debt_balance=3.0)
        tracker.record(rp1)
        tracker.record(rp2)
        assert tracker.get_debt_total() == 5.5

    def test_build_guidance_empty(self, tracker):
        """测试空数据时的指导。"""
        guidance = tracker.build_guidance(1)
        assert guidance == ""

    def test_build_guidance_hook_overuse(self, tracker):
        """测试钩子过度使用的指导。"""
        for i in range(10):
            rp = ChapterReadingPower(chapter=i+1, hook_type="crisis")
            tracker.record(rp)
        guidance = tracker.build_guidance(11)
        assert "钩子类型过于集中" in guidance

    def test_build_guidance_no_cool_points(self, tracker):
        """测试无爽点时的指导。"""
        for i in range(3):
            rp = ChapterReadingPower(chapter=i+1, cool_points=[])
            tracker.record(rp)
        guidance = tracker.build_guidance(4)
        assert "没有爽点" in guidance

    def test_build_guidance_high_debt(self, tracker):
        """测试高债务时的指导。"""
        rp = ChapterReadingPower(chapter=1, debt_balance=10.0)
        tracker.record(rp)
        guidance = tracker.build_guidance(2)
        assert "阅读债务" in guidance

    def test_build_guidance_score_declining(self, tracker):
        """测试得分下降时的指导。"""
        for i in range(3):
            rp = ChapterReadingPower(chapter=i+1, score=float(100 - i*20))
            tracker.record(rp)
        guidance = tracker.build_guidance(4)
        assert "下降趋势" in guidance

    def test_analyze_chapter_hook_type(self, tracker, sample_chapter):
        """测试章节分析的钩子类型。"""
        rp = tracker.analyze_chapter(sample_chapter, 1)
        assert rp.hook_type in HOOK_TYPES

    def test_analyze_chapter_hook_strength(self, tracker, sample_chapter):
        """测试章节分析的钩子强度。"""
        rp = tracker.analyze_chapter(sample_chapter, 1)
        assert rp.hook_strength in ["strong", "medium", "weak"]

    def test_analyze_chapter_cool_points(self, tracker, sample_chapter):
        """测试章节分析的爽点。"""
        rp = tracker.analyze_chapter(sample_chapter, 1)
        for cp in rp.cool_points:
            assert cp in COOL_POINTS

    def test_analyze_chapter_micro_payoffs(self, tracker, sample_chapter):
        """测试章节分析的微兑现。"""
        rp = tracker.analyze_chapter(sample_chapter, 1)
        for mp in rp.micro_payoffs:
            assert mp in MICRO_PAYOFFS

    def test_analyze_chapter_score(self, tracker, sample_chapter):
        """测试章节分析的得分。"""
        rp = tracker.analyze_chapter(sample_chapter, 1)
        assert 0 <= rp.score <= 100

    def test_save_and_load(self, tracker):
        """测试保存和加载。"""
        rp = ChapterReadingPower(chapter=1, score=85.0)
        tracker.record(rp)
        # 创建新的追踪器实例
        new_tracker = ReadingPowerTracker(tracker.project_dir)
        assert len(new_tracker._data) == 1
        assert new_tracker._data[0]["score"] == 85.0


class TestChapterReadingPower:
    """ChapterReadingPower 测试类。"""

    def test_default_values(self):
        """测试默认值。"""
        rp = ChapterReadingPower()
        assert rp.chapter == 0
        assert rp.hook_type == ""
        assert rp.hook_strength == "medium"
        assert rp.cool_points == []
        assert rp.micro_payoffs == []
        assert rp.hard_violations == []
        assert rp.soft_suggestions == []
        assert rp.debt_balance == 0.0
        assert rp.score == 0.0

    def test_custom_values(self):
        """测试自定义值。"""
        rp = ChapterReadingPower(
            chapter=5,
            hook_type="crisis",
            hook_strength="strong",
            cool_points=["逆袭胜利"],
            micro_payoffs=["能力", "资源"],
            score=90.0
        )
        assert rp.chapter == 5
        assert rp.hook_type == "crisis"
        assert rp.hook_strength == "strong"
        assert rp.cool_points == ["逆袭胜利"]
        assert rp.micro_payoffs == ["能力", "资源"]
        assert rp.score == 90.0
