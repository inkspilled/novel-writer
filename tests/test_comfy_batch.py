"""交给 ComfyUI 的批次包：素材、提示词、填好的工作流、清单。"""
from __future__ import annotations

import json

import pytest

from src.novel_writer.core.drama import DramaStore, asset_seed


def _asset(kind: str, asset_id: str, name: str, **extra) -> dict:
    return {
        "kind": kind, "id": asset_id, "name": name, "source": "manual",
        "prompt": "", "ref": "", "status": "draft", "notes": "", "entity": "", "image": "",
        **extra,
    }


@pytest.fixture()
def store(tmp_path):
    st = DramaStore(tmp_path)
    st.ensure({"title": "测试"})
    st.save_assets([
        _asset("character", "char_1", "顾言"),
        _asset("location", "loc_1", "藏经阁"),
        _asset("prop", "prop_1", "玄铁令"),
    ])
    return st


def _template(with_reference: bool = False) -> dict:
    data = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{positive}}"}},
        "2": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}"}},
        "3": {"class_type": "EmptyLatentImage",
              "inputs": {"width": "{{width}}", "height": "{{height}}"}},
        "4": {"class_type": "SaveImage", "inputs": {"filename_prefix": "{{prefix}}"}},
    }
    if with_reference:
        data["5"] = {"class_type": "LoadImage", "inputs": {"image": "{{reference_image}}"}}
    return data


def test_export_batch_manifest_has_prompts_and_type_canvases(store):
    out = store.export_comfy_batch(base_url="http://127.0.0.1:8188")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    jobs = {job["kind"]: job for job in reversed(manifest["jobs"])}

    # 人物 = 4 头肩定妆 + 3 全身三视图；场景 = 1 环景；道具 = 3 三视图
    assert len(manifest["jobs"]) == 11
    char_jobs = [job for job in manifest["jobs"] if job["kind"] == "character"]
    assert [job["makeup_view"] for job in char_jobs if "makeup_view" in job] == [
        "front", "left", "right", "back",
    ]
    assert [job["fullbody_view"] for job in char_jobs if "fullbody_view" in job] == [
        "front", "side", "back",
    ]
    prop_jobs = [job for job in manifest["jobs"] if job["kind"] == "prop"]
    assert [job["prop_view"] for job in prop_jobs] == ["front", "side", "back"]
    assert (jobs["character"]["width"], jobs["character"]["height"]) == (960, 1280)
    assert (jobs["location"]["width"], jobs["location"]["height"]) == (2048, 1024)
    assert (jobs["prop"]["width"], jobs["prop"]["height"]) == (1024, 1024)
    assert "画幅 3:4 竖构图" in jobs["character"]["positive"]
    assert jobs["location"]["scene_view"] == "panorama"
    assert "等距柱状投影" in jobs["location"]["positive"]
    assert jobs["location"]["negative"].startswith("人物")
    assert jobs["character"]["seed"] == asset_seed(store.assets_dir, "char_1_front")
    assert prop_jobs[1]["depends_on"] == "prop_1_front"  # 侧面/背面依赖正面
    assert "全身" in next(job for job in char_jobs if job.get("fullbody_view") == "front")["positive"]

    prompts = (out / "prompts.md").read_text(encoding="utf-8")
    assert "顾言" in prompts and "藏经阁" in prompts and "玄铁令" in prompts
    assert "负向提示词" in prompts
    assert (out / "README.md").exists()
    assert manifest["workflow_template"] == ""  # 没配工作流时只导提示词/清单


def test_export_batch_fills_workflow_with_ints(store, tmp_path):
    wf = tmp_path / "workflow_api.json"
    wf.write_text(json.dumps(_template(), ensure_ascii=False), encoding="utf-8")

    out = store.export_comfy_batch(workflow=str(wf))
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["workflow_size_placeholders"] is True
    job = next(item for item in manifest["jobs"] if item["kind"] == "character")
    filled = json.loads((out / job["workflow"]).read_text(encoding="utf-8"))

    assert filled["1"]["inputs"]["text"] == job["positive"]
    assert filled["3"]["inputs"]["width"] == 960  # 数字占位符要变 int，ComfyUI 才认
    assert filled["3"]["inputs"]["height"] == 1280
    assert filled["2"]["inputs"]["seed"] == job["seed"]
    assert filled["4"]["inputs"]["filename_prefix"] == "char_1_front"
    assert (out / "workflows" / "_template_api.json").exists()


def test_export_batch_copies_reference_images_and_uses_ref_workflow(store, tmp_path):
    from PIL import Image

    generated = store.assets_dir / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    portrait = generated / "char_1.png"
    Image.new("RGB", (512, 896), "blue").save(portrait)
    store.save_assets([
        _asset("character", "char_1", "顾言",
               reference_image="generated/char_1.png", status="ready"),
    ])

    ref_wf = tmp_path / "reference_api.json"
    ref_wf.write_text(json.dumps(_template(with_reference=True), ensure_ascii=False),
                      encoding="utf-8")
    out = store.export_comfy_batch(reference_workflow=str(ref_wf))
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    job = manifest["jobs"][0]

    assert job["reference_image"] == "images/char_1_front.png"
    with Image.open(out / job["reference_image"]) as img:
        assert img.size == (960, 1280)
    filled = json.loads((out / job["workflow"]).read_text(encoding="utf-8"))
    assert filled["5"]["inputs"]["image"] == "char_1_front.png"
    makeup_sides = manifest["jobs"][1:4]
    for angle in makeup_sides:
        assert angle["depends_on"] == "char_1_front"
        filled = json.loads((out / angle["workflow"]).read_text(encoding="utf-8"))
        assert filled["5"]["inputs"]["image"] == "char_1_front_input.png"
    fullbody_jobs = manifest["jobs"][4:]
    assert [job["prefix"] for job in fullbody_jobs] == [
        "char_1_fb_front", "char_1_fb_side", "char_1_fb_back",
    ]
    # 正面任务没有依赖，参考图用固定锚点；侧面/背面依赖本层正面
    assert fullbody_jobs[0]["depends_on"] == ""
    for job in fullbody_jobs[1:]:
        assert job["depends_on"] == "char_1_fb_front"
        filled = json.loads((out / job["workflow"]).read_text(encoding="utf-8"))
        assert filled["5"]["inputs"]["image"] == "char_1_fb_front_input.png"


def test_export_batch_warns_when_workflow_has_no_size_placeholders(store, tmp_path):
    """模板写死尺寸时画幅不会生效，README 必须明确警告，不能让人以为已按类型设好。"""
    wf = tmp_path / "fixed_size.json"
    wf.write_text(json.dumps({
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{positive}}"}},
        "3": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 896}},
    }, ensure_ascii=False), encoding="utf-8")
    out = store.export_comfy_batch(workflow=str(wf))
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["workflow_size_placeholders"] is False
    assert "{{width}}" in (out / "README.md").read_text(encoding="utf-8")


def test_export_batch_includes_episode_keyframes(store, tmp_path):
    """批次包要带本集首尾帧：每镜首帧/尾帧两个任务，画布按剧集画幅。"""
    store.save_storyboard(1, {"shots": [{
        "shot_id": "E01-S001", "shot_size": "全景", "camera": "平视，固定",
        "duration_sec": 3, "location": "藏经阁", "time": "夜", "characters": "顾言",
        "action": "顾言朝画面左走两步", "dialogue": "", "sfx": "",
        "transition": "硬切", "status": "draft", "notes": "",
    }]})
    store.save_keyframes(1, [{
        "shot_id": "E01-S001", "first_frame": "顾言站在门口，手边有木匣",
        "last_frame": "顾言停在书架前", "motion": "顾言朝画面左走两步",
    }])
    wf = tmp_path / "workflow_api.json"
    wf.write_text(json.dumps(_template(), ensure_ascii=False), encoding="utf-8")

    out = store.export_comfy_batch(workflow=str(wf), episode=1, include_assets=False)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["jobs"] == []
    assert manifest["episode"] == 1
    assert manifest["keyframe_missing"] == []
    jobs = manifest["keyframe_jobs"]
    assert [job["kind"] for job in jobs] == ["keyframe_first", "keyframe_last"]
    assert (jobs[0]["width"], jobs[0]["height"]) == (720, 1280)
    assert "手边有木匣" in jobs[0]["positive"]
    assert jobs[0]["workflow"].startswith("workflows/")
    filled = json.loads((out / jobs[0]["workflow"]).read_text(encoding="utf-8"))
    assert filled["3"]["inputs"]["width"] == 720
    assert "首尾帧" in (out / "prompts.md").read_text(encoding="utf-8")


def test_export_batch_skips_image_with_aspect_warning(store):
    generated = store.assets_dir / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "char_1.png").write_bytes(b"bad-aspect")
    store.save_assets([
        _asset("character", "char_1", "顾言",
               image="generated/char_1.png", aspect_warning="比例不对", status="needs_crop"),
    ])
    out = store.export_comfy_batch()
    job = json.loads((out / "manifest.json").read_text(encoding="utf-8"))["jobs"][0]
    assert job["reference_image"] == ""  # 比例不对的图不进批次，避免固化错误
