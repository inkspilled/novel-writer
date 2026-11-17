"""首尾帧提示词：让模型把每镜写成「首帧画什么 / 尾帧画什么 / 中间发生什么」。

文章里的关键做法：先写首尾帧说明并审核，再画图；首帧要含动作道具、旁人反应提前摆好；
尾帧机位构图与首帧一致，推近镜头用同一构图裁切放大。这里只做文本层，不调模型。
"""
from __future__ import annotations

import json
from typing import Any

from .json_extract import extract_json_object

KEYFRAME_FIELDS = ("first_frame", "last_frame", "motion")


def keyframe_canvas(aspect: str, short_side: int = 720) -> tuple[int, int]:
    """首尾帧画布：按剧集画幅取 720p 短边（9:16 → 720×1280，16:9 → 1280×720）。"""
    try:
        width, height = (int(part) for part in str(aspect or "9:16").split(":", 1))
    except (TypeError, ValueError):
        width, height = 9, 16
    if width <= 0 or height <= 0:
        width, height = 9, 16
    if width == height:
        return short_side, short_side
    if height > width:
        return short_side, round(short_side * height / width / 2) * 2
    return round(short_side * width / height / 2) * 2, short_side


def _shot_payload(shot: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "shot_id", "scene_id", "shot_size", "camera", "duration_sec",
        "location", "time", "characters", "action", "dialogue", "transition",
    )
    return {key: shot.get(key, "") for key in keys}


def build_keyframe_prompt(
    episode: int,
    shots: list[dict[str, Any]],
    *,
    style: str = "",
    palette: str = "",
    asset_hints: str = "",
) -> str:
    """写首尾帧说明的模型提示词（只输出 JSON）。"""
    visual = "；".join(
        part for part in (
            f"风格：{style}" if style.strip() else "",
            f"配色：{palette}" if palette.strip() else "",
            f"人物外观：{asset_hints}" if asset_hints.strip() else "",
        ) if part
    ) or "（未设置，按分镜描述）"
    return (
        f"你在给短剧第{episode}集的分镜写首尾帧说明。只输出 JSON 对象，"
        "不要 Markdown 代码块、不要解释。\n\n"
        '{"shots":[{"shot_id":"E01-S001","first_frame":"首帧画什么",'
        '"last_frame":"尾帧画什么","motion":"中间发生什么"}]}\n\n'
        "要求：\n"
        "1. 每镜都写，shot_id 原样照抄；不能漏镜、不能加镜；\n"
        "2. first_frame 必须包含这一镜动作会用到的道具和人物位置；"
        "旁人反应提前摆好，不要几个人同一姿势；\n"
        "3. last_frame 是动作结束时的画面：机位、构图、人物位置与首帧一致；"
        "推近镜头写「同一构图裁切放大」；\n"
        "4. motion 写中间发生什么：谁先动谁后动、手放在哪、东西从哪来；"
        "每镜只写一两个主要动作；\n"
        "5. 风向、光线要统一；不写画面文字、镜子和倒影；\n"
        "6. 画面里只出现分镜里的人和道具，数量写死；\n"
        "7. 首帧和尾帧尺寸一致，画幅按剧集画幅。\n\n"
        f"【视觉基调】{visual}\n\n"
        f"【分镜】\n{json.dumps([_shot_payload(s) for s in shots], ensure_ascii=False, indent=1)}\n"
    )


def parse_keyframe_briefs(
    text: str, shots: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """解析模型输出；按 shot_id 对齐，缺 id 时按顺序兜底。只保留有内容的镜。"""
    data = extract_json_object(text or "")
    items = data.get("shots") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    by_id = {
        str(item.get("shot_id") or ""): item
        for item in items if isinstance(item, dict)
    }
    # 模型完全没写 shot_id 时才允许按顺序兜底；写了错 id 不能硬配。
    any_ids = any(str(item.get("shot_id") or "").strip() for item in items if isinstance(item, dict))
    briefs: list[dict[str, str]] = []
    for index, shot in enumerate(shots):
        shot_id = str(shot.get("shot_id") or f"S{index + 1:03d}")
        raw = by_id.get(shot_id)
        if raw is None and not any_ids and index < len(items) and isinstance(items[index], dict):
            raw = items[index]
        if not isinstance(raw, dict):
            continue
        brief = {
            "shot_id": shot_id,
            "first_frame": str(raw.get("first_frame") or "").strip(),
            "last_frame": str(raw.get("last_frame") or "").strip(),
            "motion": str(raw.get("motion") or "").strip(),
        }
        if any(brief[key] for key in KEYFRAME_FIELDS):
            briefs.append(brief)
    return briefs


def audit_keyframe_briefs(
    shots: list[dict[str, Any]], briefs: list[dict[str, str]]
) -> list[dict[str, str]]:
    """首尾帧说明体检：缺镜、空字段、首尾一样、动作太少。"""
    issues: list[dict[str, str]] = []
    by_id = {str(item.get("shot_id") or ""): item for item in briefs}
    for shot in shots:
        shot_id = str(shot.get("shot_id") or "")
        brief = by_id.get(shot_id)
        if not brief:
            issues.append({"shot_id": shot_id, "issue": "缺首尾帧说明"})
            continue
        for field in ("first_frame", "last_frame"):
            if not brief.get(field):
                issues.append({"shot_id": shot_id, "issue": f"缺 {field}"})
        if brief.get("first_frame") and brief.get("first_frame") == brief.get("last_frame"):
            issues.append({"shot_id": shot_id, "issue": "首帧和尾帧写成同一张图，视频会不动"})
        if len(brief.get("motion") or "") < 6:
            issues.append({"shot_id": shot_id, "issue": "中间动作太短，写清谁先动、东西从哪来"})
    return issues


def ratio_label(width: int, height: int) -> str:
    """720×1280 → 9:16；给首尾帧提示词补画幅句。"""
    from math import gcd

    left, right = int(width or 0), int(height or 0)
    if left <= 0 or right <= 0:
        return ""
    divisor = gcd(left, right) or 1
    return f"{left // divisor}:{right // divisor}"


def compose_keyframe_image_prompt(
    text: str, *, style: str = "", palette: str = "", ratio: str = ""
) -> str:
    """首帧/尾帧文字 → 生图正向提示词（补风格、配色、画幅、质感）。"""
    parts = [str(text or "").strip()]
    if style.strip():
        parts.append(f"{style.strip()}风格")
    if palette.strip():
        parts.append(f"配色：{palette.strip()}")
    if ratio.strip():
        parts.append(f"画幅 {ratio.strip()}")
    parts.append("电影感，高清细节")
    return "，".join(part for part in parts if part)


def compose_video_prompt(
    brief: dict[str, str],
    shot: dict[str, Any],
    *,
    style: str = "",
    palette: str = "",
) -> str:
    """把首尾帧说明压成视频工作流用的正向提示词（一镜一段）。"""
    from .drama import build_shot_video_prompt

    return build_shot_video_prompt(
        {**shot, "action": brief.get("motion") or shot.get("action", "")},
        style=style, palette=palette, aspect=str(shot.get("aspect") or "9:16"),
    )


def brief_map(briefs: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {str(item.get("shot_id") or ""): item for item in briefs}
