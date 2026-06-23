"""quality_checker 模块的单元测试。"""
import pytest
from pathlib import Path
from src.novel_writer.core.quality_checker import QualityChecker, QualityReport


@pytest.fixture
def checker(tmp_path):
    """创建临时目录的检查器。"""
    return QualityChecker(tmp_path)


@pytest.fixture
def sample_chapter():
    """示例章节内容。"""
    return """# 第一章 风起云涌

清晨的阳光透过窗帘洒进房间，凌尘睁开双眼，感受着体内灵力的涌动。

"今天是突破的关键时刻。"他自言自语道。

突然，门外传来急促的敲门声。

"凌尘！快开门！"是师姐焦急的声音。

他猛地站起，椅子被推得"吱"一声。三步并作两步冲到门前，一把拉开门栓。

"出大事了！"师姐脸色苍白，"禁地的封印...松动了！"

凌尘心中一震。禁地封印千年未动，怎么会突然松动？

"走！去看看！"他抓起桌上的青霜剑，跟着师姐冲出门去。

两人御剑飞行，转眼间来到禁地上空。只见原本平静的山谷此刻黑气冲天，地面裂开一道道缝隙。

"这..."凌尘倒吸一口凉气。

就在这时，一道黑影从裂缝中窜出，直扑向他！

"小心！"师姐惊呼。

凌尘本能地挥剑格挡，剑身与黑影碰撞，发出刺耳的金属声。他被震得倒退数步，虎口发麻。

"好强的力量！"他心中暗惊。

黑影在空中盘旋一圈，再次袭来。这次凌尘看清了，那是一只通体漆黑的巨蟒，双眼泛着血红色的光芒。

"孽畜！休得猖狂！"师姐祭出法宝，一道金光射向巨蟒。

巨蟒灵活闪避，尾巴横扫，将师姐击飞出去。

"师姐！"凌尘大喝一声，体内灵力疯狂运转。青霜剑爆发出耀眼的蓝光，剑气纵横，将巨蟒逼退。

"好小子，有点本事。"巨蟒竟然口吐人言，"不过，你还太嫩了！"

话音刚落，巨蟒身形暴涨，化作百丈巨兽，张开血盆大口朝凌尘咬来。

生死关头，凌尘反而冷静下来。他闭上双眼，感受着体内灵力的流动。

"原来如此..."他喃喃自语，"这就是突破的契机！"

猛然间，他睁开双眼，眼中精光闪烁。青霜剑发出龙吟之声，剑气化作一条青龙，迎向巨蟒。

轰！

惊天动地的爆炸声响起，烟尘弥漫。待烟尘散去，巨蟒已经消失不见，只留下一颗泛着黑光的内丹。

凌尘单膝跪地，大口喘息着。刚才那一剑，几乎耗尽了他全部的灵力。

"你...你突破了？"师姐难以置信地看着他。

凌尘感受着体内全新的力量，嘴角露出一丝微笑："是的，我突破了。"

他站起身，望向禁地深处。那里，似乎还有什么在等待着他...

这场战斗，只是开始。
"""


class TestQualityChecker:
    """QualityChecker 测试类。"""

    def test_check_word_count_optimal(self, checker, sample_chapter):
        """测试最佳字数范围。"""
        report = checker.check_chapter(sample_chapter, 1)
        # 样本章节大约 800 字
        assert report.word_count > 0
        assert report.word_count_score > 0

    def test_check_word_count_too_short(self, checker):
        """测试字数过少。"""
        content = "太短了。"
        report = checker.check_chapter(content, 1)
        assert report.word_count < 100
        assert report.word_count_score == 40
        assert any("字数过少" in issue for issue in report.issues)

    def test_check_word_count_too_long(self, checker):
        """测试字数过多。"""
        content = "这是一段很长的文字。" * 600
        report = checker.check_chapter(content, 1)
        assert report.word_count > 5000
        assert report.word_count_score == 40
        assert any("字数过多" in issue for issue in report.issues)

    def test_check_structure_with_title(self, checker, sample_chapter):
        """测试有标题的结构。"""
        report = checker.check_chapter(sample_chapter, 1)
        assert report.structure_score > 60

    def test_check_structure_without_title(self, checker):
        """测试无标题的结构。"""
        content = "这是一段没有标题的文字。" * 50
        report = checker.check_chapter(content, 1)
        assert report.structure_score == 60

    def test_check_dialogue(self, checker, sample_chapter):
        """测试对话检查。"""
        report = checker.check_chapter(sample_chapter, 1)
        assert report.dialogue_score > 60

    def test_check_scene(self, checker, sample_chapter):
        """测试场景描写检查。"""
        report = checker.check_chapter(sample_chapter, 1)
        assert report.scene_score > 60

    def test_check_rhythm(self, checker, sample_chapter):
        """测试节奏检查。"""
        report = checker.check_chapter(sample_chapter, 1)
        assert report.rhythm_score > 60

    def test_check_hook(self, checker, sample_chapter):
        """测试钩子检查。"""
        report = checker.check_chapter(sample_chapter, 1)
        assert report.hook_score > 60

    def test_check_cool_points(self, checker, sample_chapter):
        """测试爽点检查。"""
        report = checker.check_chapter(sample_chapter, 1)
        assert report.cool_point_score >= 60

    def test_check_micro_payoffs(self, checker, sample_chapter):
        """测试微兑现检查。"""
        report = checker.check_chapter(sample_chapter, 1)
        assert report.micro_payoff_score >= 60

    def test_check_consistency(self, checker, sample_chapter):
        """测试一致性检查。"""
        report = checker.check_chapter(sample_chapter, 1)
        assert report.consistency_score >= 50

    def test_check_sentence_variety(self, checker, sample_chapter):
        """测试句式多样性检查。"""
        report = checker.check_chapter(sample_chapter, 1)
        assert report.sentence_variety_score >= 60

    def test_total_score(self, checker, sample_chapter):
        """测试总分计算。"""
        report = checker.check_chapter(sample_chapter, 1)
        assert 0 <= report.total_score <= 100

    def test_generate_report_text(self, checker, sample_chapter):
        """测试报告文本生成。"""
        report = checker.check_chapter(sample_chapter, 1)
        text = checker.generate_report_text(report)
        assert "第1章" in text
        assert "总分" in text
        assert "各维度得分" in text

    def test_empty_content(self, checker):
        """测试空内容。"""
        report = checker.check_chapter("", 1)
        assert report.total_score < 60
        assert report.word_count == 0

    def test_chinese_only(self, checker):
        """测试纯中文内容。"""
        content = "这是一段纯中文的内容，没有英文单词。" * 100
        report = checker.check_chapter(content, 1)
        assert report.word_count > 0

    def test_english_only(self, checker):
        """测试纯英文内容。"""
        content = "This is a test content with English words only. " * 100
        report = checker.check_chapter(content, 1)
        assert report.word_count > 0

    def test_mixed_content(self, checker):
        """测试中英混合内容。"""
        content = "这是中文，This is English，混合内容。" * 100
        report = checker.check_chapter(content, 1)
        assert report.word_count > 0


class TestQualityReport:
    """QualityReport 测试类。"""

    def test_default_values(self):
        """测试默认值。"""
        report = QualityReport()
        assert report.chapter == 0
        assert report.total_score == 0.0
        assert report.word_count == 0
        assert report.word_count_score == 0.0
        assert report.structure_score == 0.0
        assert report.dialogue_score == 0.0
        assert report.scene_score == 0.0
        assert report.rhythm_score == 0.0
        assert report.hook_score == 0.0
        assert report.cool_point_score == 0.0
        assert report.micro_payoff_score == 0.0
        assert report.consistency_score == 0.0
        assert report.sentence_variety_score == 0.0
        assert report.issues == []
        assert report.suggestions == []

    def test_chapter_number(self):
        """测试章节号。"""
        report = QualityReport(chapter=5)
        assert report.chapter == 5
