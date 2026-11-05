"""短剧资产：与小说正典（人物/地点/物品）对应 + 提示词 + 生成图落盘。"""
from __future__ import annotations

import json

import pytest

from src.novel_writer.core.drama import ASSET_NEGATIVE, DramaStore


@pytest.fixture()
def store(tmp_path):
    st = DramaStore(tmp_path)
    st.ensure({"title": "测试", "target_chapters": 10})
    (tmp_path / "canon").mkdir(exist_ok=True)
    (tmp_path / "canon" / "characters.json").write_text(json.dumps({
        "version": 1,
        "characters": [
            {"id": "c1", "name": "顾言", "public_profile": {"身份": "主角", "外形": "白衣"}},
            {"id": "c2", "name": "苏月", "public_profile": {"身份": "女主"}},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "world_state.json").write_text(json.dumps({
        "version": 1, "world": {}, "characters": {}, "timeline": [], "location_log": [],
        "locations": {"藏经阁": {"desc": "藏书重地，闲人免进"}},
        "items_catalog": {"玄铁令": {"desc": "祖传令牌，见令如见家主"}},
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({
        "title": "测试", "genre": "仙侠", "style": "快节奏",
    }, ensure_ascii=False), encoding="utf-8")
    return st


def test_sync_assets_from_canon_links_characters_and_locations(store):
    assert store.sync_assets_from_canon() == 3  # 2 人物 + 1 地点

    assets = {a["name"]: a for a in store.load_assets()}
    assert assets["顾言"]["kind"] == "character"
    assert assets["顾言"]["entity"] == "c1"  # 指向角色卡 id
    assert assets["藏经阁"]["kind"] == "location"
    assert assets["藏经阁"]["status"] == "draft"

    # 幂等：再导一次不重复
    assert store.sync_assets_from_canon() == 0
    assert len(store.load_assets()) == 3


def test_sync_assets_can_include_props(store):
    added = store.sync_assets_from_canon(kinds=("character", "location", "prop"))
    assert added == 4
    names = {a["name"] for a in store.load_assets()}
    assert "玄铁令" in names


def test_asset_prompt_uses_canon_summary_and_style(store):
    store.sync_assets_from_canon()
    gu = next(a for a in store.load_assets() if a["name"] == "顾言")

    positive, negative = store.asset_prompt(gu)
    assert "顾言" in positive and "人物" in positive
    assert "主角" in positive  # 角色卡里的身份进了提示词
    assert "仙侠" in positive and "快节奏" in positive
    assert "竖屏" in positive
    assert negative == ASSET_NEGATIVE


def test_asset_prompt_prefers_manual_prompt(store):
    store.sync_assets_from_canon()
    gu = next(a for a in store.load_assets() if a["name"] == "顾言")
    gu["prompt"] = "手写提示词：白衣少年，回眸"
    positive, _ = store.asset_prompt(gu)
    assert positive == "手写提示词：白衣少年，回眸"


def test_save_generated_image_updates_asset(store):
    store.sync_assets_from_canon()
    gu = next(a for a in store.load_assets() if a["name"] == "顾言")

    rel = store.save_generated_image(gu["id"], b"\x89PNG-bytes")
    assert rel.startswith("generated/")
    saved = store.assets_dir / "generated" / f"{gu['id']}.png"
    assert saved.read_bytes() == b"\x89PNG-bytes"

    refreshed = next(a for a in store.load_assets() if a["id"] == gu["id"])
    assert refreshed["status"] == "ready"
    assert refreshed["image"] == rel
    assert refreshed["ref"] == rel


def test_extract_assets_from_storyboard_keeps_entity_field(store):
    store.save_storyboard(1, {"shots": [{
        "scene_id": "SC01", "shot_size": "近景", "camera": "固定",
        "duration_sec": 2, "location": "藏经阁", "time": "夜",
        "characters": "顾言、苏月", "action": "对望", "dialogue": "",
        "sfx": "", "transition": "硬切", "status": "draft", "notes": "",
    }]})
    assert store.extract_assets_from_storyboard(1) == 3
    assets = {a["name"]: a for a in store.load_assets()}
    assert assets["顾言"]["entity"] == "顾言"
    assert assets["藏经阁"]["kind"] == "location"
