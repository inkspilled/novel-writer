"""角色档案 ↔ 世界状态同步：位置/目标不能各说各话。"""
from __future__ import annotations

from src.novel_writer.core.characters.registry import CharacterRegistry
from src.novel_writer.core.world_state import WorldState


def test_sync_location_and_goals_from_world_state(tmp_path):
    reg = CharacterRegistry(tmp_path)
    rec = reg.create("沈玄微", chapter=1, tier="core")
    rec.location = ""
    reg.save()

    gs = WorldState(tmp_path)
    gs.load()
    gs.add_character("沈玄微", {"location": "药圃", "goals": ["查清灵脉", "保护苏婉"]})
    gs.save()

    changed = reg.sync_from_world_state()
    got = reg.get("沈玄微")
    assert changed >= 2
    assert got.location == "药圃"
    assert got.goals == ["查清灵脉", "保护苏婉"]

    # 落盘：重新加载仍在
    again = CharacterRegistry(tmp_path).get("沈玄微")
    assert again.location == "药圃"
    assert again.goals == ["查清灵脉", "保护苏婉"]


def test_push_manual_edit_back_to_world_state(tmp_path):
    reg = CharacterRegistry(tmp_path)
    rec = reg.create("沈玄微", chapter=1, tier="core")
    reg.save()

    gs = WorldState(tmp_path)
    gs.load()
    gs.add_character("沈玄微", {"location": "杂院"})
    gs.save()

    rec.location = "禁地"
    rec.goals = ["拿到钥匙"]
    reg.save()
    reg.push_to_world_state()

    gs2 = WorldState(tmp_path)
    gs2.load()
    assert gs2.characters["沈玄微"]["location"] == "禁地"
    assert gs2.characters["沈玄微"]["goals"] == ["拿到钥匙"]


def test_alias_matches_world_state_key(tmp_path):
    reg = CharacterRegistry(tmp_path)
    reg.create("沈玄微", chapter=1, tier="core", aliases=["玄微先生"])
    reg.save()

    gs = WorldState(tmp_path)
    gs.load()
    gs.add_character("玄微先生", {"location": "山门"})
    gs.save()

    reg.sync_from_world_state()
    assert reg.get("沈玄微").location == "山门"


def test_context_text_includes_goals(tmp_path):
    gs = WorldState(tmp_path)
    gs.load()
    gs.add_character("沈玄微", {"location": "药圃", "goals": ["查清灵脉"]})
    text = gs.build_context_text()
    assert "目标" in text and "查清灵脉" in text


def test_ensure_missing_characters_from_world_state(tmp_path):
    """世界状态里的新角色要补 extra 卡，否则 POV/感知抽取名单里没有他。"""
    reg = CharacterRegistry(tmp_path)
    reg.create("沈玄微", chapter=1, tier="core")
    gs = WorldState(tmp_path)
    gs.load()
    gs.add_character("赵无极", {"location": "书房", "goals": ["查清旧骨来源"]})
    gs.add_character("沈玄微", {"location": "药圃"})

    added = reg.ensure_from_world_state(gs, chapter=3)
    assert added == 1
    got = CharacterRegistry(tmp_path).get("赵无极")
    assert got is not None and got.location == "书房"
    assert got.goals == ["查清旧骨来源"]
    assert got.auto_sim is False
    # 幂等：第二次不再重复建卡
    assert reg.ensure_from_world_state(gs, chapter=3) == 0


def test_context_text_includes_item_position(tmp_path):
    """世界状态更新 prompt 依赖 build_context_text 知道物品在哪/谁持有。"""
    gs = WorldState(tmp_path)
    gs.load()
    gs.add_item("骨牌", {"type": "法宝", "effect": "吞噬灵力"})
    gs.set_item_position("骨牌", holder="林乐天")
    text = gs.build_context_text()
    assert "骨牌" in text and "林乐天" in text


def test_world_state_prompt_requests_goals():
    from src.novel_writer.core.workflow.prompts import build_world_state_prompt
    text = build_world_state_prompt(3, "", "正文")
    assert '"goals"' in text and "goals" in text
