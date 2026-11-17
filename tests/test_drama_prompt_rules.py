"""短剧提示词工程：画幅规格、出图尺寸核对、分镜体检、七段式视频提示词。

这一组测试是文章经验的锚点——改提示词/画幅/审核逻辑时先看这里，
避免「人物 3:4、场景 16:9、道具 1:1」和七段式写法在重构中回退。
"""
from __future__ import annotations

import struct
import zlib

import pytest

from src.novel_writer.core.drama import (
    ASSET_PROMPT_PLANS,
    VIDEO_PROMPT_SECTIONS,
    DramaStore,
    aspect_matches,
    asset_canvas,
    audit_video_prompt,
    build_shot_video_prompt,
    image_dimensions,
)


def png_bytes(width: int, height: int) -> bytes:
    raw = b"\x00" + b"\x00\x00\x00" * width

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg_bytes(width: int, height: int) -> bytes:
    """最小 JPEG 头：SOI + APP0 + SOF0，够 image_dimensions 读宽高。"""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = (b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
            + struct.pack(">HH", height, width) + b"\x03"
            + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01")
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


@pytest.fixture()
def store(tmp_path):
    st = DramaStore(tmp_path)
    st.ensure({"title": "测试"})
    return st


def _asset(kind: str, name: str = "顾言") -> dict:
    return {"kind": kind, "id": f"{kind}_1", "name": name, "source": "canon",
            "prompt": "", "ref": "", "status": "draft", "notes": "", "entity": name, "image": ""}


def test_asset_canvas_matches_kind():
    """人物 3:4 竖屏、场景 16:9 横屏、道具 1:1 方图——文件里写死，不靠模型自觉。"""
    assert asset_canvas("character") == (960, 1280)
    assert asset_canvas("location") == (1280, 720)
    assert asset_canvas("prop") == (1024, 1024)
    assert ASSET_PROMPT_PLANS["character"]["ratio"] == "3:4"
    assert ASSET_PROMPT_PLANS["location"]["ratio"] == "16:9"
    assert ASSET_PROMPT_PLANS["prop"]["ratio"] == "1:1"


def test_plan_ratio_phrase_matches_suffix():
    """画幅句只写一处会漂：模板后缀和手填补句必须用同一句。"""
    for kind, plan in ASSET_PROMPT_PLANS.items():
        for key in ("ratio", "orientation", "ratio_phrase", "width", "height",
                    "suffix", "fallback_desc", "negative", "reference_prefix"):
            assert key in plan, f"{kind} 缺 {key}"
        assert plan["ratio_phrase"] in plan["suffix"], kind
        assert plan["ratio"] in plan["ratio_phrase"], kind


def test_manual_prompt_ratio_words_do_not_false_trigger(store):
    """「画幅感强」不是画幅声明；「22:30」是时间不是比例。"""
    asset = _asset("character", "顾言")
    asset["prompt"] = "顾言，人物定妆图，画幅感强，22:30 的夜戏"
    positive, _ = store.asset_prompt(asset)
    assert "画幅 3:4 竖构图" in positive  # 没声明 → 补上正确画幅
    assert not any("不一致" in issue for issue in store.audit_asset_prompt(asset))

    # 已经用像素写对了比例：不再重复补
    asset["prompt"] = "顾言，人物定妆图，960:1280，单人全身"
    positive, _ = store.asset_prompt(asset)
    assert positive.count("画幅") == 0


def test_manual_prompt_conflicting_ratio_is_flagged(store):
    """手填了别的画幅要报冲突：参数按类型传，文字写别的比例图一定歪。"""
    asset = _asset("character", "顾言")
    asset["prompt"] = "顾言，人物定妆图，画幅 9:16，单人全身"
    positive, _ = store.asset_prompt(asset)
    assert "画幅 9:16" in positive and "3:4" not in positive  # 不擅自改用户文字
    issues = store.audit_asset_prompt(asset)
    assert any("不一致" in issue and "9:16" in issue for issue in issues)


def test_prompt_text_also_states_the_ratio(store):
    """画幅既传 width/height 也写进正向词：接口/模板丢参数时还有文字兜底。"""
    for kind, ratio, word in (
        ("character", "3:4", "竖构图"),
        ("location", "16:9", "横构图"),
        ("prop", "1:1", "方构图"),
    ):
        positive, _ = store.asset_prompt(_asset(kind))
        assert ratio in positive
        assert word in positive


def test_manual_prompt_still_gets_ratio_sentence(store):
    """手填提示词不能绕过画幅：没写就补一句，避免参数 3:4、文字没提。"""
    asset = _asset("location", "藏经阁")
    asset["prompt"] = "藏经阁，场景参考图，雨夜，青石台阶"
    positive, _ = store.asset_prompt(asset)
    assert "画幅 16:9 横构图" in positive

    asset["prompt"] = "藏经阁，场景参考图，画幅 16:9，雨夜"
    positive, _ = store.asset_prompt(asset)
    assert positive.count("画幅") == 1  # 已经写了不重复补


def test_image_dimensions_reads_png_jpeg_gif():
    assert image_dimensions(png_bytes(960, 1280)) == (960, 1280)
    assert image_dimensions(jpeg_bytes(1280, 720)) == (1280, 720)
    assert image_dimensions(b"GIF89a" + struct.pack("<HH", 320, 240) + b"\x00" * 8) == (320, 240)
    assert image_dimensions(b"not an image") is None


def test_aspect_matches_uses_relative_tolerance():
    assert aspect_matches(960, 1280, (960, 1280))
    assert not aspect_matches(1024, 1024, (960, 1280))  # 方图不能当人物竖版用
    assert aspect_matches(1280, 720, (1920, 1080))      # 同比例不同分辨率算通过
    assert aspect_matches(0, 0, (960, 1280))            # 读不出尺寸不误报


def test_palette_is_written_into_asset_and_video_prompts(store):
    """配色在画第一张图之前定死，并同时进生图提示词和七段式。"""
    store.save_series({"style": "仙侠写实", "palette": "夜戏冷蓝 + 暖黄灯笼"})

    positive, _ = store.asset_prompt(_asset("location", "藏经阁"))
    assert "配色：夜戏冷蓝 + 暖黄灯笼" in positive

    prompt = build_shot_video_prompt(
        {"shot_size": "中景", "camera": "固定", "duration_sec": 3,
         "location": "藏经阁", "characters": "顾言", "action": "推门"},
        style="仙侠写实", palette="夜戏冷蓝 + 暖黄灯笼",
    )
    assert "配色：夜戏冷蓝 + 暖黄灯笼" in prompt


def test_save_generated_image_records_size_and_warns_on_wrong_aspect(store):
    store.save_assets([_asset("character")])
    store.save_generated_image("character_1", png_bytes(960, 1280), expected_size=(960, 1280))
    asset = store.load_assets()[0]
    assert asset["image_size"] == "960x1280"
    assert asset["aspect_warning"] == ""

    # 第二次出图变成方图：要留下告警、状态标 needs_crop，并出现在「检查资产」里
    store.save_generated_image("character_1", png_bytes(1024, 1024), expected_size=(960, 1280))
    asset = store.load_assets()[0]
    assert asset["image_size"] == "1024x1024"
    assert "不一致" in asset["aspect_warning"]
    assert asset["status"] == "needs_crop"
    report = store.check_assets()
    assert any("顾言" in item["name"] for item in report["aspect_risk"])


def test_audit_asset_prompt_flags_feature_without_side(store):
    """固定特征只写「有纹身」不写左右，后面每张图的位置都会漂。"""
    asset = _asset("character", "顾言")
    asset["prompt"] = "顾言，人物定妆图，脸上有纹身"
    issues = store.audit_asset_prompt(asset)
    assert any("没写位置" in i for i in issues)

    asset["prompt"] = "顾言，人物定妆图，左脸有纹身"
    assert not any("没写位置" in i for i in store.audit_asset_prompt(asset))


def test_regeneration_keeps_old_image_in_versions(store):
    """新版本存新文件：重生成不覆盖旧图，旧图留档方便对比挑选。"""
    store.save_assets([_asset("character")])
    first = png_bytes(960, 1280)
    second = png_bytes(1024, 1024)
    store.save_generated_image("character_1", first, expected_size=(960, 1280))
    store.save_generated_image("character_1", second, expected_size=(960, 1280))

    versions = list((store.assets_dir / "generated" / "versions").glob("character_1_*"))
    assert len(versions) == 1
    assert versions[0].read_bytes() == first
    assert (store.assets_dir / "generated" / "character_1.png").read_bytes() == second


def test_wrong_aspect_image_is_not_used_as_reference_anchor(store):
    """画幅不对的图不能当定妆图，否则错误比例会被固化成一致性基准。"""
    store.save_assets([_asset("location", "藏经阁")])
    store.save_generated_image("location_1", png_bytes(1024, 1024), expected_size=(1280, 720))
    asset = store.load_assets()[0]
    assert asset["status"] == "needs_crop"
    assert asset["reference_image"] == ""
    assert not (store.assets_dir / "generated" / "references").exists()


def test_unreadable_image_is_marked_needs_check(store):
    store.save_assets([_asset("character")])
    store.save_generated_image("character_1", b"not-an-image", expected_size=(960, 1280))
    asset = store.load_assets()[0]
    assert asset["status"] == "needs_check"
    assert asset["reference_image"] == ""
    assert "读不出" in asset["aspect_warning"]


def test_audit_storyboard_covers_expanded_weak_spots(store):
    """文章点名的弱点不止镜子/文字：交叉、换光、手拿小东西、俯拍、配角太多也要查。"""
    shots = [
        {"shot_id": "S1", "shot_size": "中景", "camera": "推近", "duration_sec": 3,
         "characters": "顾言", "action": "顾言和护卫交叉走过", "transition": "硬切"},
        {"shot_id": "S2", "shot_size": "近景", "camera": "摇", "duration_sec": 3,
         "characters": "顾言", "action": "烛台换光，房间由暗转亮", "transition": "切"},
        {"shot_id": "S3", "shot_size": "特写", "camera": "推近", "duration_sec": 2,
         "characters": "顾言", "action": "手里拿着小刀", "transition": "切"},
        {"shot_id": "S4", "shot_size": "全景", "camera": "俯拍，固定", "duration_sec": 3,
         "characters": "顾言、苏月、护卫甲、护卫乙、路人", "action": "众人围住门口",
         "transition": "切"},
    ]
    messages = [item["issue"] for item in store.audit_storyboard(shots)]
    assert any("交叉" in m for m in messages)
    assert any("换光" in m for m in messages)
    assert any("小东西" in m for m in messages)
    assert any("俯拍" in m for m in messages)
    assert any("配角太多" in m for m in messages)


def test_audit_storyboard_flags_article_pitfalls(store):
    shots = [
        {"shot_id": "S1", "shot_size": "中景", "camera": "平视", "duration_sec": 3,
         "action": "顾言朝画面左走两步", "transition": "硬切"},
        {"shot_id": "S2", "shot_size": "中景", "camera": "平视", "duration_sec": 3,
         "action": "顾言朝画面左走两步", "transition": "硬切"},
        {"shot_id": "S3", "shot_size": "中景", "camera": "平视", "duration_sec": 3,
         "action": "众人同时抬手", "transition": ""},
        {"shot_id": "S4", "shot_size": "近景", "camera": "固定", "duration_sec": 8,
         "action": "顾言坐着", "transition": "切"},
        {"shot_id": "S5", "shot_size": "特写", "camera": "固定", "duration_sec": 3,
         "action": "顾言看向镜子", "transition": "切"},
    ]
    messages = [item["issue"] for item in store.audit_storyboard(shots)]

    assert any("连续三镜" in m for m in messages)
    assert any("平视正面超过两次" in m for m in messages)
    assert any("同时" in m for m in messages)
    assert any("转到下一镜" in m for m in messages)
    assert any("偏长" in m for m in messages)
    assert any("镜子" in m for m in messages)


def test_audit_storyboard_flags_missing_direction(store):
    shots = [
        {"shot_id": "S1", "shot_size": "中景", "camera": "跟", "duration_sec": 3,
         "action": "顾言走过长廊", "transition": "硬切"},
    ]
    messages = [item["issue"] for item in store.audit_storyboard(shots)]
    assert any("画面左/右" in m for m in messages)


def test_audit_storyboard_static_check_handles_camera_with_angle(store):
    """camera 现在写「机位，运镜」，固定机位判定不能再用 == "固定"。"""
    shots = [
        {"shot_id": "S1", "shot_size": "中景", "camera": "平视，固定", "duration_sec": 3,
         "action": "顾言坐着", "transition": "硬切"},
    ]
    messages = [item["issue"] for item in store.audit_storyboard(shots)]
    assert any("静态图" in m for m in messages)


def test_build_shot_video_prompt_has_seven_sections_and_passes_audit():
    shot = {
        "shot_id": "E01-S001", "shot_size": "中景", "camera": "固定",
        "duration_sec": 3.0, "location": "藏经阁", "time": "夜",
        "characters": "顾言", "action": "顾言朝画面左走两步，停在书架前",
        "dialogue": "顾言：谁", "sfx": "脚步和翻页声", "transition": "硬切",
    }
    prompt = build_shot_video_prompt(shot, style="仙侠写实", aspect="9:16")

    for section in VIDEO_PROMPT_SECTIONS:
        assert section in prompt
    assert "3.0 秒" in prompt and "画幅 9:16" in prompt
    assert "0–0.2 秒" in prompt and "最后 0.3 秒" in prompt
    assert "人物不说话" in prompt and "一镜到底" in prompt
    assert audit_video_prompt(prompt) == []


def test_audit_video_prompt_catches_missing_sections():
    issues = audit_video_prompt("【画风】写实\n【镜头】3 秒，众人同时抬手")
    assert any("构图与配色" in i for i in issues)
    assert any("人物不说话" in i for i in issues)
    assert any("同时" in i for i in issues)
    assert any("尾帧" in i for i in issues)


def test_video_prompt_fixed_camera_with_angle():
    """camera 写成「平视，固定」时也要出「固定机位」，不能拼成「固定运镜」。"""
    prompt = build_shot_video_prompt({
        "shot_size": "中景", "camera": "平视，固定", "duration_sec": 2.0,
        "characters": "顾言", "action": "顾言低头",
    })
    assert "平视，固定机位" in prompt
    assert "固定运镜" not in prompt


def test_video_prompt_handles_empty_shot_without_people():
    """空镜（无人物）不能写"头和眼睛落在…"，转场也不该混进提示词。"""
    prompt = build_shot_video_prompt({
        "shot_size": "全景", "camera": "固定", "duration_sec": 2.0,
        "location": "山门", "time": "夜", "characters": "",
        "action": "雪落在石阶上", "transition": "硬切",
    })
    assert "没有人物" in prompt
    assert "头和眼睛" not in prompt
    assert "转场" not in prompt
    assert audit_video_prompt(prompt) == []


def test_storyboard_regeneration_keeps_previous_version(store):
    """重新生成分镜不覆盖旧版：旧 JSON 进 storyboard/versions/。"""
    shot = {"shot_id": "E01-S001", "shot_size": "中景", "camera": "固定",
            "duration_sec": 3, "action": "推门", "transition": "硬切"}
    store.save_storyboard(1, {"shots": [shot]}, update_source=True)
    store.save_storyboard(1, {"shots": [dict(shot, action="关门")]}, update_source=True)

    versions = list((store.storyboard_dir / "versions").glob("ep001_*.json"))
    assert len(versions) == 1
    assert "推门" in versions[0].read_text(encoding="utf-8")
    assert store.load_storyboard(1)["shots"][0]["action"] == "关门"


def test_export_video_prompts_writes_markdown_with_checklist(store):
    store.save_storyboard(1, {"shots": [{
        "shot_id": "E01-S001", "shot_size": "中景", "camera": "固定",
        "duration_sec": 3, "location": "藏经阁", "time": "夜",
        "characters": "顾言", "action": "顾言朝画面左走两步", "dialogue": "",
        "sfx": "", "transition": "硬切", "status": "draft", "notes": "",
    }]})
    path = store.export_video_prompts(1)
    text = path.read_text(encoding="utf-8")

    assert path.name == "视频提示词_第001集.md"
    assert "七段式" in text and "【逐秒】" in text
    assert "E01-S001" in text
    assert "通过" in text and "不通过" in text  # 提交前审核清单
    assert "你是审核，只读不改" in text  # 可直接丢给独立审核 agent 的提示词
    assert "转场：硬切" in text  # 转场放镜头标题，不混进提示词（否则和"不切镜"打架）
