"""首尾帧说明：画布、提示词、解析、体检、视频提示词压缩。"""
from __future__ import annotations

import json

from src.novel_writer.core.drama_keyframes import (
    audit_keyframe_briefs,
    build_keyframe_prompt,
    compose_keyframe_image_prompt,
    compose_video_prompt,
    keyframe_canvas,
    parse_keyframe_briefs,
    ratio_label,
)


SHOTS = [
    {"shot_id": "E01-S001", "shot_size": "全景", "camera": "平视，固定",
     "duration_sec": 3, "location": "藏经阁", "time": "夜", "characters": "顾言",
     "action": "顾言朝画面左走两步", "dialogue": "", "transition": "硬切"},
    {"shot_id": "E01-S002", "shot_size": "特写", "camera": "推近",
     "duration_sec": 2.5, "location": "藏经阁", "time": "夜", "characters": "顾言",
     "action": "手指停在木匣锁扣上", "dialogue": "", "transition": "切"},
]


def test_keyframe_canvas_follows_aspect():
    assert keyframe_canvas("9:16") == (720, 1280)
    assert keyframe_canvas("16:9") == (1280, 720)
    assert keyframe_canvas("1:1") == (720, 720)
    assert keyframe_canvas("") == (720, 1280)
    assert ratio_label(720, 1280) == "9:16"
    prompt = compose_keyframe_image_prompt(
        "顾言在门口", style="仙侠写实", palette="夜戏冷蓝", ratio="9:16"
    )
    assert "画幅 9:16" in prompt and "夜戏冷蓝" in prompt


def test_build_prompt_contains_shots_and_visual_tone():
    prompt = build_keyframe_prompt(
        1, SHOTS, style="仙侠写实", palette="夜戏冷蓝", asset_hints="顾言：白衣"
    )
    assert "E01-S001" in prompt and "E01-S002" in prompt
    assert "首帧画什么" in prompt
    assert "仙侠写实" in prompt and "夜戏冷蓝" in prompt and "顾言：白衣" in prompt


def test_parse_briefs_aligns_by_shot_id_and_skips_empty():
    text = "```json\n" + json.dumps({"shots": [
        {"shot_id": "E01-S001", "first_frame": "顾言站在门口，手边有木匣",
         "last_frame": "顾言停在书架前，木匣在手里", "motion": "顾言朝画面左走两步"},
        {"shot_id": "E01-S999", "first_frame": "多出来的镜", "last_frame": "x", "motion": "x"},
    ]}, ensure_ascii=False) + "\n```"
    briefs = parse_keyframe_briefs(text, SHOTS)
    assert [b["shot_id"] for b in briefs] == ["E01-S001"]
    assert "木匣" in briefs[0]["first_frame"]


def test_parse_briefs_falls_back_to_order_when_model_omits_ids():
    text = json.dumps({"shots": [
        {"first_frame": "首帧一", "last_frame": "尾帧一", "motion": "动作一"},
        {"first_frame": "首帧二", "last_frame": "尾帧二", "motion": "动作二"},
    ]}, ensure_ascii=False)
    briefs = parse_keyframe_briefs(text, SHOTS)
    assert [b["shot_id"] for b in briefs] == ["E01-S001", "E01-S002"]
    assert briefs[1]["first_frame"] == "首帧二"


def test_audit_keyframes_flags_missing_and_identical_frames():
    briefs = [
        {"shot_id": "E01-S001", "first_frame": "同一张", "last_frame": "同一张",
         "motion": "站着"},
    ]
    messages = [item["issue"] for item in audit_keyframe_briefs(SHOTS, briefs)]
    assert any("缺首尾帧说明" in m for m in messages)  # S002 没写
    assert any("同一张图" in m for m in messages)
    assert any("中间动作太短" in m for m in messages)


def test_compose_video_prompt_has_motion_and_lock():
    prompt = compose_video_prompt(
        {"shot_id": "E01-S001", "motion": "顾言朝画面左走两步，抬手抽出木匣"},
        SHOTS[0], style="仙侠写实", palette="夜戏冷蓝",
    )
    assert "顾言朝画面左走两步" in prompt
    assert "一镜到底" in prompt and "人物不说话" in prompt
    assert "藏经阁·夜" in prompt and "夜戏冷蓝" in prompt
