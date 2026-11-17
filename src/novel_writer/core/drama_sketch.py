"""分镜色块草图：每镜 1920×1080、标出 38%/62%、拼成总表、转黑白查对比。

文章 L173-L175 的要求：

```text
最后用代码给每个镜头画一张 1920×1080 的色块草图，标出 38% 和 62% 的位置和
主要的东西，拼成一张总表给我确认。再把草图转成黑白看一遍，如果人和背景区分
不开，就说明明暗对比不明显，要重新调配色。
```

落地点：

- `draw_sketch()`：按分镜的景别/机位/人物数/走位方向画色块（本地绘制）；
- `contact_sheet()`：4 列拼总表，每格带镜号、景别、时长；
- `grayscale_contrast()`：算「人（主体）平均亮度 vs 背景平均亮度」，差 < 28 判为
  明暗对比不足——这就是文章说的「转黑白分不清人和背景」；
- `palette_table()` / `palette_table_md()`：每场用什么颜色单独列成色表，
  画第一张图之前定死；
- `build_episode_sketches()`：一集全画完，落
  `drama/exports/sketch_epNNN/`（单镜 + 总表 + 黑白总表）与 `色表_第N集.md`。

Pillow 是可选依赖：没装时抛 `SketchError`，提示装 pillow，不影响其它功能。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import project_io
from .logger import get_logger

logger = get_logger(__name__)

SKETCH_W, SKETCH_H = 1920, 1080
GUIDE_RATIOS = (0.38, 0.62)
CONTRAST_THRESHOLD = 28.0

# 兜底色板：调色板缺省时按镜号轮换，保证相邻镜头颜色有区分
FALLBACK_COLORS = (
    ("#1f3352", "#0d1626"),  # 夜戏冷蓝
    ("#4a3a22", "#20180d"),  # 暖黄室内
    ("#2d3b2f", "#131c16"),  # 青灰林间
    ("#3a2430", "#1a0f16"),  # 暗红
    ("#2b2f3a", "#12151c"),  # 阴天灰
    ("#3f3326", "#1c1710"),  # 土黄
)

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)
_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
_DIRECTION_WORDS = ("画面左", "画面右", "左边", "右边", "左侧", "右侧")
_MOVE_WORDS = ("走", "跑", "冲", "退", "转身", "递", "抬", "拿", "跨过")


class SketchError(RuntimeError):
    """缺 Pillow 或画布参数非法。"""


def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise SketchError(
            "没装 Pillow：画色块草图需要 `pip install pillow`（dev 依赖里已有）。"
        ) from None
    return Image, ImageDraw, ImageFont


def _font(size: int):
    _Image, _draw, ImageFont = _pil()
    for candidate in _FONT_CANDIDATES:
        try:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def _hex_to_rgb(value: str, default: tuple[int, int, int] = (40, 48, 64)) -> tuple[int, int, int]:
    text = (value or "").strip().lstrip("#")
    if len(text) == 6:
        try:
            return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
        except ValueError:
            pass
    return default


def _darken(rgb: tuple[int, int, int], factor: float = 0.55) -> tuple[int, int, int]:
    return tuple(max(0, int(channel * factor)) for channel in rgb)  # type: ignore[return-value]


# ── 色表 ──


def palette_table(series: dict[str, Any] | None) -> list[dict[str, str]]:
    """每场颜色表：优先结构化 `palette_table`，否则从 `palette` 一段文字拆。"""
    series = series or {}
    table = series.get("palette_table")
    rows: list[dict[str, str]] = []
    if isinstance(table, list):
        for item in table:
            if not isinstance(item, dict):
                continue
            scene = str(item.get("scene") or "").strip()
            colors = str(item.get("colors") or "").strip()
            if scene or colors:
                rows.append({"scene": scene or "未命名场次", "colors": colors, "hex": str(item.get("hex") or "")})
    if rows:
        return rows
    text = str(series.get("palette") or "").strip()
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"[；;\n]+", text) if part.strip()]
    for index, part in enumerate(parts, start=1):
        match = re.match(r"^([^：:]{1,12})[：:](.+)$", part)
        if match:
            rows.append({"scene": match.group(1).strip(), "colors": match.group(2).strip(), "hex": ""})
        else:
            scene = "全场" if len(parts) == 1 else f"第{index}段"
            rows.append({"scene": scene, "colors": part, "hex": ""})
    return rows


def palette_colors(text: str) -> list[str]:
    return _HEX_RE.findall(text or "")


def palette_for_scene(series: dict[str, Any] | None, scene_key: str, index: int) -> str:
    """给某个场次挑一组颜色（hex）；匹配不到就按镜号轮换。"""
    key = (scene_key or "").strip()
    if key:
        for row in palette_table(series):
            scene = row.get("scene") or ""
            if scene and (scene in key or key in scene):
                found = palette_colors(row.get("colors", "") + row.get("hex", ""))
                if found:
                    return found[0]
    top, _bottom = FALLBACK_COLORS[index % len(FALLBACK_COLORS)]
    return top


def palette_table_md(series: dict[str, Any] | None) -> str:
    rows = palette_table(series)
    lines = [
        "# 色表",
        "",
        "> 配色单独设计，在画第一张图之前定好；不交给模型自由发挥。",
        "> 定完把画面转成黑白看一遍：人和背景分不开，说明明暗对比不够，要重配。",
        "",
    ]
    if not rows:
        lines.append("（还没定配色：在企划页填「配色」，例如「夜戏：冷蓝 + 暖黄灯笼；日戏：青灰 + 土黄」）")
        return "\n".join(lines) + "\n"
    lines += ["| 场次 | 颜色 | 色块 |", "|---|---|---|"]
    for row in rows:
        hexes = palette_colors(row.get("colors", "")) or ([row["hex"]] if row.get("hex") else [])
        swatch = " ".join(hexes) if hexes else "（纯文字描述）"
        lines.append(f"| {row.get('scene') or ''} | {row.get('colors') or ''} | {swatch} |")
    return "\n".join(lines) + "\n"


# ── 草图 ──


def _people_count(shot: dict[str, Any]) -> int:
    raw = str(shot.get("characters") or "").strip()
    if not raw:
        return 0
    parts = [part for part in re.split(r"[、,，/／+＋\s]+", raw) if part]
    return max(0, min(4, len(parts)))


def _direction(shot: dict[str, Any]) -> str:
    action = str(shot.get("action") or "")
    if "画面左" in action or "左边" in action or "左侧" in action:
        return "left"
    if "画面右" in action or "右边" in action or "右侧" in action:
        return "right"
    return ""


def _draw_text(draw, xy, text: str, *, size: int = 40, fill=(255, 255, 255), anchor="la"):
    font = _font(size)
    try:
        draw.text(xy, text, font=font, fill=fill, anchor=anchor, stroke_width=2,
                  stroke_fill=(0, 0, 0))
    except (OSError, ValueError):
        draw.text(xy, text, font=font, fill=fill)


def subject_boxes(people: int) -> list[tuple[int, int, int, int]]:
    """主体框（头+身体）：按 38%/62% 两条黄金位置摆人。"""
    boxes: list[tuple[int, int, int, int]] = []
    if people <= 0:
        return boxes
    slots = list(GUIDE_RATIOS)
    if people > 2:
        slots = [0.18, 0.38, 0.62, 0.82]
    for i in range(people):
        ratio = slots[i] if i < len(slots) else GUIDE_RATIOS[i % 2]
        cx = int(SKETCH_W * ratio)
        top = int(SKETCH_H * 0.22) + (i % 2) * 40
        body_w = int(SKETCH_W * 0.045) if i == 0 else int(SKETCH_W * 0.033)
        boxes.append((cx - body_w, top, cx + body_w, int(SKETCH_H * 0.86)))
    return boxes


def _draw_arrow(draw, x: int, y: int, direction: str, color=(255, 214, 102)):
    if not direction:
        return
    length = 260 if direction == "right" else -260
    end = x + length
    draw.line([(x, y), (end, y)], fill=color, width=8)
    tip = 26
    if direction == "right":
        draw.polygon([(end, y), (end - tip, y - tip // 2), (end - tip, y + tip // 2)], fill=color)
    else:
        draw.polygon([(end, y), (end + tip, y - tip // 2), (end + tip, y + tip // 2)], fill=color)


def draw_sketch(
    shot: dict[str, Any],
    *,
    palette_color: str = "",
    index: int = 1,
    size: tuple[int, int] = (SKETCH_W, SKETCH_H),
):
    """画一个镜头的色块草图（返回 PIL.Image）。"""
    Image, ImageDraw, _ImageFont = _pil()
    width, height = size
    top_color = _hex_to_rgb(palette_color, _hex_to_rgb(FALLBACK_COLORS[index % len(FALLBACK_COLORS)][0]))
    bottom_color = _darken(top_color)
    image = Image.new("RGB", (width, height), top_color)
    draw = ImageDraw.Draw(image)

    # 天/墙 + 地：一眼看出空间和明暗分层
    draw.rectangle([0, 0, width, int(height * 0.62)], fill=top_color)
    draw.rectangle([0, int(height * 0.62), width, height], fill=bottom_color)
    draw.line([(0, int(height * 0.62)), (width, int(height * 0.62))], fill=_darken(top_color, 0.35), width=4)

    # 38% / 62% 黄金位置：竖虚线 + 标注
    for ratio in GUIDE_RATIOS:
        x = int(width * ratio)
        for y in range(0, height, 36):
            draw.line([(x, y), (x, y + 18)], fill=(255, 255, 255, 120), width=3)
        _draw_text(draw, (x + 10, 84), f"38%" if ratio == 0.38 else "62%", size=34, fill=(255, 214, 102))

    people = _people_count(shot)
    for box in subject_boxes(people):
        draw.rectangle(box, fill=(238, 238, 238), outline=(20, 20, 20), width=4)
        head_r = int((box[2] - box[0]) * 0.42)
        head_cx = (box[0] + box[2]) // 2
        head_cy = box[1] - head_r
        draw.ellipse(
            [head_cx - head_r, head_cy - head_r, head_cx + head_r, head_cy + head_r],
            fill=(238, 238, 238), outline=(20, 20, 20), width=4,
        )

    # 主要道具：底部三个色块（对应「画面里主要的东西」）
    props = [str(part).strip() for part in re.split(r"[、,，/／]", str(shot.get("props") or "")) if part]
    for i, _prop in enumerate(props[:3]):
        x0 = int(width * (0.12 + 0.28 * i))
        y0 = int(height * 0.74)
        draw.rectangle([x0, y0, x0 + int(width * 0.06), y0 + int(height * 0.09)],
                       fill=(255, 255, 255), outline=(20, 20, 20), width=3)

    direction = _direction(shot)
    _draw_arrow(draw, int(width * 0.5), int(height * 0.9), direction)

    # 左上角镜号牌 + 底部技术信息
    shot_id = str(shot.get("shot_id") or f"S{index:03d}")
    duration = shot.get("duration_sec") or 0
    try:
        duration_text = f"{float(duration):.1f}s"
    except (TypeError, ValueError):
        duration_text = "?s"
    plaque = f"{shot_id} · {shot.get('shot_size') or '未写景别'} · {duration_text}"
    draw.rectangle([0, 0, int(width * 0.42), 72], fill=(0, 0, 0))
    _draw_text(draw, (16, 14), plaque, size=36)
    camera = str(shot.get("camera") or "未写机位")
    action = str(shot.get("action") or "")[:42]
    footer = f"{camera}｜{action}"
    draw.rectangle([0, height - 64, width, height], fill=(0, 0, 0))
    _draw_text(draw, (16, height - 56), footer, size=30)
    return image


def grayscale_contrast(image, people: int = 1) -> dict[str, Any]:
    """转黑白后量「人 vs 背景」的平均亮度差；差值太小说明明暗对比不够。"""
    Image, _draw, _font = _pil()
    gray = image.convert("L")
    width, height = gray.size
    boxes = subject_boxes(people) or [(int(width * 0.38) - 40, int(height * 0.3),
                                       int(width * 0.38) + 40, int(height * 0.8))]
    pixels = gray.load()
    subject_values: list[int] = []
    for box in boxes:
        x0, y0, x1, y1 = [max(0, int(v)) for v in box]
        for x in range(x0, min(x1, width), max(1, (x1 - x0) // 12)):
            for y in range(y0, min(y1, height), max(1, (y1 - y0) // 12)):
                subject_values.append(int(pixels[x, y]))
    # 背景取右上角一块（人通常在左/中，右上角一般是墙/天）
    bg_box = (int(width * 0.72), int(height * 0.12), int(width * 0.96), int(height * 0.46))
    background_values: list[int] = []
    for x in range(bg_box[0], bg_box[2], max(1, (bg_box[2] - bg_box[0]) // 12)):
        for y in range(bg_box[1], bg_box[3], max(1, (bg_box[3] - bg_box[1]) // 12)):
            background_values.append(int(pixels[x, y]))
    subject = sum(subject_values) / len(subject_values) if subject_values else 0.0
    background = sum(background_values) / len(background_values) if background_values else 0.0
    diff = abs(subject - background)
    return {
        "subject_luma": round(subject, 1),
        "background_luma": round(background, 1),
        "diff": round(diff, 1),
        "ok": diff >= CONTRAST_THRESHOLD,
        "threshold": CONTRAST_THRESHOLD,
    }


def contact_sheet(images: list[Any], *, cols: int = 4, title: str = "",
                  cell: tuple[int, int] = (480, 270), gap: int = 10):
    """拼总表：4 列，每格下面留白写镜号由草图自带。"""
    Image, ImageDraw, _ImageFont = _pil()
    count = max(1, len(images))
    rows = (count + cols - 1) // cols
    width = cols * cell[0] + (cols + 1) * gap
    header = 64 if title else 0
    height = header + rows * cell[1] + (rows + 1) * gap
    sheet = Image.new("RGB", (width, height), (18, 20, 24))
    draw = ImageDraw.Draw(sheet)
    if title:
        _draw_text(draw, (gap, 12), title, size=36)
    for i, image in enumerate(images):
        row, col = divmod(i, cols)
        x = gap + col * (cell[0] + gap)
        y = header + gap + row * (cell[1] + gap)
        from PIL import ImageOps
        thumb = ImageOps.contain(image, cell)
        sheet.paste(thumb, (x + (cell[0]-thumb.width)//2, y + (cell[1]-thumb.height)//2))
        draw.rectangle([x, y, x + cell[0], y + cell[1]], outline=(90, 96, 108), width=2)
    return sheet


def build_episode_sketches(store, episode: int) -> dict[str, Any]:
    """一集全画：单镜草图 + 总表 + 黑白总表 + 色表 Markdown。"""
    shots = list(store.load_storyboard(int(episode)).get("shots") or [])
    if not shots:
        raise SketchError(f"第{episode}集还没有分镜，先拆镜再画草图")
    series = store.load_series()
    from .drama_production import stamp
    out_dir = Path(store.exports_dir) / f"sketch_ep{int(episode):03d}" / stamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    images: list[Any] = []
    contrast_rows: list[dict[str, Any]] = []
    for index, shot in enumerate(shots, start=1):
        scene_key = "·".join(
            part for part in (str(shot.get("location") or ""), str(shot.get("time") or "")) if part
        )
        color = palette_for_scene(series, scene_key, index)
        image = draw_sketch(shot, palette_color=color, index=index)
        shot_id = str(shot.get("shot_id") or f"S{index:03d}")
        image.save(out_dir / f"{shot_id}.png", format="PNG")
        images.append(image)
        report = grayscale_contrast(image, _people_count(shot))
        contrast_rows.append({"shot_id": shot_id, **report})
    sheet = contact_sheet(images, title=f"第{int(episode)}集 色块草图 · 38%/62% 黄金位置")
    sheet_path = out_dir / "总表.png"
    sheet.save(sheet_path, format="PNG")
    gray = sheet.convert("L").convert("RGB")
    gray_path = out_dir / "总表_黑白.png"
    gray.save(gray_path, format="PNG")

    palette_path = Path(store.exports_dir) / f"色表_第{int(episode)}集.md"
    lines = [palette_table_md(series), "", f"## 黑白对比检查 · 第{int(episode)}集", ""]
    lines += ["| 镜号 | 人（亮度） | 背景（亮度） | 差值 | 结论 |", "|---|---|---|---|---|"]
    problems = 0
    for row in contrast_rows:
        verdict = "通过" if row["ok"] else "对比不足，重配色"
        if not row["ok"]:
            problems += 1
        lines.append(
            f"| {row['shot_id']} | {row['subject_luma']} | {row['background_luma']} | "
            f"{row['diff']} | {verdict} |"
        )
    lines += [
        "",
        f"> 阈值：亮度差 ≥ {CONTRAST_THRESHOLD:.0f} 视为人和背景分得开。",
        "> 转黑白分不开 = 明暗对比不够，先改配色，不要急着生成。",
    ]
    project_io.write_md(palette_path, "\n".join(lines) + "\n")
    return {
        "dir": out_dir,
        "sheet": sheet_path,
        "gray_sheet": gray_path,
        "palette": palette_path,
        "shots": len(shots),
        "contrast": contrast_rows,
        "problems": problems,
        "summary": (
            f"第{int(episode)}集色块草图 {len(shots)} 张，总表 {sheet_path.name}；"
            + (f"{problems} 镜黑白对比不足，先改配色" if problems else "黑白对比全部通过")
        ),
    }
