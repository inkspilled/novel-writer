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
    assert assets["顾言"]["source"] == "canon"
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
    assert "竖构图" in positive  # 人物 3:4 竖版定妆
    assert negative == ASSET_NEGATIVE


def test_asset_prompt_prefers_manual_prompt(store):
    store.sync_assets_from_canon()
    gu = next(a for a in store.load_assets() if a["name"] == "顾言")
    gu["prompt"] = "手写提示词：白衣少年，回眸"
    positive, _ = store.asset_prompt(gu)
    # 手填内容保留，但补一句画幅，防止文字和 width/height 打架
    assert positive.startswith("手写提示词：白衣少年，回眸")
    assert "画幅 3:4 竖构图" in positive


def test_asset_prompt_location_is_empty_scene_without_people(store):
    """回归：地点不能套人像后缀，否则生图会画出人。"""
    store.sync_assets_from_canon(kinds=("character", "location", "prop"))
    loc = next(a for a in store.load_assets() if a["kind"] == "location")

    positive, negative = store.asset_prompt(loc)
    assert "空镜" in positive and "无人物" in positive
    assert "正面参考" not in positive  # 旧 bug：人像词拼给了地点
    assert "人物" in negative  # 负面词把人挡掉
    assert "藏经阁" in positive and "藏书重地" in positive  # 正典描述保留


def test_asset_prompt_prop_is_closeup_without_people(store):
    store.sync_assets_from_canon(kinds=("character", "location", "prop"))
    prop = next(a for a in store.load_assets() if a["kind"] == "prop")

    positive, negative = store.asset_prompt(prop)
    assert "特写" in positive and "无人物" in positive
    assert "正面参考" not in positive
    assert "人物" in negative
    assert "玄铁令" in positive and "祖传令牌" in positive


def test_asset_prompt_storyboard_asset_gets_kind_fallback_desc(store):
    """无正典描述的原创资产也要有类型化兜底，不能裸名字配人像词。"""
    store.save_storyboard(1, {"shots": [{
        "scene_id": "SC01", "shot_size": "全景", "camera": "固定",
        "duration_sec": 2, "location": "临时码头", "time": "夜",
        "characters": "顾言", "action": "下船", "dialogue": "",
        "sfx": "", "transition": "硬切", "status": "draft", "notes": "",
    }]})
    store.extract_assets_from_storyboard(1)
    loc = next(a for a in store.load_assets() if a["name"] == "临时码头")
    assert loc["kind"] == "location"

    positive, negative = store.asset_prompt(loc)
    assert "全景空镜" in positive  # fallback_desc 生效
    assert "空镜" in positive
    assert "人物" in negative


def test_asset_prompt_plans_reference_prefix_by_kind():
    """垫图重生成前缀按类型分化：人物/场景/道具各说各的基准。"""
    from src.novel_writer.core.drama import ASSET_PROMPT_PLANS

    assert "同一人物" in ASSET_PROMPT_PLANS["character"]["reference_prefix"]
    assert "同一场景" in ASSET_PROMPT_PLANS["location"]["reference_prefix"]
    assert "同一物品" in ASSET_PROMPT_PLANS["prop"]["reference_prefix"]


def test_asset_prompt_plans_aspect_ratio_by_kind():
    """画幅按类型：人物 3:4 竖版定妆、场景 16:9 横版空镜、道具 1:1 方图。"""
    from src.novel_writer.core.drama import ASSET_PROMPT_PLANS

    c, l, p = (ASSET_PROMPT_PLANS[k] for k in ("character", "location", "prop"))
    assert (c["width"], c["height"]) == (960, 1280)
    assert (l["width"], l["height"]) == (1280, 720)
    assert (p["width"], p["height"]) == (1024, 1024)
    # 画幅标签同时供提示词和导出使用
    assert (c["ratio"], l["ratio"], p["ratio"]) == ("3:4", "16:9", "1:1")
    # 宽高都是 16 的倍数（latent 对齐）
    for plan in (c, l, p):
        assert plan["width"] % 16 == 0 and plan["height"] % 16 == 0


def test_asset_prompt_uses_positive_phrasing_and_fixed_counts(store):
    """文章经验：正面式表述（画面中只有…）+ 数量写死（单人/单件）。"""
    store.sync_assets_from_canon(kinds=("character", "location", "prop"))
    assets = {a["kind"]: a for a in store.load_assets()}

    loc_pos, _ = store.asset_prompt(assets["location"])
    assert "只有建筑结构" in loc_pos  # 正面式，不是光写"不要有人"
    prop_pos, _ = store.asset_prompt(assets["prop"])
    assert "单件" in prop_pos
    char_pos, _ = store.asset_prompt(assets["character"])
    assert "单人" in char_pos


def test_audit_asset_prompt_flags_known_risks(store):
    """提交前体检：类型锚词缺失 / 人像词泄漏 / 无描述兜底 / 多实体名。"""
    store.sync_assets_from_canon(kinds=("character", "location", "prop"))

    # 1. 手填提示词缺类型锚词
    loc = next(a for a in store.load_assets() if a["kind"] == "location")
    loc["prompt"] = "一个可爱的女孩子站在门口"
    issues = store.audit_asset_prompt(loc)
    assert any("锚词" in i for i in issues)
    assert any("人像词" in i for i in issues)  # 人像词同时被点名

    # 2. 有正典描述、无手填 → 无风险
    loc2 = next(a for a in store.load_assets() if a["kind"] == "location")
    loc2["prompt"] = ""
    assert store.audit_asset_prompt(loc2) == []

    # 3. 无描述的原创资产 → 兜底警告
    store.save_storyboard(5, {"shots": [{
        "scene_id": "SC01", "shot_size": "全景", "camera": "固定",
        "duration_sec": 2, "location": "临时码头", "time": "夜",
        "characters": "顾言", "action": "下船", "dialogue": "",
        "sfx": "", "transition": "硬切", "status": "draft", "notes": "",
    }]})
    store.extract_assets_from_storyboard(5)
    dock = next(a for a in store.load_assets() if a["name"] == "临时码头")
    issues = store.audit_asset_prompt(dock)
    assert any("兜底" in i for i in issues)

    # 4. 名字里挤了多个实体
    dock["name"] = "刀、剑和盾"
    assert any("拆分" in i for i in store.audit_asset_prompt(dock))


def test_check_assets_reports_prompt_risk(store):
    store.sync_assets_from_canon(kinds=("character", "location", "prop"))
    assets = store.load_assets()
    loc = next(a for a in assets if a["kind"] == "location")
    loc["prompt"] = "一个可爱的女孩子站在门口"  # 人像词泄漏
    store.save_assets(assets)  # check_assets 重新从盘读，先落盘
    report = store.check_assets()
    names = [r["name"] for r in report["prompt_risk"]]
    assert "藏经阁" in names


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


def test_regeneration_keeps_original_reference_image(store):
    store.sync_assets_from_canon()
    asset = store.load_assets()[0]
    store.save_generated_image(asset["id"], b"first-portrait")
    first = store.load_assets()[0]
    anchor = store.assets_dir / first["reference_image"]
    store.save_generated_image(asset["id"], b"second-scene")
    updated = store.load_assets()[0]
    assert anchor.read_bytes() == b"first-portrait"
    assert updated["reference_image"] == first["reference_image"]
    assert (store.assets_dir / updated["image"]).read_bytes() == b"second-scene"


def test_extract_assets_from_storyboard_keeps_entity_field(store):
    store.save_storyboard(1, {"shots": [{
        "scene_id": "SC01", "shot_size": "近景", "camera": "固定",
        "duration_sec": 2, "location": "藏经阁", "time": "夜",
        "characters": "顾言、苏月", "action": "对望", "dialogue": "",
        "sfx": "", "transition": "硬切", "status": "draft", "notes": "",
    }]})
    assert store.extract_assets_from_storyboard(1) == 3
    assets = {a["name"]: a for a in store.load_assets()}
    assert assets["顾言"]["entity"] == "c1"  # 分镜名字对回了角色卡
    assert assets["顾言"]["source"] == "canon"
    assert assets["藏经阁"]["kind"] == "location"
    assert assets["藏经阁"]["source"] == "canon"


def test_storyboard_extras_marked_as_original(store):
    """分镜里的小说没有的角色/地点（龙套、临时场景）标成短剧原创。"""
    store.save_storyboard(2, {"shots": [{
        "scene_id": "SC01", "shot_size": "全景", "camera": "固定",
        "duration_sec": 2, "location": "临时茶棚", "time": "日",
        "characters": "顾言、路人甲", "action": "喝茶", "dialogue": "",
        "sfx": "", "transition": "硬切", "status": "draft", "notes": "",
    }]})
    store.extract_assets_from_storyboard(2)

    assets = {a["name"]: a for a in store.load_assets()}
    assert assets["顾言"]["source"] == "canon"
    assert assets["路人甲"]["source"] == "storyboard"
    assert assets["路人甲"]["entity"] == ""
    assert assets["临时茶棚"]["source"] == "storyboard"


def test_check_assets_counts_sources_and_gaps(store):
    store.sync_assets_from_canon()
    gu = next(a for a in store.load_assets() if a["name"] == "顾言")
    store.save_generated_image(gu["id"], b"img")

    report = store.check_assets()
    assert report["total"] == 3
    assert report["canon"] == 3 and report["original"] == 0
    assert report["missing_prompt"] == 3  # 都没写提示词
    assert report["missing_image"] == 2   # 只有顾言生成了图
    assert report["duplicates"] == []
    assert report["orphan"] == []


def test_alias_matches_canon(store):
    """角色别名/带括号注解的名字也能对回正典。"""
    store.save_storyboard(3, {"shots": [{
        "scene_id": "SC01", "shot_size": "近景", "camera": "固定",
        "duration_sec": 2, "location": "藏经阁", "time": "夜",
        "characters": "顾言（主角）", "action": "推门", "dialogue": "",
        "sfx": "", "transition": "硬切", "status": "draft", "notes": "",
    }]})
    store.extract_assets_from_storyboard(3)
    gu = next(a for a in store.load_assets() if a["name"] == "顾言（主角）")
    assert gu["source"] == "canon"
    assert gu["entity"] == "c1"
