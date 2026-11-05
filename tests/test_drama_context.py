"""短剧上下文切片：预算、场景优先、圣经小节、剧本压缩、前情提要。"""
from __future__ import annotations

from src.novel_writer.core.drama_context import (
    compress_script, filter_character_sections, parse_character_names,
    plan_digest, recap_lines, slice_bible, slice_chapter, truncate,
)

FILLER = (
    "窗外的风把树叶吹得沙沙响，远处的钟楼敲了三下，街上没有人。"
    "他把桌上的纸一张张理好，又把茶盏挪到一边，慢慢坐下。"
)


def _long_chapter(scenes: int = 8, hot: int = 4) -> str:
    blocks = []
    for i in range(scenes):
        if i == hot:
            body = (
                "他拔刀的那一下没有半点犹豫，血溅在门框上。"
                "“你敢动她试试。”对方却笑了，突然抬手，枪口顶住他的眉心。"
                "就在这时，门外传来警笛声，所有人都愣住了。"
            )
        else:
            body = FILLER * 3
        blocks.append(body)
    return "\n\n***\n\n".join(blocks)


class TestSliceChapter:
    def test_short_text_is_lossless(self):
        text = "林乐天捡到骨牌，苏月拦住他。"
        assert slice_chapter(text, budget=500) == text
        assert plan_digest(text, budget=200) == text

    def test_long_text_respects_budget_and_marks_omissions(self):
        text = _long_chapter()
        out = slice_chapter(text, budget=700)
        assert len(out) <= 900  # 700 预算 + 说明行
        assert "（略）" in out
        assert "按冲突密度选取" in out
        assert out.startswith("（本章原文")  # 先告诉模型这是删减稿

    def test_conflict_scene_survives_slicing(self):
        out = slice_chapter(_long_chapter(scenes=10, hot=6), budget=800)
        assert "枪口顶住他的眉心" in out  # 冲突戏必须留下，哪怕在中间

    def test_single_giant_block_falls_back_to_head_tail(self):
        text = "开端" + "铺陈" * 4000 + "结尾钩子"
        out = slice_chapter(text, budget=400)
        assert len(out) <= 500
        assert "开端" in out  # 首块超预算时给前缀
        assert "结尾钩子" in out  # 结尾钩子永远保留


class TestBibleSlice:
    def test_sections_prioritised_by_key(self):
        bible = (
            "# 改编圣经\n" + "总述" * 200 + "\n\n"
            "## 角色视觉锚点\n" + "外形" * 200 + "\n\n"
            "## 每集节奏规则\n前3秒冲突，15秒升级，结尾5秒钩子。\n" + "节奏" * 50 + "\n\n"
            "## 世界观补充\n" + "设定" * 300
        )
        out = slice_bible(bible, ("节奏", "视觉"), budget=600)
        assert len(out) <= 800
        assert "节奏规则" in out  # 命中关键词的小节优先

    def test_short_bible_untouched(self):
        assert slice_bible("# 圣经\n很短。", ("节奏",), budget=100) == "# 圣经\n很短。"


class TestCharacterSlice:
    def test_only_matching_characters_kept(self):
        profile = (
            "# 人物设定\n\n## 顾言\n- 主角，冷静。\n\n"
            "## 赵九衡\n- 反派，执事。\n\n"
            "## 苏月\n- 女主，记者。\n"
        )
        names = parse_character_names(profile)
        assert "顾言" in names and "赵九衡" in names

        out = filter_character_sections(profile, ["顾言"], budget=400)
        assert "顾言" in out
        assert "赵九衡" not in out

    def test_no_match_falls_back_to_slice(self):
        profile = "# 人物设定\n\n## 顾言\n" + "主角" * 400
        out = filter_character_sections(profile, ["不存在"], budget=300)
        assert len(out) <= 400


class TestScriptCompress:
    def test_keeps_scene_headings_and_dialogue(self):
        script = "# 第1集 试\n\n" + "\n\n".join(
            f"## 第{i}场 山门 · 夜\n" + "动作描写" * 200 + f"\n林乐天：第{i}场台词。"
            for i in range(1, 6)
        )
        out = compress_script(script, budget=800)
        assert len(out) <= 1000
        for i in range(1, 6):
            assert f"## 第{i}场" in out
        assert "第3场台词" in out
        assert "剧本已压缩" in out

    def test_short_script_untouched(self):
        script = "# 第1集\n\n## 第1场 门口 · 夜\n\n林乐天：走。"
        assert compress_script(script, budget=500) == script


class TestRecap:
    def test_last_three_episodes_in_order(self):
        episodes = [
            {"number": n, "title": f"集{n}", "goal": f"目标{n}", "hook": f"钩子{n}"}
            for n in range(1, 7)
        ]
        out = recap_lines(episodes, before_number=6, limit=3)
        assert "第3集" in out and "第4集" in out and "第5集" in out
        assert "第6集" not in out
        assert out.index("第3集") < out.index("第5集")
        assert "钩子5" in out

    def test_truncate(self):
        assert truncate("短", 10) == "短"
        assert len(truncate("长" * 100, 20)) <= 21
