"""统一生成账本 + 三层资产视图 + shot→asset 引用表 + 批次结果回流。

覆盖：
- generation_records：记一条、按 target 查、markdown 汇总；
- failures.json：同目标覆盖、按目标清除、汇总文案；
- versions_index：旧图移入 versions/ 时登记；
- 道具三视图 / 人物全身三视图：提示词、落盘、体检计数；
- shot→asset 结构化引用：构建、解析（老项目回退名字匹配）；
- import_comfy_results：按文件名前缀回流（含计数后缀、坏画幅、未识别）。
"""
from __future__ import annotations

import io
import json

import pytest

from src.novel_writer.core.drama import (
    FULLBODY_VIEWS, MAKEUP_VIEWS, PROP_VIEWS, DramaStore,
)
from src.novel_writer.core.drama_records import (
    clear_failures, failures_summary, latest_record, load_failures,
    read_generation_records, read_versions, record_failure, record_generation,
    record_markdown,
)


def png(size=(96, 128), color=(200, 30, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def store(tmp_path):
    st = DramaStore(tmp_path)
    st.ensure({"title": "测试"})
    (tmp_path / "meta.json").write_text(
        json.dumps({"title": "测试", "genre": "仙侠", "style": "快节奏"}, ensure_ascii=False),
        encoding="utf-8",
    )
    return st


def _asset(kind: str, asset_id: str, name: str, **extra) -> dict:
    return {
        "kind": kind, "id": asset_id, "name": name, "source": "manual",
        "prompt": "", "ref": "", "status": "draft", "notes": "", "entity": "", "image": "",
        **extra,
    }


class FakeImageClient:
    """假图片客户端：带 cfg 供账本提取 provider/model。"""

    def __init__(self, size=(96, 128)):
        self.cfg = {"provider": "image_api", "model": "mock-model"}
        self.size = size
        self.calls: list[dict] = []

    def generate(self, positive, negative="", **kwargs):
        self.calls.append({"positive": positive, **kwargs})
        return png(self.size)

    def close(self):
        pass


# ── 生成账本 ──


def test_record_generation_roundtrip(store, tmp_path):
    workflow = tmp_path / "workflow_api.json"
    workflow.write_text(json.dumps({"1": {}}), encoding="utf-8")
    data = png()
    rel = store.save_generated_image("char_x", data, expected_size=(960, 1280))
    record_generation(store, target="asset:char_x:plain", client=FakeImageClient(),
                      seed=42, positive="一只猫", negative="模糊", width=960, height=1280,
                      output=store.assets_dir / rel)

    records = read_generation_records(store, target="asset:char_x:plain")
    assert len(records) == 1
    record = records[0]
    assert record["seed"] == 42
    assert record["provider"] == "image_api"
    assert record["model"] == "mock-model"
    assert record["prompt_digest"] and record["output_digest"]
    assert latest_record(store, "asset:char_x:plain")["seed"] == 42
    assert latest_record(store, "asset:other") is None
    assert "一只猫" not in record_markdown(store)  # 表格里只有元数据
    assert "| 42 |" in record_markdown(store)


def test_generate_makeup_view_writes_record(store):
    store.save_assets([_asset("character", "char_1", "顾言")])
    asset = store.load_assets()[0]
    client = FakeImageClient()
    from src.novel_writer.core.image_generation import generate_makeup_view

    path = generate_makeup_view(store, client, asset, "front")
    assert path.is_file()
    record = latest_record(store, "asset:char_1:makeup_front")
    assert record and record["seed"] is not None
    assert record["provider"] == "image_api"
    assert record["reference"] == ""  # 没有参考图
    views = store.load_assets()[0]["makeup_views"]
    assert set(views) == {"front"}


# ── 失败清单 ──


def test_failures_replace_same_target_and_clear(store):
    record_failure(store, stage="asset_images", target="char_1",
                   label="顾言", message="连不上")
    record_failure(store, stage="asset_images", target="char_1",
                   label="顾言", message="超时")
    record_failure(store, stage="frame_images", target="E01-S001",
                   label="E01-S001 首尾帧", message="画幅不对")
    items = load_failures(store)
    assert len(items) == 2
    assert items[0]["message"] == "超时"

    assert "顾言" in failures_summary(store)
    assert failures_summary(store, stage="frame_images").startswith("失败 1 项")

    clear_failures(store, targets=["char_1"])
    assert [item["target"] for item in load_failures(store)] == ["E01-S001"]
    clear_failures(store, stage="frame_images")
    assert load_failures(store) == []
    assert failures_summary(store) == ""


# ── versions 索引 ──


def test_regeneration_indexes_old_version(store):
    store.save_assets([_asset("character", "char_1", "顾言")])
    store.save_generated_image("char_1", png(color=(10, 10, 10)), expected_size=(960, 1280))
    store.save_generated_image("char_1", png(color=(20, 20, 20)), expected_size=(960, 1280))
    versions = list((store.assets_dir / "generated" / "versions").glob("char_1_*.png"))
    assert len(versions) == 1
    entries = read_versions(store, owner="char_1")
    assert len(entries) == 1
    # file=被替换的主路径，replaced_by=归档到 versions/ 里的文件名
    assert entries[0]["file"] == "assets/generated/char_1.png"
    assert entries[0]["replaced_by"] == versions[0].name


# ── 道具三视图 ──


def test_prop_views_roundtrip_and_health(store):
    store.save_assets([_asset("prop", "prop_1", "玄铁令")])
    asset = store.load_assets()[0]

    positive, negative = store.prop_prompt(asset, "side")
    assert "以正面照为同一物品基准" in positive
    assert "多人" in negative

    store.save_prop_view("prop_1", "front", png((128, 128)))
    saved = next(a for a in store.load_assets() if a["id"] == "prop_1")
    assert saved["prop_views"]["front"]
    assert saved["image"].endswith(".png")  # 正面照同时是参考图

    store.save_prop_view("prop_1", "side", png((128, 128), (0, 255, 0)))
    store.save_prop_view("prop_1", "back", png((128, 128), (0, 0, 255)))
    saved = next(a for a in store.load_assets() if a["id"] == "prop_1")
    assert saved["status"] == "ready"
    assert set(store.prop_paths(saved)) == set(PROP_VIEWS)

    report = store.check_assets()
    assert report["prop_incomplete"] == 0
    store.clear_prop_view("prop_1", "side")
    assert len(store.prop_paths(store.load_assets()[0])) == 2
    assert store.check_assets()["prop_incomplete"] == 1


def test_save_prop_view_rejects_wrong_aspect(store):
    store.save_assets([_asset("prop", "prop_1", "玄铁令")])
    with pytest.raises(ValueError, match="1:1"):
        store.save_prop_view("prop_1", "front", png((128, 64)))


# ── 人物全身三视图 ──


def test_fullbody_views_roundtrip(store):
    store.save_assets([_asset("character", "char_1", "顾言")])
    asset = store.load_assets()[0]

    positive, negative = store.fullbody_prompt(asset, "back")
    assert "正后方全身站姿" in positive
    assert "头肩特写" in negative

    store.save_fullbody_view("char_1", "front", png())
    store.save_fullbody_view("char_1", "side", png((96, 128), (0, 255, 0)))
    saved = store.load_assets()[0]
    assert set(store.fullbody_paths(saved)) == {"front", "side"}
    # 全身视图不改资产状态；状态仍由定妆照管理
    assert saved["status"] == "draft"

    report = store.check_assets()
    assert report["fullbody_incomplete"] == 1
    store.clear_fullbody_view("char_1", "side")
    assert store.check_assets()["fullbody_incomplete"] == 1  # 还缺 side/back


def test_save_fullbody_view_rejects_wrong_aspect(store):
    store.save_assets([_asset("character", "char_1", "顾言")])
    with pytest.raises(ValueError, match="3:4"):
        store.save_fullbody_view("char_1", "front", png((128, 128)))


# ── shot→asset 结构化引用 ──


def test_shot_refs_build_and_resolve(store):
    store.save_assets([
        _asset("character", "char_gu", "顾言"),
        _asset("location", "loc_ge", "藏经阁"),
        _asset("prop", "prop_ling", "玄铁令"),
    ])
    store.save_storyboard(1, {"shots": [
        {"shot_id": "E01-S001", "characters": "顾言", "location": "藏经阁",
         "action": "顾言握紧玄铁令", "dialogue": ""},
        {"shot_id": "E01-S002", "characters": "龙套甲", "location": "茶棚",
         "action": "喝水", "dialogue": ""},
    ]}, update_source=True)
    store.extract_assets_from_storyboard(1)  # 顺带落引用表

    refs = store.load_shot_refs(1)
    assert refs["E01-S001"]["characters"] == ["char_gu"]
    assert refs["E01-S001"]["location"] == "loc_ge"
    assert refs["E01-S001"]["props"] == ["prop_ling"]  # 动作文本里点名的道具
    # 龙套场景由提取时新建为短剧原创资产，引用表照样登记
    assert refs["E01-S002"]["location"] == "loc_茶棚"

    shot = store.load_storyboard(1)["shots"][0]
    resolved = store.resolve_shot_assets(1, shot)
    assert resolved["characters"][0]["name"] == "顾言"
    assert resolved["location"]["name"] == "藏经阁"
    assert resolved["props"][0]["name"] == "玄铁令"
    # 没有引用表的镜（老项目）退回名字匹配
    store.save_shot_refs(1, {})
    resolved = store.resolve_shot_assets(1, shot)
    assert resolved["characters"][0]["name"] == "顾言"


# ── 批次结果回流 ──


def test_import_comfy_results_by_prefix(store):
    store.save_assets([
        _asset("character", "char_1", "顾言"),
        _asset("prop", "prop_1", "玄铁令"),
    ])
    store.save_storyboard(1, {"shots": [{
        "shot_id": "E01-S001", "shot_size": "中景", "camera": "平视，固定",
        "duration_sec": 3, "location": "", "time": "", "characters": "",
        "action": "", "dialogue": "", "sfx": "", "transition": "硬切",
        "status": "draft", "notes": "",
    }]}, update_source=True)

    folder = store.root / "imports_tmp"
    folder.mkdir()
    (folder / "char_1.png").write_bytes(png())  # 无角度 → 通用图
    (folder / "char_1_front.png").write_bytes(png())  # 定妆正面
    (folder / "char_1_fb_side_00001_.png").write_bytes(png((96, 128), (0, 255, 0)))  # ComfyUI 计数后缀
    (folder / "prop_1_back.png").write_bytes(png((128, 128)))
    (folder / "E01-S001_first.png").write_bytes(png((64, 128)))
    (folder / "mystery.png").write_bytes(png((32, 32)))
    (folder / "char_1_left.png").write_bytes(png((64, 64)))  # 画幅不对 → 失败清单

    result = store.import_comfy_results(folder)
    assert result["imported"] == 5
    assert len(result["failed"]) == 1 and "char_1_left" in result["failed"][0]
    assert result["unmatched"] == ["mystery.png"]

    saved = {a["id"]: a for a in store.load_assets()}
    assert saved["char_1"]["image"]  # 通用图回填
    assert "front" in saved["char_1"]["makeup_views"]
    assert "side" in saved["char_1"]["fullbody_views"]
    assert "back" in saved["prop_1"]["prop_views"]
    assert store.keyframe_image_path(1, "E01-S001", "first").is_file()


def test_import_comfy_results_requires_dir(store, tmp_path):
    with pytest.raises(ValueError, match="目录不存在"):
        store.import_comfy_results(tmp_path / "nope")
