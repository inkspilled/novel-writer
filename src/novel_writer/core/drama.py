"""短剧数据 — 依附于当前小说项目的 drama/ 目录，页面与工作流独立。

目录结构（小说项目根目录下）：
    drama/
    ├── series.json              # 剧集企划：平台/画幅/集数/单集时长/卖点
    ├── bible.md                 # 改编与视觉规则（人设/风格/台词/节奏）
    ├── episodes.json            # 分集元数据
    ├── episodes/ep001.md        # 分集剧本（Markdown，场次标题可解析）
    ├── storyboard/ep001.json    # 结构化镜头表
    ├── assets/index.json        # 资产注册表（角色/场景/道具）
    └── exports/                 # 导出的 CSV / JSON

设计原则：短剧不单独建项目；它依附当前小说，但 UI 是独立大页签，
工作流也不复用小说明章节循环。分镜是剧本的派生层，后续资产/生成再引用分镜 ID。
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import re
import struct
from datetime import datetime
from pathlib import Path
from typing import Any

from .json_extract import extract_json_object
from .drama_scene import SCENE_PANORAMA_PROMPT, SCENE_PANORAMA_SIZE, SCENE_VIEWS, project_scene_views
from .drama_story import pitfalls_markdown
from .logger import get_logger
from . import project_io

logger = get_logger(__name__)

DRAMA_DIR = "drama"
EPISODES_DIR = "episodes"
STORYBOARD_DIR = "storyboard"
ASSETS_DIR = "assets"
EXPORTS_DIR = "exports"
KEYFRAMES_DIR = "keyframes"
VIDEOS_DIR = "videos"

SERIES_FILE = "series.json"
EPISODES_FILE = "episodes.json"
BIBLE_FILE = "bible.md"
ASSETS_FILE = "index.json"
SCHEMA_VERSION = 1

# 分镜字段（JSON 的 key -> 中文表头）。顺序即表格/CSV 的列顺序。
# 字段口径来自文章 L160：片中时间（配歌按小节算）、生成秒数（时长向上取整、
# 多留 0.5 秒）、景别、机位、运镜、谁往哪走（画面左右）、靠什么表情推进、
# 这个镜头是什么在动、怎么转到下一镜。
SHOT_COLUMNS: list[tuple[str, str]] = [
    ("shot_id", "镜号"),
    ("scene_id", "场次"),
    ("shot_size", "景别"),
    ("camera", "机位/运镜"),
    ("duration_sec", "时长(秒)"),
    ("start_sec", "片中时间(秒)"),
    ("gen_seconds", "生成秒数"),
    ("location", "地点"),
    ("time", "时间"),
    ("characters", "人物"),
    ("blocking", "谁往哪走"),
    ("emotion", "表情推进"),
    ("motion", "什么在动"),
    ("action", "画面/动作"),
    ("dialogue", "台词"),
    ("sfx", "音效"),
    ("transition", "转场"),
    ("status", "状态"),
    ("notes", "备注"),
]
SHOT_KEYS = [key for key, _label in SHOT_COLUMNS]

ASSET_COLUMNS: list[tuple[str, str]] = [
    ("kind", "类型"),
    ("id", "ID"),
    ("name", "名称"),
    ("source", "来源"),
    ("prompt", "提示词"),
    ("ref", "参考图"),
    ("reference_image", "定妆图（固定）"),
    ("status", "状态"),
    ("notes", "备注"),
]

ASSET_KIND_LABELS = {"character": "人物", "location": "场景", "prop": "道具"}

# 资产来源：正典（小说里有的）/ 分镜（短剧新增，如龙套、临时场景）/ 手工
ASSET_SOURCES = {"canon": "小说正典", "storyboard": "短剧原创", "manual": "手工"}
_ASSET_SOURCE_ALIASES = {"从小说导入": "canon", "从分镜自动提取": "storyboard"}

# 人物图负面词基础串（本地模板，用户可改工作流模板）
ASSET_NEGATIVE = (
    "低清晰度，模糊，噪点，畸形手指，多余肢体，比例失调，脸部崩坏，"
    "文字，水印，logo，签名，多人重复，过曝，杂乱背景"
)

MAKEUP_VIEWS = {
    "front": ("正面", "正对镜头，两眼和两侧脸颊均可见"),
    "left": ("左侧", "相机位于人物左侧 90 度，只展示人物左侧轮廓"),
    "right": ("右侧", "相机位于人物右侧 90 度，只展示人物右侧轮廓"),
    "back": ("正后方", "相机位于人物正后方，人物不回头，展示后脑、发髻和后颈"),
}

# 人物第二层参考：全身三视图（正/侧/背全身立像）。头肩定妆照管脸部一致性，
# 全身镜头（远景/全景/中景）用全身参考，姿态和服装才不会漂。
FULLBODY_VIEWS = {
    "front": ("全身正面", "从头顶到鞋底完整入画，正面站姿，双臂自然下垂"),
    "side": ("全身侧面", "相机位于人物左侧 90 度，全身侧面站姿，完整入画"),
    "back": ("全身背面", "正后方全身站姿，展示背部、后脑和鞋底，面部不可见"),
}

# 道具三视图：正/侧/背。单张特写只看得到一个面，视频里道具一转就穿帮。
PROP_VIEWS = {
    "front": ("正面", "从正前方平视拍摄，物品正面朝向镜头"),
    "side": ("侧面", "相机位于物品左侧 90 度，展示左侧面轮廓、厚度和侧面细节"),
    "back": ("背面", "从正后方拍摄，展示物品背面细节，正面不可见"),
}

# 按类型分化的生图方案：人物=定妆照，场景=空镜，道具=特写。
# 教训：三类共用"柔和布光、背景干净、正面参考"这类人像词时，
# 地点/道具会被生图模型画成人；场景/道具必须把"人物"挡进负面词。
# 提示词写法吸收的生图经验：
# - 正面式表述优先（"画面中只有建筑陈设"比"不要出现人"可靠），否定交给负面词通道
# - 数量写死（道具"单件"、人物"单人"），防模型自行加戏
# - 画幅按类型：人物 3:4 竖版定妆（即"4:3 竖屏"）、场景 16:9 横版空镜、道具 1:1 方图
#   画幅**既传 width/height，也写进正向词**：模板/接口丢参数时，模型仍按文字画对画幅。
ASSET_PROMPT_PLANS: dict[str, dict[str, Any]] = {
    "character": {
        "suffix": (
            "短剧人物妆发定妆照，画幅 3:4 竖构图（宽:高=3:4），单人头肩特写，"
            "镜头与眼睛等高，柔和日光，背景干净，完整保留头顶、发饰和肩线，高清细节"
        ),
        "fallback_desc": "展示脸型、妆容、发型和发饰",
        "negative": ASSET_NEGATIVE,
        "ratio": "3:4",
        "orientation": "竖版",
        "ratio_phrase": "画幅 3:4 竖构图（宽:高=3:4）",
        "width": 960,
        "height": 1280,  # 3:4 竖版（宽 960 × 高 1280）
        # 垫图重生成时拼在正向词最前面的"保持一致"要求
        "reference_prefix": (
            "以参考图为同一人物的外观基准，保持相同脸型、五官、发型和服装，"
            "只按以下要求调整构图和动作："
        ),
    },
    "location": {
        "suffix": (
            "短剧场景参考图，画幅 16:9 横构图（宽:高=16:9），全景空镜，"
            "前景、中景和远处后景层次清楚，显示场景边界、出入口和可走动的通道，"
            "复杂空间用固定建筑或陈设作为视觉锚点，画面中只有建筑结构、地形和陈设，无人物"
        ),
        "fallback_desc": "展示空间结构、陈设与整体氛围",
        "negative": "人物，人影，人脸，手，" + ASSET_NEGATIVE,
        "ratio": "16:9",
        "orientation": "横版",
        "ratio_phrase": "画幅 16:9 横构图（宽:高=16:9）",
        "width": 1280,
        "height": 720,  # 16:9 横版（宽 1280 × 高 720）
        "reference_prefix": (
            "以参考图为同一场景的外观基准，保持相同建筑结构、空间布局和材质，"
            "只按以下要求调整光线和氛围："
        ),
    },
    "prop": {
        "suffix": (
            "短剧道具参考图，画幅 1:1 方构图（宽:高=1:1），单件物品特写，"
            "物品居中，纯色背景，材质、颜色与造型细节清晰，无人物，"
            "写实质感，高清细节"
        ),
        "fallback_desc": "展示材质、颜色与造型细节",
        "negative": "人物，人影，人脸，手，" + ASSET_NEGATIVE,
        "ratio": "1:1",
        "orientation": "方图",
        "ratio_phrase": "画幅 1:1 方构图（宽:高=1:1）",
        "width": 1024,
        "height": 1024,  # 1:1 方图（1024 × 1024）
        "reference_prefix": (
            "以参考图为同一物品的外观基准，保持相同造型、颜色和材质，"
            "只按以下要求调整角度和光影："
        ),
    },
}

# 生成图与计划画幅的比例容差（相对误差 2%）：超出就说明要裁切，不能直接拿去用
ASPECT_TOLERANCE = 0.02

# 手填提示词里"声明画幅"的两种写法：画幅 3:4 / 9:16 竖屏。
# 只认带画幅语义的词，避免把 "22:30" 这种时间误判成比例。
_DECLARED_RATIO_RE = re.compile(
    r"(?:画幅|比例|宽高比|宽\s*[:：]\s*高)\s*[:：]?\s*(\d{1,4})\s*[:：]\s*(\d{1,4})"
    r"|(\d{1,4})\s*[:：]\s*(\d{1,4})\s*(?:竖屏|横屏|方图|竖版|横版)"
)
_BARE_RATIO_RE = re.compile(r"(?<!\d)(\d{1,4})\s*[:：]\s*(\d{1,4})(?!\d)")


def _declared_ratio(text: str) -> tuple[int, int] | None:
    """从提示词里取显式声明的画幅（画幅 3:4 / 9:16 竖屏）；没有返回 None。"""
    match = _DECLARED_RATIO_RE.search(text or "")
    if not match:
        return None
    if match.group(1):
        return int(match.group(1)), int(match.group(2))
    return int(match.group(3)), int(match.group(4))


def _ratio_equal(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] * right[1] == right[0] * left[1]


def asset_canvas(kind: str) -> tuple[int, int]:
    """资产类型的出图画布：人物 960×1280、场景 1280×720、道具 1024×1024。"""
    plan = ASSET_PROMPT_PLANS.get(str(kind), ASSET_PROMPT_PLANS["character"])
    return int(plan["width"]), int(plan["height"])


def asset_seed(assets_dir: Path | str, asset_id: str) -> int:
    """同一资产固定种子；直连 ComfyUI 与导出批次共用，保证两边结果可复现。"""
    identity = f"{assets_dir}:{asset_id}"
    return int.from_bytes(hashlib.sha256(identity.encode()).digest()[:4], "big")


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """从图片字节读宽高（PNG/JPEG/GIF/WebP）；解析不了返回 None。

    生图服务不保证按请求画幅出图（文章：Codex 出的图不一定正好是 16:9），
    所以落盘时要能核对真实像素，而不是相信请求参数。
    """
    if not data:
        return None
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            return struct.unpack(">II", data[16:24])
        if data[:3] == b"\xff\xd8\xff":
            i, n = 2, len(data)
            while i + 9 < n:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                if marker == 0xD9:
                    break
                seg_len = int.from_bytes(data[i + 2:i + 4], "big")
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    height, width = struct.unpack(">HH", data[i + 5:i + 9])
                    return width, height
                i += 2 + seg_len
            return None
        if data[:4] == b"GIF8" and len(data) >= 10:
            return struct.unpack("<HH", data[6:10])
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
            chunk = data[12:16]
            if chunk == b"VP8X":
                return (1 + int.from_bytes(data[24:27], "little"),
                        1 + int.from_bytes(data[27:30], "little"))
            if chunk == b"VP8L" and len(data) >= 25:
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if chunk == b"VP8 ":
                start = data.find(b"\x9d\x01\x2a", 20)
                if start > 0 and start + 7 <= len(data):
                    width = int.from_bytes(data[start + 3:start + 5], "little") & 0x3FFF
                    height = int.from_bytes(data[start + 5:start + 7], "little") & 0x3FFF
                    return width, height
    except (struct.error, IndexError, ValueError):
        return None
    return None


def aspect_matches(width: int, height: int, expected: tuple[int, int] | None) -> bool:
    """实际宽高比是否在计划画幅的容差内；读不出尺寸时按“未知”放行。"""
    if not width or not height or not expected or not expected[0] or not expected[1]:
        return True
    target = expected[0] / expected[1]
    return abs((width / height) - target) <= target * ASPECT_TOLERANCE

DEFAULT_SERIES: dict[str, Any] = {
    "title": "",
    "genre": "",
    "style": "",
    # 画风确认闸：没确认之前不画正式镜头（文章规矩 2）
    "style_confirmed": False,
    # 配色单独定：在画第一张图之前就写死，不交给模型自由发挥
    "palette": "",
    # 每场颜色表：[{scene, colors, hex}]；画第一张图前定死，转黑白查对比
    "palette_table": [],
    "platform": "抖音",
    "aspect": "9:16",
    "episode_count": 30,
    "episode_seconds": 90,
    "logline": "",
    # 配歌（歌名、时长、BPM）与字幕
    "music": "",
    "subtitles": False,
    "intake": {},
    # 结构：片段式最稳，长篇改编剧集默认连贯式
    "structure": "连贯式",
    # 视频模型与分辨率
    "video_model": "",
    "video_resolution": "720p",
    "requirements": "",
    "reference_video": "",
    "music_file": "",
    "post_options": {},
    "updated_at": "",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _entity_summary(info: Any, cap: int = 200) -> str:
    """世界状态里 地点/物品 的条目摘要（dict 取前几个字符串字段）。"""
    if isinstance(info, str):
        return info.strip()[:cap]
    if not isinstance(info, dict):
        return ""
    bits: list[str] = []
    for key in ("desc", "description", "说明", "位置", "location", "用途", "note", "notes"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            bits.append(value.strip())
    if not bits:
        bits = [str(v).strip() for v in info.values()
                if isinstance(v, str) and v.strip()]
    return "；".join(bits)[:cap]


def _normalize_asset_name(name: str) -> str:
    """资产名归一：去括号注解、空白与间隔号，用于正典匹配/去重。"""
    text = str(name or "").strip()
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    text = re.sub(r"[\s·・.。]+", "", text)
    return text


def _unique_asset_id(assets: list[dict[str, Any]], kind: str, name: str) -> str:
    base = re.sub(r"[^\w\u4e00-\u9fff]+", "_", name).strip("_") or "asset"
    prefix = {"character": "char", "location": "loc", "prop": "prop"}.get(kind, "asset")
    ids = {a.get("id") for a in assets}
    candidate = f"{prefix}_{base}"
    i = 2
    while candidate in ids:
        candidate = f"{prefix}_{base}_{i}"
        i += 1
    return candidate


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("读取短剧数据失败 %s: %s", path, e)
        return default
    return data if isinstance(data, type(default)) else default


def _write_json(path: Path, data: Any) -> None:
    project_io.write_json(path, data)


def episode_filename(number: int) -> str:
    return f"ep{max(1, int(number)):03d}"


def script_template(number: int, title: str = "") -> str:
    """一集剧本的最小模板：场次标题可被 stories_from_script 解析。"""
    heading = title or f"第{number}集"
    return (
        f"# {heading}\n\n"
        f"## 第1场 地点 · 日/夜\n\n"
        f"（画面/动作：先写观众能看见的东西，不写心理活动。）\n\n"
        f"角色：台词（短句、口语化，能一句说完不说两句。）\n\n"
        f"## 第2场 地点 · 日/夜\n\n"
        f"（本集反转/爽点落在这一场。）\n\n"
        f"角色：台词。\n\n"
        f"## 第3场 地点 · 日/夜\n\n"
        f"（集尾钩子：留一个必须点下一集的问题。）\n"
    )


def new_shot(episode: int, index: int) -> dict[str, Any]:
    """一条空镜头；后续由 UI/工作流填写。"""
    duration = 3.0
    return {
        "shot_id": f"E{max(1, int(episode)):02d}-S{max(1, int(index)):03d}",
        "scene_id": "",
        "shot_size": "中景",
        "camera": "平视，固定",
        "duration_sec": duration,
        "start_sec": 0.0,
        "gen_seconds": generation_seconds(duration),
        "location": "",
        "time": "",
        "characters": "",
        "blocking": "",
        "emotion": "",
        "motion": "",
        "action": "",
        "dialogue": "",
        "sfx": "",
        "transition": "硬切",
        "status": "draft",
        "notes": "",
    }


def generation_seconds(duration: float) -> float:
    """生成秒数：片中时长向上取整，并尽量多留 0.5 秒给后期变速/对齐。

    3.0 → 3.5（3.0 已整，再留 0.5）；3.2 → 4.0（向上取整后已有 0.8 余量）；
    2.7 → 3.5（取整 3.0 只剩 0.3，补到 3.5）。
    """
    try:
        value = max(0.0, float(duration))
    except (TypeError, ValueError):
        return 0.0
    if value <= 0:
        return 0.0
    rounded = float(math.ceil(value))
    if rounded - value < 0.5:
        rounded += 0.5
    return round(rounded, 2)


def assign_time_codes(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """逐镜补「片中时间」和「生成秒数」：时间是累计时长，生成秒数向上取整加余量。

    已有 start_sec 的镜头不覆盖（人工调过的顺序要保住），但生成秒数始终按
    当前时长重算。
    """
    cursor = 0.0
    for shot in shots or []:
        if not isinstance(shot, dict):
            continue
        try:
            duration = max(0.0, float(shot.get("duration_sec") or 0))
        except (TypeError, ValueError):
            duration = 0.0
        if not shot.get("start_sec"):
            shot["start_sec"] = round(cursor, 2)
        shot["gen_seconds"] = generation_seconds(duration)
        cursor += duration
    return shots


def shot_time_label(shot: dict[str, Any], bpm: float = 0.0) -> str:
    """分镜表里的「片中时间」写法：配歌按小节算，没配歌写秒。"""
    try:
        start = float(shot.get("start_sec") or 0)
    except (TypeError, ValueError):
        start = 0.0
    if bpm and bpm > 0:
        from .drama_story import bar_label

        return bar_label(start, bpm)
    return f"{start:.1f} 秒"


def normalize_shot(shot: dict | None, episode: int = 1, index: int = 1) -> dict[str, Any]:
    base = new_shot(episode, index)
    if isinstance(shot, dict):
        for key, _label in SHOT_COLUMNS:
            if key in shot and shot[key] is not None:
                base[key] = shot[key]
    # 模型偶尔把人物/台词写成数组，统一成可编辑文本
    for key in (
        "characters", "action", "dialogue", "sfx", "notes", "location", "time",
        "blocking", "emotion", "motion", "camera", "shot_size", "transition",
    ):
        value = base.get(key, "")
        if isinstance(value, (list, tuple)):
            value = "、".join(str(v) for v in value if str(v).strip())
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        base[key] = str(value or "")
    for key in ("duration_sec", "start_sec", "gen_seconds"):
        try:
            base[key] = float(base.get(key) or 0)
        except (TypeError, ValueError):
            base[key] = 0.0
        if not math.isfinite(base[key]) or base[key] < 0:
            base[key] = 0.0
    # 生成秒数没写或小于镜头时长时，按当前时长重算。
    if not base["gen_seconds"] or base["gen_seconds"] + 1e-6 < generation_seconds(base["duration_sec"]):
        base["gen_seconds"] = generation_seconds(base["duration_sec"])
    return base


def clean_script_output(text: str, episode: int, fallback_title: str = "") -> str:
    """清洗模型输出：去 think/代码块/前言，保证剧本以 # 第N集 和 ## 场次 开头。"""
    body = (text or "").strip()
    body = re.sub(r"<think>[\s\S]*?</think>", "", body, flags=re.IGNORECASE).strip()
    fence = re.search(r"```(?:markdown|md)?\s*([\s\S]*?)```", body, flags=re.IGNORECASE)
    if fence:
        body = fence.group(1).strip()
    # 去掉「好的，以下是剧本」这类前言，从第一个 # 第X集 开始
    match = re.search(r"^#\s*第\s*[0-9一二三四五六七八九十百]+\s*集.*$", body, flags=re.MULTILINE)
    if match:
        body = body[match.start():].strip()
    if not body.startswith("#"):
        title = fallback_title or f"第{episode}集"
        body = f"# 第{episode}集 {title}\n\n{body}"
    else:
        first, sep, rest = body.partition("\n")
        m = re.match(r"^#\s*第\s*[0-9一二三四五六七八九十百]+\s*集\s*(.*)$", first)
        if m:
            title = (m.group(1) or fallback_title or "").strip()
            body = f"# 第{episode}集 {title}".rstrip() + sep + rest
    if "##" not in body:
        body = body.rstrip() + f"\n\n## 第1场 {fallback_title or '本集'} · 日/夜\n\n（补场次）\n"
    return body.strip() + "\n"


def parse_storyboard_output(
    text: str, episode: int, default_duration: float = 3.0
) -> list[dict[str, Any]]:
    """解析模型输出的分镜 JSON；失败返回空列表。"""
    data = extract_json_object(text or "")
    if not isinstance(data, dict):
        return []
    raw_shots = data.get("shots")
    if not isinstance(raw_shots, list):
        return []
    shots: list[dict[str, Any]] = []
    for i, raw in enumerate(raw_shots[:100], start=1):
        if not isinstance(raw, dict):
            continue
        shot = normalize_shot(raw, episode, i)
        shot["shot_id"] = f"E{max(1, int(episode)):02d}-S{i:03d}"
        if not shot.get("duration_sec"):
            shot["duration_sec"] = float(default_duration)
        if not shot.get("scene_id"):
            shot["scene_id"] = f"SC{max(1, (i - 1) // 5 + 1):02d}"
        shots.append(shot)
    return assign_time_codes(shots)


# ── 七段式视频提示词（文章里最核心的提示词写法）──

VIDEO_PROMPT_SECTIONS = (
    "【画风】", "【镜头】", "【构图与配色】", "【人物】", "【逐秒】", "【声音】", "【约束】",
)


def _aspect_orientation(aspect: str) -> str:
    try:
        width, height = (int(part) for part in str(aspect).split(":", 1))
    except (TypeError, ValueError):
        return ""
    if width <= 0 or height <= 0:
        return ""
    return "竖屏" if height > width else ("横屏" if width > height else "方屏")


def build_shot_video_prompt(
    shot: dict[str, Any], *, style: str = "", aspect: str = "9:16", palette: str = ""
) -> str:
    """把一条分镜展开成七段式视频提示词初稿。

    【画风】【镜头】【构图与配色】【人物】【逐秒】【声音】【约束】七段缺一不可：
    逐秒写死开头静止、结尾对齐尾帧，是降低抽卡率最有效的一段。
    这里只把分镜已有信息排进模板；动作细节由导演/模型再补，不凭空编。
    """
    duration = 3.0
    try:
        duration = max(0.5, float(shot.get("duration_sec") or 3.0))
    except (TypeError, ValueError):
        pass
    try:
        gen_seconds = float(shot.get("gen_seconds") or 0)
    except (TypeError, ValueError):
        gen_seconds = 0.0
    size = str(shot.get("shot_size") or "中景").strip() or "中景"
    camera = str(shot.get("camera") or "固定").strip() or "固定"
    location = str(shot.get("location") or "").strip()
    time_text = str(shot.get("time") or "").strip()
    characters = str(shot.get("characters") or "").strip()
    action = str(shot.get("action") or "").strip() or "（待补：这一镜发生什么）"
    if action[-1] not in "。！？.!?":
        action += "。"
    # 走位/表情/什么在动：分镜里单独填了就补进逐秒段，没填就不硬编
    details: list[str] = []
    blocking = str(shot.get("blocking") or "").strip()
    emotion = str(shot.get("emotion") or "").strip()
    motion = str(shot.get("motion") or "").strip()
    if blocking and blocking not in action:
        details.append(f"走位：{blocking}。")
    if emotion and emotion not in action:
        details.append(f"表情推进：{emotion}。")
    if motion and motion not in action:
        details.append(f"画面里在动的是：{motion}。")
    action_line = action + ("".join(details) if details else "")
    sfx = str(shot.get("sfx") or "").strip()
    orientation = _aspect_orientation(aspect)

    if "固定" in camera:
        # camera 可能写「平视，固定」：角度保留，运镜说明固定
        angle = camera.replace("固定", "").strip(" ，,、")
        camera_text = (f"{angle}，" if angle else "") + "固定机位，不推不拉不摇，不切镜"
    else:
        camera_text = f"{camera}运镜，一镜到底不切镜"
    scene = "·".join(bit for bit in (location, time_text) if bit) or "本镜场景"
    mid_end = max(0.3, duration - 0.3)
    style_text = style.strip() or "写实电影感，和首帧、尾帧完全一致"
    color_text = f"配色：{palette.strip()}，和首帧一致" if palette.strip() else "配色和首帧一致"
    sound_text = f"只有{sfx}；人物不说话。" if sfx else "只有画面里看得见原因的声音；人物不说话。"
    length_text = f"整段 {duration:.1f} 秒"
    if gen_seconds and gen_seconds > duration:
        length_text += f"（生成 {gen_seconds:.1f} 秒，后期留余量）"

    if characters:
        composition = (
            f"{scene}；{characters}在画面里，头和眼睛落在画面横向 38% 或 62% 的位置，"
            f"不放正中；{color_text}，画面里只有本镜的人和道具。"
        )
        people_line = (
            f"{characters}；长相、发型、服装和随身固定道具与定妆图一致，全程不变。"
        )
        constraint = (
            "画面里一直只有首帧这几个人和这些东西，脸和身形全程不变，一镜到底；"
            "画面干净，没有文字、字幕、水印。"
        )
    else:
        composition = f"{scene}；画面里只有场景、建筑和道具，没有人物；{color_text}。"
        people_line = "本镜没有人物，只有场景和道具，形状、材质和位置全程不变。"
        constraint = (
            "画面里一直只有首帧这些场景和道具，形状和位置全程不变，一镜到底；"
            "画面干净，没有文字、字幕、水印。"
        )

    lines = [
        f"【画风】{style_text}。整段的画风、颜色、人物设计与首帧和尾帧完全一致。",
        f"【镜头】{length_text}，{size}，{camera_text}；画幅 {aspect}"
        + (f" {orientation}" if orientation else "") + "。",
        f"【构图与配色】{composition}",
        f"【人物】{people_line}",
        "【逐秒】",
        "0–0.2 秒：画面完全静止，和首帧一模一样。",
        f"0.2–{mid_end:.1f} 秒：{action_line}",
        "最后 0.3 秒：所有人和物完全停住，画面与尾帧完全一致。",
        f"【声音】{sound_text}",
        f"【约束】{constraint}",
    ]
    return "\n".join(lines)


def audit_video_prompt(text: str) -> list[str]:
    """视频提示词提交前体检：七段齐不齐、有没有文章点名的坏写法。

    规则来自文章 L232-L274 的 20 行错误对照表；能本地查的查，查不了的
    （云长脸、闪电太写实这类）在导出清单里交给人看。
    """
    body = text or ""
    issues: list[str] = []
    for section in VIDEO_PROMPT_SECTIONS:
        if section not in body:
            issues.append(f"缺「{section.strip('【】')}」段")
    if "人物不说话" not in body:
        issues.append("没写「人物不说话」，模型容易自己动嘴")
    if "一镜到底" not in body:
        issues.append("没写「一镜到底」，模型可能中途切镜")
    if "尾帧" not in body or "最后" not in body:
        issues.append("结尾没有对齐尾帧，最后几帧容易跳")
    if any(word in body for word in ("同时", "一齐", "众人")):
        issues.append("有多人同时动作，改成谁先谁后")
    if "不要" in body or "不能" in body:
        issues.append("有否定句，尽量改成正面的「画面里一直只有…」")
    if "没有人物" not in body and ("38%" not in body or "62%" not in body):
        issues.append("没写 38%/62% 黄金位置，构图容易摆正中")
    if body.count("然后") >= 3:
        issues.append("一个镜头里动作太多，每镜只写一两个主要动作")
    from .drama_story import audit_prompt_pitfalls

    for item in audit_prompt_pitfalls(body, has_keyframes=True):
        if item not in issues:
            issues.append(item)
    return issues


class DramaStore:
    """短剧项目数据访问层；不依赖 Qt，方便单测与后续工作流调用。"""

    def __init__(self, project_dir: Path | str):
        self.project_dir = Path(project_dir)
        self.root = self.project_dir / DRAMA_DIR
        self.episodes_dir = self.root / EPISODES_DIR
        self.storyboard_dir = self.root / STORYBOARD_DIR
        self.assets_dir = self.root / ASSETS_DIR
        self.exports_dir = self.root / EXPORTS_DIR
        self.keyframes_dir = self.root / KEYFRAMES_DIR
        self.videos_dir = self.root / VIDEOS_DIR

    # ── 初始化 ──

    def ensure(self, meta: dict | None = None) -> None:
        for sub in (
            self.episodes_dir, self.storyboard_dir, self.assets_dir,
            self.exports_dir, self.keyframes_dir, self.videos_dir,
        ):
            sub.mkdir(parents=True, exist_ok=True)
        meta = meta or {}
        if not (self.root / SERIES_FILE).exists():
            series = dict(DEFAULT_SERIES)
            series.update({
                "title": meta.get("title", ""),
                "genre": meta.get("genre", ""),
                "style": meta.get("style", ""),
                "episode_count": int(meta.get("target_chapters") or 30),
                "logline": meta.get("synopsis", ""),
                "updated_at": _now(),
            })
            _write_json(self.root / SERIES_FILE, series)
        if not (self.root / EPISODES_FILE).exists():
            _write_json(self.root / EPISODES_FILE, {
                "episodes": [{"number": 1, "title": "第1集", "goal": "", "hook": "", "status": "draft"}],
                "updated_at": _now(),
            })
            if not self.script_path(1).exists():
                self.save_script(1, script_template(1, "第1集"))
        if not (self.assets_dir / ASSETS_FILE).exists():
            _write_json(self.assets_dir / ASSETS_FILE, {"assets": [], "updated_at": _now()})
        if not (self.root / BIBLE_FILE).exists():
            (self.root / BIBLE_FILE).write_text("", encoding="utf-8")

    # ── 企划 ──

    def load_series(self) -> dict[str, Any]:
        # 深拷贝：palette_table / intake 是可变默认值，浅拷贝会被多处写串
        series = copy.deepcopy(DEFAULT_SERIES)
        data = _read_json(self.root / SERIES_FILE, {})
        if isinstance(data, dict):
            series.update({k: v for k, v in data.items() if k in series})
        return series

    def save_series(self, data: dict[str, Any]) -> dict[str, Any]:
        series = self.load_series()
        for key in DEFAULT_SERIES:
            if key in data:
                series[key] = data[key]
        series["updated_at"] = _now()
        _write_json(self.root / SERIES_FILE, series)
        return series

    def bible_text(self) -> str:
        path = self.root / BIBLE_FILE
        try:
            return path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError:
            return ""

    def save_bible(self, text: str) -> None:
        if self.bible_text() != text:
            from .drama_production import preserve
            preserve(self.root / BIBLE_FILE)
        project_io.write_md(self.root / BIBLE_FILE, text or "")

    def build_bible_from_novel(self) -> str:
        """把小说规划文档汇总成改编圣经草稿（确定性，不调用 LLM）。"""
        planning = self.project_dir / "planning"
        docs = {
            "核心立意": "立意.md",
            "故事骨架": "大纲.md",
            "人物视觉锚点": "人物设定.md",
            "世界观": "世界观.md",
        }
        sections: list[str] = []
        for label, filename in docs.items():
            path = planning / filename
            try:
                body = path.read_text(encoding="utf-8").strip() if path.exists() else ""
            except OSError:
                body = ""
            sections.append(f"## {label}\n\n{body or '（小说规划里还没有这一节）'}")
        series = self.load_series()
        text = (
            f"# {series.get('title') or '短剧'} · 改编圣经（草稿）\n\n"
            "> 由小说规划文档自动汇总。AI 精修与短剧化规则下一阶段接入；\n"
            "> 这里先给出人物/世界观/故事骨架，避免改编时丢设定。\n\n"
            + "\n\n".join(sections)
            + "\n\n## 短剧化规则（待填）\n\n"
            f"- 平台/画幅：{series.get('platform')} / {series.get('aspect')}\n"
            f"- 集数与单集时长：{series.get('episode_count')} 集 × {series.get('episode_seconds')} 秒\n"
            "- 每集钩子：前 3 秒冲突、结尾 5 秒留问题。\n"
            "- 保留主线；支线只留能改变人物关系的。\n"
        )
        self.save_bible(text)
        return text

    # ── 剧本 ──

    def load_episodes(self) -> list[dict[str, Any]]:
        data = _read_json(self.root / EPISODES_FILE, {})
        episodes = data.get("episodes") if isinstance(data, dict) else None
        if not isinstance(episodes, list):
            return [{"number": 1, "title": "第1集", "goal": "", "hook": "", "status": "draft"}]
        cleaned: list[dict[str, Any]] = []
        for i, ep in enumerate(episodes, start=1):
            if not isinstance(ep, dict):
                continue
            cleaned.append({
                "number": int(ep.get("number") or i),
                "title": str(ep.get("title") or f"第{i}集"),
                "goal": str(ep.get("goal") or ""),
                "hook": str(ep.get("hook") or ""),
                "status": str(ep.get("status") or "draft"),
                "source_chapter": int(ep.get("source_chapter") or 0),
                "source_chapters": ep.get("source_chapters", []),
                "source_hashes": ep.get("source_hashes", {}),
                "adaptation_plan": ep.get("adaptation_plan", {}),
            })
        return sorted(cleaned, key=lambda ep: ep["number"])

    def save_episodes(self, episodes: list[dict[str, Any]]) -> None:
        _write_json(self.root / EPISODES_FILE, {"episodes": episodes, "updated_at": _now()})

    def next_adaptive_episode_number(self) -> int:
        episodes = self.load_episodes()
        for ep in episodes:
            if not ep.get("source_chapter") and not ep.get("source_chapters") and not self.has_script(ep["number"]):
                return int(ep["number"])
        return max((int(ep["number"]) for ep in episodes), default=0) + 1

    def commit_adaptive_episode(self, number: int, plan: dict, chapters: list[dict], script: str) -> None:
        """剧本成功写盘后才提交来源映射；元数据是已消费素材的权威来源。"""
        import hashlib
        episodes = self.load_episodes()
        sources = [int(c["number"]) for c in chapters]
        if sources != plan["source_chapters"]:
            raise ValueError("分集来源与选定素材不一致")
        for ep in episodes:
            existing = ep.get("source_chapters") or ([ep["source_chapter"]] if ep.get("source_chapter") else [])
            if set(existing) & set(sources):
                raise ValueError("这些章节已被其他剧集使用，请刷新待用素材")
        target = next((e for e in episodes if e["number"] == number), None)
        if target is not None and self.has_script(number):
            raise ValueError("目标剧集已有剧本，不能自动覆盖")
        target = target or {"number": number}
        target.update({"title": plan["title"], "goal": plan["goal"], "hook": plan["hook"],
                       "status": "draft", "source_chapter": sources[0], "source_chapters": sources,
                       "source_hashes": {str(c["number"]): hashlib.sha256(c["text"].encode("utf-8")).hexdigest() for c in chapters},
                       "adaptation_plan": plan})
        if not any(e["number"] == number for e in episodes):
            episodes.append(target)
        path = self.script_path(number)
        previous = path.read_text(encoding="utf-8") if path.exists() else None
        self.save_script(number, script)
        try:
            self.save_episodes(episodes)
        except Exception:
            # 保留生成稿以便人工恢复，同时回滚主剧本，避免下次续跑出现未关联的占位集。
            project_io.write_md(path.with_suffix(".pending.md"), script)
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                project_io.write_md(path, previous)
            raise

    def adaptation_state(self) -> dict:
        return _read_json(self.root / "adaptation.json", {})

    def add_episode(self, title: str = "", source_chapter: int = 0) -> dict[str, Any]:
        episodes = self.load_episodes()
        number = max((int(e["number"]) for e in episodes), default=0) + 1
        ep = {
            "number": number, "title": title or f"第{number}集", "goal": "", "hook": "",
            "status": "draft", "source_chapter": int(source_chapter or 0),
        }
        episodes.append(ep)
        self.save_episodes(episodes)
        path = self.script_path(number)
        path.write_text(script_template(number, ep["title"]), encoding="utf-8")
        return ep

    def upsert_episode_for_chapter(
        self, chapter_number: int, chapter_title: str, script_text: str
    ) -> dict[str, Any]:
        """按源章节更新/新建一集，避免同一章重复一键生成时堆出多个「第N集」。"""
        episodes = self.load_episodes()
        title = (chapter_title or f"第{chapter_number}章").strip()
        target = None
        for ep in episodes:
            if int(ep.get("source_chapter") or 0) == int(chapter_number):
                if len(ep.get("source_chapters") or []) > 1:
                    raise ValueError("本章属于跨章合并剧集，请在剧本页编辑该集，避免逐章生成覆盖分集规划")
                target = ep
                break
        if target is None:
            # 兼容旧数据：标题完全相同也视为同一章
            for ep in episodes:
                if (ep.get("title") or "").strip() == title:
                    target = ep
                    break
        if target is None:
            # 首次一键生成：复用还空着的默认模板集，避免多出一个「第1集」
            for ep in episodes:
                if int(ep.get("source_chapter") or 0) != 0:
                    continue
                current = self.load_script(int(ep["number"])).strip()
                if not current or current == script_template(
                    int(ep["number"]), ep.get("title", "")
                ).strip():
                    target = ep
                    break
        if target is None:
            target = self.add_episode(title=title, source_chapter=chapter_number)
        else:
            target["title"] = title
            target["source_chapter"] = int(chapter_number)
            self.save_episodes(episodes)
        self.save_script(int(target["number"]), script_text)
        return target

    def target_episode_number(self, chapter_number: int, chapter_title: str) -> int:
        """与保存时使用同一套匹配规则，包含首次复用默认模板的情况。"""
        episodes = self.load_episodes()
        for ep in episodes:
            if int(ep.get("source_chapter") or 0) == chapter_number:
                return int(ep["number"])
        title = (chapter_title or f"第{chapter_number}章").strip()
        for ep in episodes:
            if ep["title"].strip() == title:
                return int(ep["number"])
        for ep in episodes:
            if not ep.get("source_chapter"):
                number = int(ep["number"])
                text = self.load_script(number).strip()
                if not text or text == script_template(number, ep["title"]).strip():
                    return number
        return max((int(e["number"]) for e in episodes), default=0) + 1

    def previous_episode_tail(self, number: int, limit: int = 800) -> str:
        previous = [e for e in self.load_episodes() if int(e["number"]) < number]
        return self.load_script(int(previous[-1]["number"]))[-limit:] if previous else ""

    def has_script(self, episode: int) -> bool:
        """空模板不进入 AI 分镜任务。"""
        text = self.load_script(episode).strip()
        ep = next((e for e in self.load_episodes() if e["number"] == episode), {})
        return bool(text and text != script_template(episode, ep.get("title", "")).strip())

    def episode_report(self, episode: int) -> dict[str, Any]:
        script = self.load_script(episode)
        data = self.load_storyboard(episode)
        shots = data["shots"]
        seconds = sum(s["duration_sec"] for s in shots)
        target = float(self.load_series().get("episode_seconds") or 90)
        issues = []
        if not self.has_script(episode):
            issues.append("剧本仍为空模板")
        scenes = len(re.findall(r"^##\s+", script, re.MULTILINE))
        if not scenes:
            issues.append("缺少场次标题")
        if not shots:
            issues.append("尚未制作分镜")
        elif abs(seconds - target) > target * 0.2:
            issues.append(f"分镜总时长 {seconds:g} 秒，目标 {target:g} 秒")
        if data["script_changed"]:
            issues.append("剧本已修改，请更新分镜")
        if any(s["duration_sec"] <= 0 for s in shots):
            issues.append("存在未填写时长的镜头")
        return {"scenes": scenes, "shots": len(shots), "seconds": seconds,
                "target_seconds": target, "issues": issues}

    def export_scripts(self, episode: int | None = None) -> Path:
        episodes = [e for e in self.load_episodes() if episode is None or e["number"] == episode]
        if not episodes:
            raise ValueError("没有可导出的剧集")
        name = episode_filename(episode) if episode is not None else "all_episodes"
        path = self.exports_dir / f"{name}_scripts.md"
        project_io.write_md(path, "\n\n---\n\n".join(self.load_script(e["number"]).strip() for e in episodes) + "\n")
        return path

    def delete_episode(self, number: int) -> None:
        episodes = [e for e in self.load_episodes() if int(e["number"]) != int(number)]
        self.save_episodes(episodes)
        for path in (self.script_path(number), self.storyboard_path(number)):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def import_chapter_as_episode(
        self, chapter_number: int, chapter_title: str, chapter_text: str
    ) -> dict[str, Any]:
        """把一章小说原文落成一集剧本素材（确定性兜底，AI 改编走 worker）。"""
        title = (chapter_title or f"第{chapter_number}章").strip()
        body = (chapter_text or "").strip()
        script = (
            f"# 第{chapter_number}集 {title}\n\n"
            f"> 来源：小说第{chapter_number}章。下面是原文素材，下一步改成场次/动作/对白。\n\n"
            f"## 第1场 {title} · 日/夜\n\n"
            "（把小说里的心理描写翻译成观众能看见的动作。）\n\n"
            f"{body}\n"
        )
        return self.upsert_episode_for_chapter(chapter_number, title, script)

    def script_path(self, episode: int) -> Path:
        return self.episodes_dir / f"{episode_filename(episode)}.md"

    def load_script(self, episode: int) -> str:
        path = self.script_path(episode)
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("读取剧本失败 %s: %s", path, e)
            return ""

    def save_script(self, episode: int, text: str) -> None:
        path = self.script_path(episode)
        if path.exists():
            previous = path.read_text(encoding="utf-8")
            if previous != (text or ""):
                from .drama_production import preserve
                preserve(path)
                project_io.write_md(path.with_suffix(".bak.md"), previous)
        project_io.write_md(path, text or "")

    def script_hash(self, episode: int) -> str:
        """当前剧本文本 hash；用于判断分镜是否过期。"""
        try:
            text = self.load_script(episode)
        except Exception:
            return ""
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

    def update_episode_meta(self, number: int, **fields) -> None:
        episodes = self.load_episodes()
        for ep in episodes:
            if int(ep["number"]) == int(number):
                for key in ("title", "goal", "hook", "status"):
                    if key in fields:
                        ep[key] = fields[key]
                break
        self.save_episodes(episodes)

    # ── 分镜 ──

    def storyboard_path(self, episode: int) -> Path:
        return self.storyboard_dir / f"{episode_filename(episode)}.json"

    def load_storyboard(self, episode: int) -> dict[str, Any]:
        episodes = {int(e["number"]): e for e in self.load_episodes()}
        ep = episodes.get(int(episode), {"number": episode, "title": f"第{episode}集"})
        series = self.load_series()
        data = _read_json(self.storyboard_path(episode), {})
        if not isinstance(data, dict):
            data = {}
        shots = data.get("shots")
        shots = shots if isinstance(shots, list) else []
        normalized = [normalize_shot(s, episode, i) for i, s in enumerate(shots, start=1)]
        source_hash = str(data.get("source_script_hash") or "")
        current_hash = self.script_hash(episode)
        return {
            "schema_version": SCHEMA_VERSION,
            "episode": int(episode),
            "title": str(data.get("title") or ep.get("title") or f"第{episode}集"),
            "aspect": str(data.get("aspect") or series.get("aspect") or "9:16"),
            "duration_sec": data.get("duration_sec", series.get("episode_seconds", 90)),
            "source_script_hash": source_hash,
            "script_changed": bool(source_hash and current_hash and source_hash != current_hash),
            "updated_at": str(data.get("updated_at") or ""),
            "shots": normalized,
        }

    def save_storyboard(
        self, episode: int, data: dict[str, Any], update_source: bool = False
    ) -> dict[str, Any]:
        current = self.load_storyboard(episode)
        shots = data.get("shots") if isinstance(data, dict) else None
        current["shots"] = [
            normalize_shot(s, episode, i)
            for i, s in enumerate(shots if isinstance(shots, list) else [], start=1)
        ]
        for key in ("title", "aspect", "duration_sec"):
            if isinstance(data, dict) and data.get(key) not in (None, ""):
                current[key] = data[key]
        # 只有“按剧本生成”才刷新源 hash；手工改表格不会把过期标记洗掉
        if update_source:
            current["source_script_hash"] = self.script_hash(episode)
        current["schema_version"] = SCHEMA_VERSION
        current["updated_at"] = _now()
        current.pop("script_changed", None)
        path = self.storyboard_path(episode)
        # 重新生成分镜前留一份旧版（文章：新版本存新文件，不覆盖不删除）；
        # 手工改表格不落历史，否则每敲一下都存一份。
        if update_source and path.exists():
            history = self.storyboard_dir / "versions"
            history.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target = history / f"{path.stem}_{stamp}.json"
            n = 2
            while target.exists():
                target = history / f"{path.stem}_{stamp}_{n}.json"
                n += 1
            from .drama_records import index_version
            index_version(self, file=f"storyboard/{path.name}", owner=f"episode:{episode}",
                          replaced_by=target.name, note="重新生成分镜")
            path.replace(target)
        _write_json(path, current)
        return current

    # ── 分镜 → 资产结构化引用 ──
    # 分镜的角色/地点列是给人看的名字文本；引用表把「这一镜用哪些资产」落成
    # shot_id → asset_id 的结构化关系，改名不断链，视频编排和追溯都吃这张表。

    def shot_refs_path(self, episode: int) -> Path:
        return self.storyboard_dir / f"{episode_filename(episode)}_refs.json"

    def save_shot_refs(self, episode: int, refs: dict[str, dict[str, Any]]) -> None:
        _write_json(self.shot_refs_path(episode), {
            "schema_version": SCHEMA_VERSION,
            "episode": int(episode),
            "refs": refs,
            "updated_at": _now(),
        })

    def load_shot_refs(self, episode: int) -> dict[str, dict[str, Any]]:
        data = _read_json(self.shot_refs_path(episode), {})
        refs = data.get("refs") if isinstance(data, dict) else None
        if not isinstance(refs, dict):
            return {}
        cleaned: dict[str, dict[str, Any]] = {}
        for shot_id, entry in refs.items():
            if not isinstance(entry, dict):
                continue
            cleaned[str(shot_id)] = {
                "characters": [str(v) for v in entry.get("characters") or [] if str(v)],
                "location": str(entry.get("location") or ""),
                "props": [str(v) for v in entry.get("props") or [] if str(v)],
            }
        return cleaned

    def build_shot_refs(self, episode: int) -> dict[str, dict[str, Any]]:
        """按当前分镜和资产表重算引用（名字匹配）：人物/地点列精确+包含匹配，
        道具按名字出现在动作/台词/运动文本里判（分镜没有道具列）。"""
        assets = self.load_assets()
        by_kind: dict[str, dict[str, str]] = {}
        for asset in assets:
            name = _normalize_asset_name(asset.get("name") or "")
            if name and asset.get("id"):
                by_kind.setdefault(str(asset.get("kind") or ""), {})[name] = str(asset["id"])

        def match(kind: str, name: str) -> str:
            clean = _normalize_asset_name(name)
            if not clean:
                return ""
            ids = by_kind.get(kind, {})
            if clean in ids:
                return ids[clean]
            for alias, asset_id in ids.items():
                if alias and (alias in clean or clean in alias):
                    return asset_id
            return ""

        refs: dict[str, dict[str, Any]] = {}
        for shot in self.load_storyboard(episode)["shots"]:
            sid = str(shot.get("shot_id") or "")
            if not sid:
                continue
            characters: list[str] = []
            for name in re.split(r"[、,，/／+＋\s]+", str(shot.get("characters") or "")):
                asset_id = match("character", name)
                if asset_id and asset_id not in characters:
                    characters.append(asset_id)
            text = " ".join(str(shot.get(key) or "") for key in ("action", "dialogue", "motion"))
            props = []
            for asset in assets:
                if asset.get("kind") != "prop" or not asset.get("id"):
                    continue
                name = str(asset.get("name") or "").strip()
                # 单字名字容易误命中（"剑"出现在"剑法"里也算合理），仍放行；
                # 两字以上才要求整词出现由调用方裁夺，这里保守按包含匹配。
                if name and name in text and asset["id"] not in props:
                    props.append(str(asset["id"]))
            refs[sid] = {
                "characters": characters,
                "location": match("location", str(shot.get("location") or "")),
                "props": props,
            }
        return refs

    def refresh_shot_assets(self, episode: int) -> dict[str, dict[str, Any]]:
        refs = self.build_shot_refs(episode)
        self.save_shot_refs(episode, refs)
        return refs

    def resolve_shot_assets(self, episode: int, shot: dict[str, Any]) -> dict[str, Any]:
        """shot → 资产对象；优先结构化引用表，没有引用表（老项目）退回名字匹配。"""
        assets = self.load_assets()
        by_id = {str(a.get("id") or ""): a for a in assets}
        sid = str(shot.get("shot_id") or "")
        refs = self.load_shot_refs(episode).get(sid)
        if refs is None:
            refs = self.build_shot_refs(episode).get(sid) or {
                "characters": [], "location": "", "props": []}
        return {
            "characters": [by_id[i] for i in refs.get("characters") or [] if i in by_id],
            "location": by_id.get(str(refs.get("location") or "")),
            "props": [by_id[i] for i in refs.get("props") or [] if i in by_id],
        }

    # ── 分镜提交前体检（本地）──

    # 文章里"AI 视频固定弱点"的文字版检查：写进分镜就等于提前抽一次坏卡
    _AI_WEAK_SPOTS: dict[str, str] = {
        "镜子": "镜面/倒影是最容易穿帮的拍法，能改就改",
        "倒影": "倒影容易左右反、数量错，能避就避",
        "水面": "水面倒影容易穿帮，能避就避",
        "文字": "画面文字会被模型写错，尽量改成实物或台词",
        "字幕": "字幕不要交给生成模型，后期再加",
        "交叉": "人物交叉走位最容易穿帮，改成前后错开",
        "换光": "一镜内换光很难接，改成一镜一光",
        "不对称": "左右不对称的特征模型容易画反，能避就避",
        "手拿": "手拿小东西容易糊成一片，能放桌上就放桌上",
        "拿着": "手拿小东西容易糊成一片，能放桌上就放桌上",
        "攥着": "手攥小物件容易糊，改成放在明处",
        "捏着": "手捏小物件容易糊，改成放在明处",
    }
    _SHOT_DIRECTION_WORDS = ("画面左", "画面右", "朝左", "朝右", "向左", "向右", "左边", "右边")
    _SHOT_MOVE_WORDS = (
        "走", "跑", "冲", "退", "转身", "回头", "抬手", "低头", "抬头",
        "递", "推", "拉", "扑", "落", "飘", "晃", "摇", "眨", "握",
    )
    _SHOT_SIMULTANEOUS = ("同时", "一齐", "众人", "同一秒")

    def audit_storyboard(self, shots: list[dict[str, Any]]) -> list[dict[str, str]]:
        """分镜体检：能在文字里发现的错，不留到生图和视频上。

        规则全部来自实际操作踩过的坑：景别连排、平视正面超两次、
        人物走动没写画面左右、多人同时动作、静态画面像一张图、
        以及镜子/倒影/文字这类模型必翻车的画面。
        返回 ``[{"shot_id": ..., "issue": ...}]``，只告警不拦保存。
        """
        issues: list[dict[str, str]] = []
        size_run, prev_size = 0, ""
        front_count = 0
        for idx, shot in enumerate(shots, start=1):
            sid = str(shot.get("shot_id") or f"第{idx}镜")
            size = str(shot.get("shot_size") or "").strip()
            camera = str(shot.get("camera") or "").strip()
            action = str(shot.get("action") or "").strip()
            transition = str(shot.get("transition") or "").strip()
            try:
                duration = float(shot.get("duration_sec") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            if not size:
                issues.append({"shot_id": sid, "issue": "没写景别"})
            size_run = size_run + 1 if size and size == prev_size else 1
            prev_size = size or prev_size
            if size_run == 3:
                issues.append({"shot_id": sid, "issue": "连续三镜同一景别，画面会平，换全身/半身/特写"})
            if "平视" in camera or "正面" in camera:
                front_count += 1
                if front_count > 2:
                    issues.append({"shot_id": sid, "issue": "平视正面超过两次，改用俯拍/仰拍/背后/过肩/透过东西拍"})
            if duration <= 0:
                issues.append({"shot_id": sid, "issue": "缺镜头时长"})
            elif duration > 5:
                issues.append({"shot_id": sid, "issue": f"{duration:.1f} 秒偏长，动作容易乱，拆成两镜或后期变速"})
            if not action:
                issues.append({"shot_id": sid, "issue": "没写画面/动作，模型只能自己编"})
            else:
                blocking = str(shot.get("blocking") or "").strip()
                emotion = str(shot.get("emotion") or "").strip()
                motion = str(shot.get("motion") or "").strip()
                has_move = any(w in action for w in self._SHOT_MOVE_WORDS)
                has_direction = any(
                    w in action or w in blocking
                    for w in self._SHOT_DIRECTION_WORDS
                )
                if has_move and not has_direction:
                    issues.append({"shot_id": sid, "issue": "人物有走动/递物但没写画面左/右，容易走反方向"})
                if any(w in action for w in self._SHOT_SIMULTANEOUS):
                    issues.append({"shot_id": sid, "issue": "多人同时动作不真实，写清谁先谁后"})
                if "固定" in camera and not has_move:
                    issues.append({"shot_id": sid, "issue": "固定机位且画面里没有动作，会像一张静态图"})
                if "俯拍" in camera and str(shot.get("characters") or "").strip():
                    issues.append({"shot_id": sid, "issue": "俯拍容易看不到脸，重要情绪镜头换平视/过肩"})
                people = [
                    part for part in re.split(r"[、,，/／+＋\s]+", str(shot.get("characters") or ""))
                    if part
                ]
                if len(people) > 4:
                    issues.append({"shot_id": sid, "issue": f"同框 {len(people)} 个角色，配角太多容易穿帮，拆镜或虚化"})
                for word, hint in self._AI_WEAK_SPOTS.items():
                    if word in action:
                        issues.append({"shot_id": sid, "issue": f"画面里有「{word}」：{hint}"})
                # 文章 2.3：每镜写清「到底什么在动」，摄影机/焦点/光线/前景/小动作至少一样
                camera_moves = any(
                    w in camera for w in ("推", "拉", "摇", "跟", "手持", "移", "升降")
                )
                front_words = any(w in action for w in ("飘", "晃", "闪", "光", "风", "影"))
                if not motion and not has_move and not camera_moves and not front_words:
                    issues.append({
                        "shot_id": sid,
                        "issue": "没写什么在动（摄影机/焦点/光线/前景遮挡/人物小动作，至少一样）",
                    })
                if people and not emotion:
                    issues.append({"shot_id": sid, "issue": "没写靠什么表情推进，情绪镜头容易空"})
                if shot.get("start_sec") in (None, ""):
                    issues.append({"shot_id": sid, "issue": "没写片中时间"})
                gen = shot.get("gen_seconds")
                if gen not in (None, "", 0):
                    try:
                        gap = float(gen) - duration
                    except (TypeError, ValueError):
                        gap = 0.0
                    if 0 < gap < 0.5:
                        issues.append({
                            "shot_id": sid,
                            "issue": "生成秒数没给后期留 0.5 秒余量（改成时长向上取整再加 0.5）",
                        })
            if not transition:
                issues.append({"shot_id": sid, "issue": "没写怎么转到下一镜"})
        return issues

    def shots_from_script(self, script: str, episode: int) -> list[dict[str, Any]]:
        """把剧本里 ``## 场次`` 标题拆成一镜一场的草稿。

        只做确定性骨架：地点/时间/人物/动作/台词粗提取，不替代 AI 分镜。
        """
        if not script:
            return []
        lines = script.replace("\r\n", "\n").splitlines()
        headings: list[tuple[int, str]] = []
        for i, line in enumerate(lines):
            text = line.strip()
            if not text:
                continue
            if re.match(r"^#{2,4}\s+\S", text):
                headings.append((i, re.sub(r"^#{2,4}\s+", "", text)))
            elif re.match(r"^第\s*[0-9一二三四五六七八九十百零两]+\s*场", text):
                headings.append((i, text))
            elif re.match(r"^场\s*[0-9一二三四五六七八九十百零两]+\s*[：:]", text):
                headings.append((i, text))
        if not headings:
            return []

        shots: list[dict[str, Any]] = []
        for idx, (line_no, heading) in enumerate(headings[:100], start=1):
            end = headings[idx][0] if idx < len(headings) else len(lines)
            body = [ln.strip() for ln in lines[line_no + 1:end] if ln.strip()]
            clean_heading = re.sub(
                r"^第\s*[0-9一二三四五六七八九十百零两]+\s*场\s*[：:、.]?\s*", "", heading
            )
            clean_heading = re.sub(
                r"^场\s*[0-9一二三四五六七八九十百零两]+\s*[：:、.]?\s*", "", clean_heading
            ).strip()
            location, time = clean_heading, ""
            parts = re.split(r"\s*[·|｜]\s*", clean_heading, maxsplit=1)
            if len(parts) == 2:
                location, time = parts[0], parts[1]
            characters: list[str] = []
            action_parts: list[str] = []
            dialogue_parts: list[str] = []
            for ln in body:
                m = re.match(r"^([^：:]{1,12})[：:]\s*(.+)$", ln)
                if m and not m.group(1).startswith(("画面", "动作", "旁白", "字幕", "备注")):
                    speaker, line_text = m.group(1).strip(), m.group(2).strip()
                    if speaker not in characters:
                        characters.append(speaker)
                    dialogue_parts.append(f"{speaker}：{line_text}")
                else:
                    action_parts.append(ln)
            shot = new_shot(episode, idx)
            shot.update({
                "scene_id": f"SC{idx:02d}",
                "location": location.strip(),
                "time": time.strip(),
                "characters": "、".join(characters),
                "action": " ".join(action_parts)[:300],
                "dialogue": " ".join(dialogue_parts)[:300],
                "status": "draft",
            })
            shots.append(shot)
        return shots

    # ── 首尾帧 / 视频 ──

    def keyframes_path(self, episode: int) -> Path:
        return self.keyframes_dir / f"{episode_filename(episode)}.json"

    def load_keyframes(self, episode: int) -> dict[str, Any]:
        data = _read_json(self.keyframes_path(episode), {})
        raw = data.get("shots") if isinstance(data, dict) else None
        shots: list[dict[str, str]] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            shots.append({
                "shot_id": str(item.get("shot_id") or ""),
                "first_frame": str(item.get("first_frame") or ""),
                "last_frame": str(item.get("last_frame") or ""),
                "motion": str(item.get("motion") or ""),
            })
        return {
            "episode": int(episode),
            "shots": shots,
            "updated_at": str(data.get("updated_at") or "") if isinstance(data, dict) else "",
        }

    def save_keyframes(
        self, episode: int, briefs: list[dict[str, Any]]
    ) -> dict[str, Any]:
        payload = {
            "episode": int(episode),
            "shots": [
                {
                    "shot_id": str(item.get("shot_id") or ""),
                    "first_frame": str(item.get("first_frame") or ""),
                    "last_frame": str(item.get("last_frame") or ""),
                    "motion": str(item.get("motion") or ""),
                }
                for item in briefs if isinstance(item, dict)
            ],
            "updated_at": _now(),
        }
        from .drama_production import preserve
        preserve(self.keyframes_path(episode))
        _write_json(self.keyframes_path(episode), payload)
        return payload

    def keyframe_image_path(self, episode: int, shot_id: str, which: str) -> Path:
        """首尾帧图片路径（绝对）；which 为 first / last。"""
        safe = re.sub(r"[^\w\-]+", "_", str(shot_id or "shot")) or "shot"
        return self.keyframes_dir / episode_filename(episode) / f"{safe}_{which}.png"

    def save_keyframe_image(
        self, episode: int, shot_id: str, which: str, data: bytes
    ) -> str:
        path = self.keyframe_image_path(episode, shot_id, which)
        path.parent.mkdir(parents=True, exist_ok=True)
        from .drama_production import preserve
        preserve(path)
        path.write_bytes(data)
        return path.relative_to(self.root).as_posix()

    def episode_clip_dir(self, episode: int) -> Path:
        folder = self.videos_dir / episode_filename(episode)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def episode_video_path(self, episode: int) -> Path:
        return self.videos_dir / f"{episode_filename(episode)}.mp4"

    def save_clip(self, episode: int, shot_id: str, data: bytes) -> Path:
        safe = re.sub(r"[^\w\-]+", "_", str(shot_id or "shot")) or "shot"
        path = self.episode_clip_dir(episode) / f"{safe}.mp4"
        from .drama_production import preserve
        preserve(path)
        path.write_bytes(data)
        return path

    # ── 资产 ──

    def load_assets(self) -> list[dict[str, Any]]:
        data = _read_json(self.assets_dir / ASSETS_FILE, {})
        assets = data.get("assets") if isinstance(data, dict) else None
        if not isinstance(assets, list):
            return []
        cleaned: list[dict[str, Any]] = []
        for i, asset in enumerate(assets, start=1):
            if not isinstance(asset, dict):
                continue
            notes = str(asset.get("notes") or "")
            source = str(asset.get("source") or "")
            if not source:
                # 老数据没有 source：按 notes 标记回推
                source = _ASSET_SOURCE_ALIASES.get(notes, "manual")
            raw_views = asset.get("makeup_views")
            views = {view: raw_views[view] for view in MAKEUP_VIEWS
                     if isinstance(raw_views, dict) and isinstance(raw_views.get(view), str)
                     and raw_views[view]}
            raw_fb = asset.get("fullbody_views")
            fb_views = {view: raw_fb[view] for view in FULLBODY_VIEWS
                        if isinstance(raw_fb, dict) and isinstance(raw_fb.get(view), str)
                        and raw_fb[view]}
            raw_prop = asset.get("prop_views")
            prop_views = {view: raw_prop[view] for view in PROP_VIEWS
                          if isinstance(raw_prop, dict) and isinstance(raw_prop.get(view), str)
                          and raw_prop[view]}
            cleaned.append({
                "kind": str(asset.get("kind") or "character"),
                "id": str(asset.get("id") or f"asset_{i:03d}"),
                "name": str(asset.get("name") or ""),
                "source": source,
                "prompt": str(asset.get("prompt") or ""),
                "ref": str(asset.get("ref") or ""),
                "status": str(asset.get("status") or "draft"),
                "notes": notes,
                # 与小说正典的对应关系：人物/地点/道具的名称或角色 id
                "entity": str(asset.get("entity") or ""),
                # 已生成图片的相对路径（assets/generated/...）
                "image": str(asset.get("image") or ""),
                "reference_image": str(asset.get("reference_image") or ""),
                **({"makeup_views": views} if views else {}),
                **({"fullbody_views": fb_views} if fb_views else {}),
                **({"prop_views": prop_views} if prop_views else {}),
                **({"scene_panorama": str(asset["scene_panorama"])} if asset.get("scene_panorama") else {}),
                # 落盘时读到的真实像素与画幅告警（生成服务不一定按请求出图）
                "image_size": str(asset.get("image_size") or ""),
                "aspect_warning": str(asset.get("aspect_warning") or ""),
                # 公用素材库复用来源（跨项目共享库，见 core/drama_library.py）
                "library_id": str(asset.get("library_id") or ""),
                "library_name": str(asset.get("library_name") or ""),
            })
        return cleaned

    def save_assets(self, assets: list[dict[str, Any]]) -> None:
        _write_json(self.assets_dir / ASSETS_FILE, {"assets": assets, "updated_at": _now()})

    def makeup_paths(self, asset: dict[str, Any]) -> dict[str, Path]:
        if asset.get("kind") != "character":
            return {}
        views = asset.get("makeup_views") or {}
        return {view: path for view in MAKEUP_VIEWS if views.get(view)
                and (path := self.assets_dir / views[view]).is_file()}

    def clear_makeup_view(self, asset_id: str, view: str) -> None:
        assets = self.load_assets()
        asset = next(a for a in assets if a["id"] == asset_id and a["kind"] == "character")
        views = asset.get("makeup_views") or {}
        if view == "front":
            if asset.get("ref") == views.get("front"):
                asset["ref"] = ""
            if asset.get("image") == views.get("front"):
                asset["image"] = ""
                asset["image_size"] = ""
            asset.pop("makeup_views", None)
        else:
            views.pop(view, None)
        asset["status"] = "draft"
        self.save_assets(assets)

    def makeup_prompt(self, asset: dict[str, Any], view: str) -> tuple[str, str]:
        positive, negative = self.asset_prompt(asset)
        positive = positive.replace("单人全身正面站姿", "单人头肩特写")
        positive = positive.removesuffix(ASSET_PROMPT_PLANS["character"]["suffix"]).rstrip("，")
        positive = positive.replace(ASSET_PROMPT_PLANS["character"]["fallback_desc"], "妆发造型参考")
        label, angle = MAKEUP_VIEWS[view]
        notes = str(asset.get("notes") or "").strip()
        continuity = ""
        if notes and notes not in positive and not notes.startswith(("从分镜", "从小说导入")):
            continuity = f"\n妆发连续性记录：{notes}"
        details = {
            "front": "展示五官与妆容，发饰避开眉眼，面部放松。没有外观参考时按人物文字设定生成。",
            "left": "以正面照为同一人物基准，呈现左侧脸、可见的发饰和衣领，远侧自然遮挡。",
            "right": "以正面照为同一人物基准，呈现右侧脸、可见的发饰和衣领，远侧自然遮挡。",
            "back": "参照正面照的发型、发饰和衣领，呈现后脑、后颈与可见肩线，面部不可见。",
        }
        return (
            f"同一人物·{label}视角。{positive}{continuity}\n"
            f"单人头肩特写，画幅 3:4 竖构图，{angle}。{details[view]}"
            "头顶留边，采用柔和均匀的日光和纯色背景。",
            f"{negative}，全身照，多人，多视角拼图，裁切头顶",
        )

    def save_makeup_view(self, asset_id: str, view: str, data: bytes) -> Path:
        """逐张登记，续跑可复用已完成的角度；旧图路径保留。"""
        from .drama_production import stamp

        label = MAKEUP_VIEWS[view][0]
        assets = self.load_assets()
        asset = next(a for a in assets if a["id"] == asset_id and a["kind"] == "character")
        safe = re.sub(r"[^\w\-]+", "_", asset_id)[:60] or "character"
        path = self.assets_dir / "generated" / "makeup" / f"{safe}_{view}_{stamp()}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        size = image_dimensions(data)
        if not size or not aspect_matches(*size, asset_canvas("character")):
            actual = f"{size[0]}×{size[1]}" if size else "尺寸未知"
            raise ValueError(f"{asset['name']}的{label}定妆照画幅应为 3:4，实际为 {actual}。原图已保存：{path}")
        views = asset.setdefault("makeup_views", {})
        views[view] = path.relative_to(self.assets_dir).as_posix()
        if view == "front":
            # 新正面照作为其余角度的参考，重新开始一组。
            asset["makeup_views"] = {"front": views["front"]}
            asset["image"] = asset["ref"] = views["front"]
            asset["image_size"] = f"{size[0]}x{size[1]}"
            asset["aspect_warning"] = ""
        asset["status"] = "ready" if len(self.makeup_paths(asset)) == len(MAKEUP_VIEWS) else "draft"
        self.save_assets(assets)
        return path

    # ── 人物全身三视图（第二层参考：全身镜头用，头肩特写管脸部）──

    def fullbody_paths(self, asset: dict[str, Any]) -> dict[str, Path]:
        if asset.get("kind") != "character":
            return {}
        views = asset.get("fullbody_views") or {}
        return {view: path for view in FULLBODY_VIEWS if views.get(view)
                and (path := self.assets_dir / views[view]).is_file()}

    def fullbody_prompt(self, asset: dict[str, Any], view: str) -> tuple[str, str]:
        """全身三视图提示词：以头肩定妆照（或已有全身正面）为外观基准。"""
        positive, negative = self.asset_prompt(asset)
        plan = ASSET_PROMPT_PLANS["character"]
        positive = positive.replace(plan["suffix"], "").rstrip("，")
        positive = positive.replace(plan["fallback_desc"], "人物造型参考")
        # 手填词里可能写了头肩特写，全身图要换掉
        positive = positive.replace("单人头肩特写", "单人全身").replace("头肩特写", "全身")
        label, angle = FULLBODY_VIEWS[view]
        notes = str(asset.get("notes") or "").strip()
        continuity = ""
        if notes and notes not in positive and not notes.startswith(("从分镜", "从小说导入")):
            continuity = f"\n外观连续性记录：{notes}"
        details = {
            "front": "单人全身正面站姿，头顶到鞋底完整入画，双臂自然下垂，"
                     "展示发型、服装、身形比例和随身固定物品。",
            "side": "以全身正面照为同一人物基准，相机位于人物左侧 90 度，"
                    "全身侧面站姿，完整入画，远侧自然遮挡。",
            "back": "参照全身正面照的发型和服装，人物正后方全身站姿，"
                    "展示背部和后脑，面部不可见。",
        }
        return (
            f"同一人物·{label}视角。{positive}{continuity}\n"
            f"单人全身立像，画幅 3:4 竖构图，{angle}。{details[view]}"
            "头顶和脚底都留边，柔和均匀的日光，纯色背景。",
            f"{negative}，头肩特写，裁切头顶，裁切脚底，坐姿，多人，多视角拼图",
        )

    def save_fullbody_view(self, asset_id: str, view: str, data: bytes) -> Path:
        """逐张登记全身视图；旧图路径保留，画幅不对直接报错（图已保存）。"""
        from .drama_production import stamp
        from .drama_records import index_version

        label = FULLBODY_VIEWS[view][0]
        assets = self.load_assets()
        asset = next(a for a in assets if a["id"] == asset_id and a["kind"] == "character")
        safe = re.sub(r"[^\w\-]+", "_", asset_id)[:60] or "character"
        path = self.assets_dir / "generated" / "fullbody" / f"{safe}_fb_{view}_{stamp()}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        size = image_dimensions(data)
        if not size or not aspect_matches(*size, asset_canvas("character")):
            actual = f"{size[0]}×{size[1]}" if size else "尺寸未知"
            raise ValueError(f"{asset['name']}的{label}全身照画幅应为 3:4，实际为 {actual}。原图已保存：{path}")
        views = asset.setdefault("fullbody_views", {})
        old = views.get(view)
        views[view] = path.relative_to(self.assets_dir).as_posix()
        if old:
            index_version(self, file=f"assets/{old}", owner=asset_id,
                          replaced_by=path.name, note=f"全身{label}重生成")
        # 状态仍由头肩定妆照管理：全身视图是第二层参考，不单独改变资产状态
        self.save_assets(assets)
        return path

    def clear_fullbody_view(self, asset_id: str, view: str) -> None:
        assets = self.load_assets()
        asset = next(a for a in assets if a["id"] == asset_id and a["kind"] == "character")
        views = asset.get("fullbody_views") or {}
        views.pop(view, None)
        if views:
            asset["fullbody_views"] = views
        else:
            asset.pop("fullbody_views", None)
        self.save_assets(assets)

    # ── 道具三视图 ──

    def prop_paths(self, asset: dict[str, Any]) -> dict[str, Path]:
        if asset.get("kind") != "prop":
            return {}
        views = asset.get("prop_views") or {}
        return {view: path for view in PROP_VIEWS if views.get(view)
                and (path := self.assets_dir / views[view]).is_file()}

    def prop_prompt(self, asset: dict[str, Any], view: str) -> tuple[str, str]:
        """道具三视图提示词：后两角度以正面照为同一物品基准。"""
        positive, negative = self.asset_prompt(asset)
        plan = ASSET_PROMPT_PLANS["prop"]
        positive = positive.replace(plan["suffix"], "").rstrip("，")
        positive = positive.replace(plan["fallback_desc"], "道具造型参考")
        label, angle = PROP_VIEWS[view]
        notes = str(asset.get("notes") or "").strip()
        continuity = ""
        if notes and notes not in positive and not notes.startswith(("从分镜", "从小说导入")):
            continuity = f"\n道具连续性记录：{notes}"
        details = {
            "front": "单件物品正面特写，物品居中，展示正面材质、颜色与造型细节。",
            "side": "以正面照为同一物品基准，呈现左侧面轮廓、厚度和侧面细节，远侧自然遮挡。",
            "back": "参照正面照的造型和材质，呈现物品背面细节，正面不可见。",
        }
        return (
            f"同一物品·{label}视角。{positive}{continuity}\n"
            f"单件物品特写，画幅 1:1 方构图，{angle}。{details[view]}"
            "无人物，柔和均匀的日光，纯色背景，写实质感，高清细节。",
            f"{negative}，多格拼图，重复物品，多人，多视角拼图",
        )

    def save_prop_view(self, asset_id: str, view: str, data: bytes) -> Path:
        """逐张登记道具视图；front 是其余角度的参考基准，画幅不对报错。"""
        from .drama_production import stamp
        from .drama_records import index_version

        label = PROP_VIEWS[view][0]
        assets = self.load_assets()
        asset = next(a for a in assets if a["id"] == asset_id and a["kind"] == "prop")
        safe = re.sub(r"[^\w\-]+", "_", asset_id)[:60] or "prop"
        path = self.assets_dir / "generated" / "props" / f"{safe}_{view}_{stamp()}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        size = image_dimensions(data)
        if not size or not aspect_matches(*size, asset_canvas("prop")):
            actual = f"{size[0]}×{size[1]}" if size else "尺寸未知"
            raise ValueError(f"{asset['name']}的{label}三视图画幅应为 1:1，实际为 {actual}。原图已保存：{path}")
        views = asset.setdefault("prop_views", {})
        old = views.get(view)
        views[view] = path.relative_to(self.assets_dir).as_posix()
        if old:
            index_version(self, file=f"assets/{old}", owner=asset_id,
                          replaced_by=path.name, note=f"道具{label}重生成")
        if view == "front":
            # 新正面照作为其余角度的参考，重新开始一组。
            asset["prop_views"] = {"front": views["front"]}
            asset["image"] = asset["ref"] = views["front"]
            asset["image_size"] = f"{size[0]}x{size[1]}"
            asset["aspect_warning"] = ""
        asset["status"] = "ready" if len(self.prop_paths(asset)) == len(PROP_VIEWS) else "draft"
        self.save_assets(assets)
        return path

    def clear_prop_view(self, asset_id: str, view: str) -> None:
        assets = self.load_assets()
        asset = next(a for a in assets if a["id"] == asset_id and a["kind"] == "prop")
        views = asset.get("prop_views") or {}
        if view == "front":
            if asset.get("ref") == views.get("front"):
                asset["ref"] = ""
            if asset.get("image") == views.get("front"):
                asset["image"] = ""
                asset["image_size"] = ""
            asset.pop("prop_views", None)
        else:
            views.pop(view, None)
        asset["status"] = "draft"
        self.save_assets(assets)

    def scene_prompt(self, asset: dict[str, Any]) -> tuple[str, str]:
        positive, negative = self.asset_prompt(asset)
        plan = ASSET_PROMPT_PLANS["location"]
        legacy_suffix = (
            "短剧场景参考图，画幅 16:9 横构图（宽:高=16:9），全景空镜，"
            "画面中只有建筑结构、室内陈设和自然光，无人物，写实电影感，环境细节丰富，高清细节"
        )
        for suffix in (SCENE_PANORAMA_PROMPT, plan["suffix"], legacy_suffix):
            positive = positive.replace(suffix, "")
        positive = positive.replace(plan["ratio_phrase"], "")
        positive = _DECLARED_RATIO_RE.sub("", positive)
        positive = positive.strip("， ")
        notes = str(asset.get("notes") or "").strip()
        if notes and notes not in positive and not notes.startswith(("从分镜", "从小说导入")):
            positive += f"\n空间布局与视觉锚点记录：{notes}"
        return f"{positive.strip()}\n{SCENE_PANORAMA_PROMPT}", f"{negative}，多格拼图，重复布景，鱼眼圆形边框"

    def scene_paths(self, asset: dict[str, Any]) -> dict[str, Path]:
        source = asset.get("scene_panorama")
        if asset.get("kind") != "location" or not source:
            return {}
        panorama = self.assets_dir / source
        if not panorama.is_file():
            return {}
        paths = {"panorama": panorama}
        for view in ("forward", "reverse"):
            path = panorama.with_name(f"{panorama.stem}_{view}.png")
            if path.is_file():
                paths[view] = path
        return paths

    def clear_scene_panorama(self, asset_id: str) -> None:
        assets = self.load_assets()
        asset = next(a for a in assets if a["id"] == asset_id and a["kind"] == "location")
        asset.pop("scene_panorama", None)
        asset["status"] = "draft"
        self.save_assets(assets)

    def restore_scene_views(self, asset: dict[str, Any]) -> dict[str, Path]:
        paths = self.scene_paths(asset)
        if paths and len(paths) < len(SCENE_VIEWS):
            source = paths["panorama"]
            outputs = {view: source.with_name(f"{source.stem}_{view}.png")
                       for view in ("forward", "reverse")}
            project_scene_views(source, outputs)
            return {"panorama": source, **outputs}
        return paths

    def save_scene_panorama(self, asset_id: str, data: bytes) -> Path:
        from .drama_production import stamp

        assets = self.load_assets()
        asset = next(a for a in assets if a["id"] == asset_id and a["kind"] == "location")
        safe = re.sub(r"[^\w\-]+", "_", asset_id)[:60] or "scene"
        path = self.assets_dir / "generated" / "scenes" / f"{safe}_{stamp()}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        size = image_dimensions(data)
        if not size or min(size) <= 0 or not _ratio_equal(size, SCENE_PANORAMA_SIZE):
            actual = f"{size[0]}×{size[1]}" if size else "尺寸未知"
            raise ValueError(f"{asset['name']}的360°环景需要 2:1 画幅，实际为 {actual}。原图已保存：{path}")
        views = {view: path.with_name(f"{path.stem}_{view}.png") for view in ("forward", "reverse")}
        project_scene_views(path, views)
        asset["scene_panorama"] = path.relative_to(self.assets_dir).as_posix()
        asset["image"] = asset["ref"] = views["forward"].relative_to(self.assets_dir).as_posix()
        asset["image_size"] = "1280x720"
        asset["aspect_warning"] = ""
        asset["status"] = "ready"
        self.save_assets(assets)
        return path

    def extract_assets_from_storyboard(self, episode: int) -> int:
        """从分镜的角色/地点列提取资产占位，返回新增数量。

        能对上小说正典的（名字或别名）标 `source=canon` 并回填 entity；
        对不上的（龙套、临时场景）标 `source=storyboard`，属于短剧原创资产。
        """
        assets = self.load_assets()
        existing = {
            (a["kind"], _normalize_asset_name(a["name"]))
            for a in assets if a["name"].strip()
        }
        added = 0

        def add_asset(kind: str, name: str) -> None:
            nonlocal added
            name = name.strip()
            norm = _normalize_asset_name(name)
            if not norm or (kind, norm) in existing:
                return
            matched = self.match_canon_entity(kind, name)
            assets.append({
                "kind": kind,
                "id": _unique_asset_id(assets, kind, name),
                "name": name,
                "source": "canon" if matched else "storyboard",
                "prompt": "",
                "ref": "",
                "status": "draft",
                "notes": "从分镜提取·对上正典" if matched else "从分镜自动提取（短剧原创）",
                "entity": (matched.get("entity_id") or matched["name"]) if matched else "",
                "image": "",
            })
            existing.add((kind, norm))
            added += 1

        for shot in self.load_storyboard(episode)["shots"]:
            for name in re.split(r"[、,，/／+＋\s]+", str(shot.get("characters") or "")):
                add_asset("character", name)
            add_asset("location", str(shot.get("location") or ""))
        if added:
            self.save_assets(assets)
        # 提取完顺手把 shot→asset 引用表落盘（结构化引用，改名不断链）
        self.refresh_shot_assets(episode)
        return added

    # ── 资产 ↔ 小说正典 ──

    def canon_entities(self, kinds=("character", "location", "prop")) -> list[dict[str, Any]]:
        """小说侧可对应成资产的实体：人物卡 / 世界状态地点 / 物品图鉴。"""
        out: list[dict[str, Any]] = []
        if "character" in kinds:
            from .character_sim import CHARACTER_SKIP_TITLES

            data = _read_json(self.project_dir / "canon" / "characters.json", {})
            for rec in data.get("characters") or []:
                if not isinstance(rec, dict):
                    continue
                name = str(rec.get("name") or "").strip()
                if not name or name in CHARACTER_SKIP_TITLES:
                    continue
                profile = rec.get("public_profile") or {}
                summary = "；".join(
                    str(v).strip() for v in profile.values()
                    if isinstance(v, str) and v.strip()
                )[:200]
                out.append({
                    "kind": "character", "name": name,
                    "summary": summary, "entity_id": str(rec.get("id") or ""),
                    "aliases": [str(a).strip() for a in (rec.get("aliases") or [])
                                if str(a).strip()],
                })
        world = _read_json(self.project_dir / "world_state.json", {})
        if "location" in kinds:
            for name, info in (world.get("locations") or {}).items():
                out.append({"kind": "location", "name": str(name),
                            "summary": _entity_summary(info), "entity_id": ""})
        if "prop" in kinds:
            for name, info in (world.get("items_catalog") or {}).items():
                out.append({"kind": "prop", "name": str(name),
                            "summary": _entity_summary(info), "entity_id": ""})
        return out

    def _canon_index(self, kinds=("character", "location", "prop")) -> dict[tuple[str, str], dict]:
        """(类型, 名称/别名) → 正典实体，用来把分镜里的名字对回小说。"""
        index: dict[tuple[str, str], dict] = {}
        for ent in self.canon_entities(kinds):
            index[(ent["kind"], ent["name"])] = ent
            for alias in ent.get("aliases") or []:
                index.setdefault((ent["kind"], alias), ent)
        return index

    def match_canon_entity(self, kind: str, name: str) -> dict[str, Any] | None:
        """分镜里的角色/地点名 → 正典实体（支持别名、括号注解、包含匹配）。"""
        clean = _normalize_asset_name(name)
        if not clean:
            return None
        index = self._canon_index((kind,))
        if (kind, clean) in index:
            return index[(kind, clean)]
        for (k, alias), ent in index.items():
            if k != kind or not alias:
                continue
            if alias in clean or clean in alias:
                return ent
        return None

    def sync_assets_from_canon(self, kinds=("character", "location"), limit: int = 0) -> int:
        """把小说里的人物/地点补进资产表（按 类型+名称 去重，不覆盖已有行）。"""
        assets = self.load_assets()
        existing = {
            (a["kind"], _normalize_asset_name(a["name"]))
            for a in assets if a["name"].strip()
        }
        added = 0
        for ent in self.canon_entities(kinds):
            key = (ent["kind"], _normalize_asset_name(ent["name"]))
            if key in existing:
                continue
            assets.append({
                "kind": ent["kind"],
                "id": _unique_asset_id(assets, ent["kind"], ent["name"]),
                "name": ent["name"],
                "source": "canon",
                "prompt": "",
                "ref": "",
                "status": "draft",
                "notes": "从小说导入",
                "entity": ent.get("entity_id") or ent["name"],
                "image": "",
            })
            existing.add(key)
            added += 1
            if limit and added >= limit:
                break
        if added:
            self.save_assets(assets)
        return added

    def check_assets(self) -> dict[str, Any]:
        """资产体检：来源分布、缺提示词/图、正典失联、重名。

        「短剧原创」（source=storyboard，比如龙套、临时场景）不算错误，
        但会单独计数——它们是小说里没有、短剧自己需要的资产。
        """
        assets = self.load_assets()
        entities = self.canon_entities(("character", "location", "prop"))
        canon_ids = {str(e.get("entity_id") or ""): e for e in entities if e.get("entity_id")}
        canon_names: dict[str, set[str]] = {}
        for ent in entities:
            canon_names.setdefault(ent["kind"], set()).add(_normalize_asset_name(ent["name"]))
        report: dict[str, Any] = {
            "total": len(assets), "canon": 0, "original": 0, "manual": 0,
            "missing_prompt": 0, "missing_image": 0,
            "orphan": [], "duplicates": [], "prompt_risk": [], "aspect_risk": [],
            "makeup_incomplete": 0, "scene_incomplete": 0,
            "fullbody_incomplete": 0, "prop_incomplete": 0,
            "from_library": 0,
        }
        seen: set[tuple[str, str]] = set()
        for asset in assets:
            source = asset.get("source") or "manual"
            bucket = "canon" if source == "canon" else ("original" if source == "storyboard" else "manual")
            report[bucket] += 1
            if asset.get("library_id"):
                report["from_library"] += 1
            if not asset.get("prompt"):
                report["missing_prompt"] += 1
            if not asset.get("image"):
                report["missing_image"] += 1
            if asset["kind"] == "character" and len(self.makeup_paths(asset)) < len(MAKEUP_VIEWS):
                report["makeup_incomplete"] += 1
            if (asset["kind"] == "character"
                    and len(self.fullbody_paths(asset)) < len(FULLBODY_VIEWS)):
                report["fullbody_incomplete"] += 1
            if asset["kind"] == "location" and len(self.scene_paths(asset)) < len(SCENE_VIEWS):
                report["scene_incomplete"] += 1
            if asset["kind"] == "prop" and len(self.prop_paths(asset)) < len(PROP_VIEWS):
                report["prop_incomplete"] += 1
            name_key = (asset["kind"], _normalize_asset_name(asset["name"]))
            if name_key in seen:
                report["duplicates"].append(asset["name"])
            seen.add(name_key)
            if source != "canon":
                continue
            entity = str(asset.get("entity") or "")
            if entity in canon_ids:
                continue
            if _normalize_asset_name(asset["name"]) in canon_names.get(asset["kind"], set()):
                continue
            report["orphan"].append(asset["name"])
        for asset in assets:
            issues = self.audit_asset_prompt(asset)
            if issues:
                report.setdefault("prompt_risk", []).append(
                    {"name": asset.get("name") or asset.get("id") or "", "issues": issues}
                )
            # 生成图实际尺寸和类型画幅对不上：多半要裁，别直接拿去做首尾帧
            if asset.get("aspect_warning"):
                report["aspect_risk"].append({
                    "name": asset.get("name") or asset.get("id") or "",
                    "issue": asset["aspect_warning"],
                })
        return report

    # 生图前体检用：类型锚词（手填提示词至少含一个，防画错类型）
    _PROMPT_ANCHORS: dict[str, tuple[str, ...]] = {
        "character": ("人物", "男子", "女子", "少女", "少年", "男人", "女人", "角色", "定妆", "全身"),
        "location": ("场景", "空镜", "环境", "建筑", "室内", "外景", "街道"),
        "prop": ("道具", "物品", "物件", "特写"),
    }
    # 强人像词：出现在场景/道具提示词里而负面词没挡"人物"时，容易整图画成人
    _PORTRAIT_MARKERS = ("少女", "少年", "女孩", "男孩", "男子", "女子", "男人", "女人", "五官", "脸型", "站姿", "正面照")

    def audit_asset_prompt(self, asset: dict[str, Any]) -> list[str]:
        """生图前的本地体检（本地规则）：能在文字里发现的错，不留到图上。

        覆盖三类已知坑：手填提示词类型锚词缺失、场景/道具混入人像词、
        描述完全为空只能靠兜底模板（抽卡率高）。
        """
        kind = str(asset.get("kind") or "character")
        name = str(asset.get("name") or "").strip()
        issues: list[str] = []
        positive, negative = self.asset_prompt(asset)
        custom = str(asset.get("prompt") or "").strip()
        anchors = self._PROMPT_ANCHORS.get(kind, ())
        if custom and not any(a in custom for a in anchors):
            issues.append(f"手填提示词缺{ASSET_KIND_LABELS.get(kind, '资产')}类锚词，可能画错类型")
        if kind in ("location", "prop"):
            hit = next((m for m in self._PORTRAIT_MARKERS if m in positive), "")
            if hit:
                # 正向词就带偏类型，负面词只是最后防线，救不回"类型画错"
                issues.append(
                    f"提示词含人像词「{hit}」，与{ASSET_KIND_LABELS.get(kind, '资产')}类型不符，容易画成人"
                )
        has_desc = any(
            ent.get("summary") for ent in self.canon_entities((kind,))
            if ent["name"] == name
        )
        if not has_desc and not custom:
            notes = str(asset.get("notes") or "")
            if not notes or notes.startswith(("从分镜", "从小说导入")):
                issues.append("无描述走了类型兜底模板，建议先补正典摘要或手填提示词")
        if "、" in name or "和" in name or name.endswith("们"):
            issues.append("资产名疑似多个实体，建议拆分（数量写死，防生图自行加戏）")
        # 手填画幅和类型规格打架：参数按类型传，文字却写别的比例，图一定歪
        plan = ASSET_PROMPT_PLANS.get(kind, ASSET_PROMPT_PLANS["character"])
        declared = _declared_ratio(custom) if custom else None
        canvas = SCENE_PANORAMA_SIZE if kind == "location" else (int(plan["width"]), int(plan["height"]))
        if kind == "location" and declared and _ratio_equal(declared, (16, 9)):
            # 早期项目把 16:9 写入了场景默认词，生成环景时会转换其画幅要求。
            declared = None
        if declared and not _ratio_equal(declared, canvas):
            ratio = "2:1" if kind == "location" else plan["ratio"]
            issues.append(
                f"手填画幅 {declared[0]}:{declared[1]} 与{ASSET_KIND_LABELS.get(kind, '资产')}"
                f"规格 {ratio} 不一致"
            )
        # 人物固定特征必须写死位置：只写"有纹身"，每张图的位置都会漂
        if kind == "character":
            feature = next(
                (w for w in ("纹身", "刺青", "疤痕", "伤疤", "胎记", "痣") if w in positive),
                "",
            )
            if feature and not any(side in positive for side in ("左", "右")):
                issues.append(
                    f"固定特征「{feature}」没写位置（左/右），后面每张图都会漂"
                )
        return issues

    def asset_prompt(self, asset: dict[str, Any]) -> tuple[str, str]:
        """给一个资产生成 (正向提示词, 负向提示词)；已手填的提示词优先。

        人物/场景/道具各自走 ASSET_PROMPT_PLANS 的模板：
        场景出空镜、道具出特写，且负面词里把"人物"挡掉。
        """
        kind = str(asset.get("kind") or "character")
        plan = ASSET_PROMPT_PLANS.get(kind, ASSET_PROMPT_PLANS["character"])
        custom = str(asset.get("prompt") or "").strip()
        if custom:
            # 手填提示词也锁画幅：width/height 只是参数，模型看的是文字。
            # 没声明画幅就补一句；已写别的比例则保留，交给体检报冲突。
            declared = _declared_ratio(custom)
            already_right = any(
                _ratio_equal((int(w), int(h)), (int(plan["width"]), int(plan["height"])))
                for w, h in _BARE_RATIO_RE.findall(custom)
            )
            if declared is None and not already_right:
                custom = f"{custom}，{plan['ratio_phrase']}"
            return custom, plan["negative"]
        name = str(asset.get("name") or "").strip()
        label = ASSET_KIND_LABELS.get(kind, "资产")
        series = self.load_series()
        meta = _read_json(self.project_dir / "meta.json", {})
        genre = str(meta.get("genre") or series.get("genre") or "")
        style = str(meta.get("style") or series.get("style") or "")
        palette = str(series.get("palette") or "")
        desc = ""
        for ent in self.canon_entities((kind,)):
            if ent["name"] == name and ent.get("summary"):
                desc = ent["summary"]
                break
        if not desc:
            notes = str(asset.get("notes") or "")
            # "从分镜提取·对上正典"/"从分镜自动提取（短剧原创）"/"从小说导入"
            # 都是来源占位，不是画面描述，前缀判断统一挡掉
            if notes and not notes.startswith(("从分镜", "从小说导入")):
                desc = notes
        if not desc:
            desc = plan["fallback_desc"]
        parts = [f"{name}，{label}设定参考图", desc]
        if genre:
            parts.append(f"{genre}题材")
        if style:
            parts.append(f"{style}风格")
        if palette:
            parts.append(f"配色：{palette}")
        parts.append(plan["suffix"])
        positive = "，".join(parts)
        return positive, plan["negative"]

    def save_generated_image(
        self,
        asset_id: str,
        data: bytes,
        ext: str = ".png",
        expected_size: tuple[int, int] | None = None,
    ) -> str:
        """把生成图落到 assets/generated/，回填路径并标记状态；返回相对路径。

        expected_size 传该资产类型的计划画布：落盘后读真实像素核对比例。
        比例不对标 needs_crop、读不出尺寸标 needs_check，且**不拿这张图当定妆图**——
        否则错误比例会被固化成后续生成的一致性基准。图不删（钱已经花了，先留着裁）。
        """
        folder = self.assets_dir / "generated"
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\-]+", "_", asset_id) or "asset"
        path = folder / f"{safe}{ext}"
        assets = self.load_assets()
        size = image_dimensions(data)
        warning = ""
        mismatch = False
        unknown = False
        if expected_size:
            if size is None:
                unknown = True
                warning = (
                    f"读不出生成图尺寸，无法核对计划画幅 "
                    f"{expected_size[0]}×{expected_size[1]}，提交前人工看一眼"
                )
            elif not aspect_matches(size[0], size[1], expected_size):
                mismatch = True
                warning = (
                    f"生成图是 {size[0]}×{size[1]}，与计划画幅 "
                    f"{expected_size[0]}×{expected_size[1]} 不一致，用前先裁切"
                )
        # 首次图片作为独立定妆图保存，以后重生成不会逐轮漂移参考来源。
        # 画幅不对/读不出尺寸的图不当定妆图，避免把错误比例固化下来。
        if not (mismatch or unknown):
            for asset in assets:
                if asset["id"] == asset_id and not asset.get("reference_image"):
                    anchor = folder / "references" / f"{safe}{ext}"
                    anchor.parent.mkdir(parents=True, exist_ok=True)
                    anchor.write_bytes(path.read_bytes() if path.exists() else data)
                    asset["reference_image"] = f"generated/references/{anchor.name}"
        # 新版本存新文件：旧图移到 generated/versions/，不覆盖不删除
        # （重生成的不一定比旧的好，留着最后一起挑）
        if path.exists():
            history = folder / "versions"
            history.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target = history / f"{safe}_{stamp}{ext}"
            n = 2
            while target.exists():
                target = history / f"{safe}_{stamp}_{n}{ext}"
                n += 1
            from .drama_records import index_version
            index_version(self, file=f"assets/generated/{path.name}", owner=asset_id,
                          replaced_by=target.name, note="重生成旧图")
            path.replace(target)
        path.write_bytes(data)
        rel = f"generated/{path.name}"
        for asset in assets:
            if asset["id"] == asset_id:
                asset["image"] = rel
                asset["ref"] = rel
                # needs_crop：比例不对先裁；needs_check：尺寸读不出要人工确认
                asset["status"] = (
                    "needs_crop" if mismatch else ("needs_check" if unknown else "ready")
                )
                asset["image_size"] = f"{size[0]}x{size[1]}" if size else ""
                asset["aspect_warning"] = warning
        self.save_assets(assets)
        return rel

    # ── 导出 ──

    def export_storyboard_csv(self, episode: int | None = None) -> Path:
        """导出分镜表 CSV（UTF-8 BOM，Excel 可直接打开）。"""
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        if episode is None:
            filename = "分镜_全剧.csv"
            rows: list[dict[str, Any]] = []
            for ep in self.load_episodes():
                data = self.load_storyboard(int(ep["number"]))
                for shot in data["shots"]:
                    rows.append({"episode": int(ep["number"]), **shot})
        else:
            filename = f"分镜_第{int(episode):03d}集.csv"
            data = self.load_storyboard(int(episode))
            rows = [{"episode": int(episode), **shot} for shot in data["shots"]]
        path = self.exports_dir / filename
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["集数"] + [label for _key, label in SHOT_COLUMNS])
            for row in rows:
                values = [row.get("episode", "")]
                for key, _label in SHOT_COLUMNS:
                    value = row.get(key, "")
                    if key == "duration_sec":
                        try:
                            value = f"{float(value):.1f}"
                        except (TypeError, ValueError):
                            value = ""
                    values.append(value)
                writer.writerow(values)
        return path

    def export_storyboard_json(self, episode: int | None = None) -> Path:
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        if episode is None:
            payload = {
                "schema_version": SCHEMA_VERSION,
                "series": self.load_series(),
                "episodes": [
                    self.load_storyboard(int(ep["number"])) for ep in self.load_episodes()
                ],
            }
            path = self.exports_dir / "分镜_全剧.json"
        else:
            payload = self.load_storyboard(int(episode))
            path = self.exports_dir / f"分镜_第{int(episode):03d}集.json"
        _write_json(path, payload)
        return path

    def export_video_prompts(self, episode: int | None = None) -> Path:
        """导出七段式视频提示词（Markdown）：每镜一段 + 提交前审核清单。

        文章里"提交视频前半路审核"的落点：导出物自带核对清单，
        谁拿去生成都能按同一套标准逐条看，而不是凭感觉。
        """
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        series = self.load_series()
        style = str(series.get("style") or "")
        palette = str(series.get("palette") or "")
        numbers = (
            [int(episode)] if episode is not None
            else [int(ep["number"]) for ep in self.load_episodes()]
        )
        blocks = [
            "# 视频提示词（七段式）",
            "",
            "> 由分镜自动生成。提交视频生成前逐条核对：提示词每句和首尾帧对得上"
            "（位置、左右、颜色、人数、道具数量、衣服、表情）；首尾帧机位/大小/背景一致；"
            "和设定图、配色、前后镜头一致；动作有原因、手放在哪写清；"
            "避开同时动作/凭空出现/走反方向/歧义句。最后只写「通过」或「不通过」。",
            "",
            "> 首尾帧自查：动作要用的东西首帧里必须有；旁人的反应提前摆好、不要同一姿势；"
            "动作落点留空间；风向统一；缩到手机大小看远景；放大看多余物；"
            "首尾帧叠图看错位（不该变的地方必须一样）；两帧尺寸一致。",
            "",
        ]
        for number in numbers:
            data = self.load_storyboard(number)
            aspect = str(data.get("aspect") or series.get("aspect") or "9:16")
            title = str(data.get("title") or "").strip()
            heading = f"第{number}集" + (f" {title}" if title and title != f"第{number}集" else "")
            blocks.append(f"## {heading}")
            blocks.append("")
            for shot in data["shots"]:
                prompt = build_shot_video_prompt(shot, style=style, aspect=aspect, palette=palette)
                problems = audit_video_prompt(prompt)
                transition = str(shot.get("transition") or "").strip()
                start_sec = shot.get("start_sec")
                try:
                    start_text = f"{float(start_sec):.1f} 秒起"
                except (TypeError, ValueError):
                    start_text = ""
                try:
                    gen_text = f"生成 {float(shot.get('gen_seconds') or 0):.1f} 秒"
                except (TypeError, ValueError):
                    gen_text = ""
                meta = " · ".join(
                    part for part in (
                        str(shot.get("shot_size") or ""),
                        f"{float(shot.get('duration_sec') or 0):.1f} 秒",
                        gen_text,
                        start_text,
                        (f"转场：{transition}" if transition else ""),
                    ) if part
                )
                blocks.append(f"### {shot.get('shot_id') or '镜头'} · {meta}")
                blocks.append("```text")
                blocks.append(prompt)
                blocks.append("```")
                if problems:
                    blocks.append("> ⚠ 体检：" + "；".join(problems))
                blocks.append("")
        # 文章做法：提交前另开一个 agent，只挑错不改文件，最后只写「通过/不通过」。
        # 这里把这段审核提示词一起导出，保证换谁审都是同一套标准。
        blocks.extend([
            "## 提交前审核（另开一个 agent，只读不改）",
            "",
            "```text",
            "你是审核，只读不改。这些镜头马上要提交视频生成。",
            "材料：本文件的提示词、首帧、尾帧、设定图、分镜表、配色规则。",
            "逐项核对，放大看图：",
            "1. 提示词每句话和两张图对得上吗？（位置、左右、颜色、人数、道具数量、衣服、表情）",
            "2. 两张图的机位、大小、背景一致吗？",
            "3. 和设定图、配色规则、前后镜头对得上吗？",
            "4. 动作现实吗？每个动作的原因写了吗？手放在哪？",
            "5. 模型可能在哪里出错？（同时动作、凭空出现、长脸、走反方向、歧义句）",
            "最后只写「通过」或「不通过」。不通过就逐条列出要改什么。",
            "```",
            "",
            "## 模型容易犯的错 → 应该怎么写（写提示词时对着查）",
            "",
            pitfalls_markdown(),
        ])
        filename = (
            f"视频提示词_第{int(episode):03d}集.md" if episode is not None
            else "视频提示词_全剧.md"
        )
        path = self.exports_dir / filename
        path.write_text("\n".join(blocks).rstrip() + "\n", encoding="utf-8")
        return path

    # ── 交给 ComfyUI 的批次包 ──

    def export_comfy_batch(
        self,
        workflow: str = "",
        reference_workflow: str = "",
        base_url: str = "",
        episode: int | None = None,
        include_assets: bool = True,
    ) -> Path:
        """导出「素材 + 提示词 + 填好的工作流」批次包，交给 ComfyUI 直接跑。

        产物（``drama/exports/comfy_batch_时间戳/``）：
        - ``manifest.json``：每个资产的提示词/负面词/尺寸/种子/参考图/工作流路径；
        - ``workflows/{资产}.json``：占位符已填好的 API 格式工作流，可直接 Queue；
        - ``images/``：定妆图/参考图拷贝，包自包含；
        - ``prompts.md`` / ``README.md``：人看的清单与用法。

        工作流模板空着时只导出提示词和清单（先在设置里选 ComfyUI 工作流再导）。
        """
        from .comfyui import fill_workflow, load_workflow
        from .image_generation import image_reference_canvas

        template = load_workflow(workflow) if workflow else None
        ref_template = load_workflow(reference_workflow) if reference_workflow else None
        # 模板没写 {{width}}/{{height}} 时，画幅由模板固定，软件传的尺寸不会生效；
        # 导出的 README 要把这件事说清楚，否则拿到批次的人会以为画幅已经按类型设好。
        template_text = json.dumps(template, ensure_ascii=False) if template else ""
        size_placeholders = "{{width}}" in template_text and "{{height}}" in template_text

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = self.exports_dir / f"comfy_batch_{stamp}"
        workflows_dir = out / "workflows"
        images_dir = out / "images"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        images_dir.mkdir(parents=True, exist_ok=True)

        jobs: list[dict[str, Any]] = []
        for index, asset in enumerate(
            self.load_assets() if include_assets else [], start=1
        ):
            kind = str(asset.get("kind") or "character")
            asset_id = str(asset.get("id") or f"asset_{index:03d}")
            safe = re.sub(r"[^\w\-]+", "_", asset_id) or f"asset_{index:03d}"
            width, height = asset_canvas(kind)
            source = str(asset.get("reference_image") or "")
            if not source and not asset.get("aspect_warning"):
                source = str(asset.get("image") or "")
            src = self.assets_dir / source if source else None
            anchor = src if src and src.is_file() else None
            views = self.makeup_paths(asset)
            fullbody = self.fullbody_paths(asset)
            prop_views = self.prop_paths(asset)
            # 任务分层：(layer, view)。layer 决定提示词与前缀，view 决定角度。
            if kind == "character":
                angles = [("makeup", v) for v in MAKEUP_VIEWS if "front" not in views or v not in views]
                angles += [("fullbody", v) for v in FULLBODY_VIEWS
                           if "front" not in fullbody or v not in fullbody]
            elif kind == "location":
                if len(self.scene_paths(asset)) == len(SCENE_VIEWS):
                    continue
                angles = [("panorama", "panorama")]
                width, height = SCENE_PANORAMA_SIZE
                if ref_template is None:
                    anchor = None
            elif kind == "prop":
                if len(prop_views) == len(PROP_VIEWS):
                    continue
                angles = [("prop", v) for v in PROP_VIEWS
                          if "front" not in prop_views or v not in prop_views]
            else:
                angles = [("", "")]

            for layer, angle in angles:
                if layer == "panorama":
                    prefix = f"{safe}_panorama"
                    seed_token = f"{asset_id}_panorama"
                elif layer == "fullbody":
                    prefix = f"{safe}_fb_{angle}"
                    seed_token = f"{asset_id}_fb_{angle}"
                elif layer == "prop":
                    prefix = f"{safe}_{angle}"
                    seed_token = f"{asset_id}_{angle}"
                elif layer == "makeup":
                    prefix = f"{safe}_{angle}"
                    seed_token = f"{asset_id}_{angle}"
                else:
                    prefix = safe
                    seed_token = asset_id
                base_front = {"makeup": views, "fullbody": fullbody,
                              "prop": prop_views}.get(layer, {})
                # 侧面/背面依赖同层正面照：正面已存在就直接当参考，否则排队里的正面任务
                if layer == "fullbody":
                    side = angle in ("side", "back")
                    depends_on = f"{safe}_fb_front" if side and "front" not in fullbody else ""
                    if angle == "front":
                        ref = anchor
                    else:
                        # 同层正面优先，退回定妆正面；不用通用锚点（与定妆照语义一致）
                        ref = fullbody.get("front") or views.get("front")
                elif layer == "prop":
                    side = angle in ("side", "back")
                    depends_on = f"{safe}_front" if side and "front" not in prop_views else ""
                    ref = prop_views.get("front") if side else anchor
                elif layer == "makeup":
                    side = angle in ("left", "right", "back")
                    depends_on = f"{safe}_front" if side and "front" not in views else ""
                    ref = views.get("front") if side else anchor
                else:
                    side = False
                    depends_on = ""
                    ref = anchor
                reference_input = f"{depends_on}_input.png" if depends_on else ""
                reference = ""
                if ref:
                    dest = images_dir / (f"{prefix}.png" if layer else f"{prefix}{ref.suffix or '.png'}")
                    if layer:
                        with image_reference_canvas(ref, (width, height)) as prepared:
                            dest.write_bytes(prepared.read_bytes())
                    else:
                        dest.write_bytes(ref.read_bytes())
                    reference = f"images/{dest.name}"
                    reference_input = dest.name

                if layer == "panorama":
                    positive, negative = self.scene_prompt(asset)
                elif layer == "makeup":
                    positive, negative = self.makeup_prompt(asset, angle)
                elif layer == "fullbody":
                    positive, negative = self.fullbody_prompt(asset, angle)
                elif layer == "prop":
                    positive, negative = self.prop_prompt(asset, angle)
                else:
                    positive, negative = self.asset_prompt(asset)
                seed = asset_seed(self.assets_dir, seed_token)
                name = str(asset.get("name") or "")
                if layer == "panorama":
                    label = SCENE_VIEWS["panorama"]
                elif layer == "makeup":
                    label = MAKEUP_VIEWS[angle][0]
                elif layer == "fullbody":
                    label = FULLBODY_VIEWS[angle][0]
                elif layer == "prop":
                    label = PROP_VIEWS[angle][0]
                else:
                    label = ""
                job: dict[str, Any] = {
                    "asset_id": asset_id, "kind": kind,
                    "name": f"{name} · {label}" if label else name,
                    "source": str(asset.get("source") or ""),
                    "entity": str(asset.get("entity") or ""),
                    "status": str(asset.get("status") or ""),
                    "width": width, "height": height, "seed": seed, "prefix": prefix,
                    "positive": positive, "negative": negative,
                    "reference_image": reference, "workflow": "",
                }
                if layer == "panorama":
                    job["scene_view"] = angle
                elif layer:
                    job.update(**{f"{layer}_view": angle}, depends_on=depends_on,
                               reference_input=reference_input)
                chosen = ref_template if reference_input and (layer or ref_template is not None) else template
                if chosen is not None:
                    filled = fill_workflow(chosen, {
                        "positive": positive, "negative": negative, "seed": seed, "prefix": prefix,
                        "width": width, "height": height, "reference_image": reference_input,
                    })
                    wf_path = workflows_dir / f"{prefix}.json"
                    _write_json(wf_path, filled)
                    job["workflow"] = f"workflows/{wf_path.name}"
                jobs.append(job)

        keyframe_jobs: list[dict[str, Any]] = []
        keyframe_missing: list[str] = []
        if episode is not None:
            from .drama_keyframes import (
                compose_keyframe_image_prompt, keyframe_canvas, ratio_label,
            )

            storyboard = self.load_storyboard(int(episode))
            briefs = {
                item["shot_id"]: item
                for item in self.load_keyframes(int(episode))["shots"]
            }
            series = self.load_series()
            style = str(series.get("style") or "")
            palette = str(series.get("palette") or "")
            kf_width, kf_height = keyframe_canvas(
                str(storyboard.get("aspect") or series.get("aspect") or "9:16")
            )
            keyframe_missing = [
                str(shot.get("shot_id") or "")
                for shot in storyboard["shots"]
                if str(shot.get("shot_id") or "") not in briefs
            ]
            for shot in storyboard["shots"]:
                shot_id = str(shot.get("shot_id") or "")
                brief = briefs.get(shot_id)
                if not brief:
                    continue
                for which, field in (("first", "first_frame"), ("last", "last_frame")):
                    text = str(brief.get(field) or "").strip()
                    if not text:
                        continue
                    safe = re.sub(r"[^\w\-]+", "_", f"{shot_id}_{which}")
                    positive = compose_keyframe_image_prompt(
                        text, style=style, palette=palette,
                        ratio=ratio_label(kf_width, kf_height),
                    )
                    seed = asset_seed(self.keyframes_dir, f"{episode}:{shot_id}:{which}")
                    reference = ""
                    src = self.keyframe_image_path(int(episode), shot_id, which)
                    if src.is_file():
                        dest = images_dir / f"{safe}{src.suffix or '.png'}"
                        dest.write_bytes(src.read_bytes())
                        reference = f"images/{dest.name}"
                    kf_job: dict[str, Any] = {
                        "asset_id": f"{shot_id}_{which}",
                        "kind": f"keyframe_{which}",
                        "shot_id": shot_id,
                        "name": f"{shot_id} {'首帧' if which == 'first' else '尾帧'}",
                        "width": kf_width,
                        "height": kf_height,
                        "seed": seed,
                        "prefix": safe,
                        "positive": positive,
                        "negative": ASSET_NEGATIVE,
                        "reference_image": reference,
                        "workflow": "",
                    }
                    if template is not None:
                        filled = fill_workflow(template, {
                            "positive": positive,
                            "negative": ASSET_NEGATIVE,
                            "seed": seed,
                            "prefix": safe,
                            "width": kf_width,
                            "height": kf_height,
                            "reference_image": Path(reference).name if reference else "",
                        })
                        wf_path = workflows_dir / f"{safe}.json"
                        _write_json(wf_path, filled)
                        kf_job["workflow"] = f"workflows/{wf_path.name}"
                    keyframe_jobs.append(kf_job)

        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "base_url": base_url or "",
            "workflow_template": "",
            "reference_workflow_template": "",
            "workflow_size_placeholders": size_placeholders,
            "episode": int(episode) if episode is not None else 0,
            "jobs": jobs,
            "keyframe_jobs": keyframe_jobs,
            "keyframe_missing": keyframe_missing,
        }
        if template is not None:
            manifest["workflow_template"] = "workflows/_template_api.json"
            _write_json(workflows_dir / "_template_api.json", template)
        if ref_template is not None:
            manifest["reference_workflow_template"] = "workflows/_template_reference.json"
            _write_json(workflows_dir / "_template_reference.json", ref_template)

        _write_json(out / "manifest.json", manifest)
        (out / "prompts.md").write_text(
            self._comfy_batch_markdown(jobs, keyframe_jobs), encoding="utf-8"
        )
        (out / "README.md").write_text(
            self._comfy_batch_readme(
                len(jobs), template is not None, ref_template is not None,
                size_placeholders, len(keyframe_jobs),
            ),
            encoding="utf-8",
        )
        return out

    @staticmethod
    def _comfy_batch_markdown(
        jobs: list[dict[str, Any]], keyframe_jobs: list[dict[str, Any]] | None = None
    ) -> str:
        keyframe_jobs = keyframe_jobs or []
        summary = f"共 {len(jobs)} 个资产出图任务"
        if keyframe_jobs:
            summary += f"、{len(keyframe_jobs)} 张首尾帧"
        lines = ["# ComfyUI 素材与提示词", "", summary + "。", ""]

        def block(job: dict[str, Any], heading: str) -> None:
            lines.extend([
                heading,
                "",
                f"- 尺寸：{job['width']}×{job['height']}",
                f"- 种子：{job['seed']}",
                f"- 工作流：`{job['workflow'] or '未配置'}`",
                f"- 参考图：`{job['reference_image'] or '无'}`",
                "",
                "**正向提示词**",
                "```text",
                job["positive"],
                "```",
                "",
                "**负向提示词**",
                "```text",
                job["negative"],
                "```",
                "",
            ])
            if job.get("depends_on"):
                lines.append(f"先运行 `{job['depends_on']}`，将出图上传为 `{job['reference_input']}`。\n")

        for job in jobs:
            block(job, f"## {job['name']}（{job['kind']}）")
        if keyframe_jobs:
            lines.extend(["## 首尾帧（本集）", ""])
            for job in keyframe_jobs:
                block(job, f"### {job['name']}")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _comfy_batch_readme(
        count: int,
        has_workflow: bool,
        has_reference: bool,
        size_ok: bool = True,
        keyframe_count: int = 0,
    ) -> str:
        workflow_line = (
            "每个资产已把占位符填好，`workflows/*.json` 是 API 格式，可在 ComfyUI 里 "
            "「Load」后直接 Queue。"
            if has_workflow
            else "未配置 ComfyUI 工作流：本次只导出了提示词、素材和清单；"
            "到「设置 → 图片生成 API → ComfyUI」选好工作流后重新导出，才会生成 workflows/*.json。"
        )
        reference_line = (
            "有定妆图的资产会优先用「参考图工作流」，参考图已拷到 `images/`；"
            "跑之前先把图上传到 ComfyUI 的 input 目录（或直接拖进 LoadImage），"
            "填好的工作流里用的是文件名。"
            if has_reference
            else "没有配置参考图工作流：只按提示词出图，不锁定人物外观。"
        )
        size_line = (
            ""
            if (not has_workflow or size_ok)
            else "⚠ 工作流模板没有 `{{width}}`/`{{height}}` 占位符：画幅由模板固定，"
            "软件传的类型画幅不会生效，先去模板里把它们改成占位符再重导。\n\n"
        )
        keyframe_line = (
            f"本包还包含本集 {keyframe_count} 张首尾帧（`keyframe_jobs`，kind=keyframe_first/last）："
            "画布按剧集画幅取 720p，提示词来自「首尾帧提示词」；"
            "先跑资产定妆图，再跑首尾帧，最后把首尾帧交给视频工作流。\n\n"
            if keyframe_count else ""
        )
        return (
            "# ComfyUI 批次包\n\n"
            f"本包包含 {count} 个资产出图任务的素材、提示词和工作流"
            + (f"，以及 {keyframe_count} 张首尾帧" if keyframe_count else "")
            + "，交给 ComfyUI 直接跑。\n\n"
            "## 怎么用\n\n"
            "1. 启动 ComfyUI（默认 `http://127.0.0.1:8188`）；\n"
            "2. 把 `images/` 里的参考图上传到 ComfyUI 的 `input/`（没有参考图可跳过）；\n"
            "3. 在 ComfyUI 里 Load `workflows/{名字}.json`（API 格式）→ Queue；\n"
            "   或按 `manifest.json` 的 `jobs` / `keyframe_jobs` 逐条 POST `/prompt`；\n"
            "4. 出图后用文件名前缀对回资产（`prefix` 字段就是资产 ID / 镜号）。\n\n"
            "人物妆发照为独立的正面、左侧、右侧、正后方任务。已有正面照时会随包附参考图。"
            "人物另有全身正/侧/背三视图（任务前缀带 `_fb_`）；道具为正面/侧面/背面三视图。"
            "如果任务有 `depends_on`，先出正面照，再将该图以 `reference_input` 指定的文件名上传到 ComfyUI 的 input/，"
            "之后运行其余角度。使用已有参考或有依赖的任务需要参考图工作流。\n\n"
            "场景任务输出单张 2:1 环景。应用内生成时会从环景自动投影出正向和反向透视。\n\n"
            f"{workflow_line}\n\n"
            f"{reference_line}\n\n"
            f"{size_line}"
            f"{keyframe_line}"
            "## 注意\n\n"
            "- 画幅按资产类型固定：人物 3:4、场景 16:9、道具 1:1；首尾帧按剧集画幅；\n"
            "- 内置工作流 CFG=1 时负向提示词不生效，排除信息必须靠正向词；\n"
            "- 同一资产的 `seed` 固定，重跑结果可复现；换构图改提示词再重导；\n"
            "- 资产包和首尾帧都在本包；视频生成在「短剧工作流 → 生成本集视频」里跑。\n"
        )

    # ── 手工批次结果回流 ──

    def _import_prefixes(self) -> dict[str, tuple[str, str, str, int, str]]:
        """文件名前缀 → (layer, asset_id/shot_id, view, episode, kind)。"""
        targets: dict[str, tuple[str, str, str, int, str]] = {}
        for asset in self.load_assets():
            asset_id = str(asset.get("id") or "")
            safe = re.sub(r"[^\w\-]+", "_", asset_id) or "asset"
            kind = str(asset.get("kind") or "")
            if kind == "character":
                for view in MAKEUP_VIEWS:
                    targets[f"{safe}_{view}"] = ("makeup", asset_id, view, 0, kind)
                for view in FULLBODY_VIEWS:
                    targets[f"{safe}_fb_{view}"] = ("fullbody", asset_id, view, 0, kind)
            elif kind == "location":
                targets[f"{safe}_panorama"] = ("panorama", asset_id, "", 0, kind)
            elif kind == "prop":
                for view in PROP_VIEWS:
                    targets[f"{safe}_{view}"] = ("prop", asset_id, view, 0, kind)
            targets[safe] = ("plain", asset_id, "", 0, kind)
        return targets

    def import_comfy_results(self, folder: Path | str) -> dict[str, Any]:
        """把手工跑完的出图按文件名前缀回流进项目（批次包 images/ 或 ComfyUI output 都行）。

        前缀规则和 export_comfy_batch 一致；ComfyUI 重名会追加计数后缀
        （``xxx_00001_.png``），按最长前缀命中。画幅不对的照常报错但不停整批。
        返回 ``{"imported": n, "failed": [...], "unmatched": [...]}``。
        """
        folder = Path(folder)
        if not folder.is_dir():
            raise ValueError(f"目录不存在：{folder}")
        files = [p for p in sorted(folder.iterdir())
                 if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
        if not files:
            raise ValueError(f"目录里没有图片：{folder}")
        targets = self._import_prefixes()
        # 首尾帧：{镜号}_{first|last}，镜号在分镜表里
        episode_shots: set[str] = set()
        for ep in self.load_episodes():
            for shot in self.load_storyboard(int(ep["number"]))["shots"]:
                sid = str(shot.get("shot_id") or "")
                if sid:
                    episode_shots.add(sid)
        for sid in episode_shots:
            safe = re.sub(r"[^\w\-]+", "_", sid)
            for which in ("first", "last"):
                m = re.match(r"E(\d+)-S\d+", sid)
                targets[f"{safe}_{which}"] = (
                    "keyframe", sid, which, int(m.group(1)) if m else 1, "",
                )
        imported = 0
        failed: list[str] = []
        unmatched: list[str] = []
        for path in files:
            stem = path.stem
            hit: tuple[str, tuple[str, str, str, int, str]] | None = None
            for prefix, spec in targets.items():
                if stem == prefix or stem.startswith(prefix + "_") or stem.startswith(prefix + "-"):
                    if hit is None or len(prefix) > len(hit[0]):
                        hit = (prefix, spec)
            if hit is None:
                unmatched.append(path.name)
                continue
            layer, ident, view, episode, kind = hit[1]
            data = path.read_bytes()
            try:
                if layer == "makeup":
                    self.save_makeup_view(ident, view, data)
                elif layer == "fullbody":
                    self.save_fullbody_view(ident, view, data)
                elif layer == "prop":
                    self.save_prop_view(ident, view, data)
                elif layer == "panorama":
                    self.save_scene_panorama(ident, data)
                elif layer == "keyframe":
                    self.save_keyframe_image(episode or 1, ident, view, data)
                else:
                    self.save_generated_image(ident, data, expected_size=asset_canvas(kind))
                imported += 1
            except (ValueError, KeyError) as e:
                failed.append(f"{path.name}：{e}")
        return {"imported": imported, "failed": failed, "unmatched": unmatched}
