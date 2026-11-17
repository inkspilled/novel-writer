"""参考片测量：抽帧 → 让模型逐帧看 → 把机位、进出画比例量出来再模仿。

文章 L123-L126：

```text
最有效的方法就是找参考片……然后让 Claude 逐帧看参考片，让它把机位、进出画
比例这些都量出来，然后学习模仿。没有参考片也没关系，那就让 claude 按下面的
要求自己构思。
```

本模块只做**确定性**部分（不调模型）：

- `probe_duration()`：先读时长，决定抽多少帧；
- `extract_frames()`：用 ffmpeg 按固定间隔抽帧（默认每 0.5 秒一张，最多 120 张）；
- `reference_sheet()`：把抽出的帧拼成一张总表，人一眼能扫完；
- `measurement_prompt()`：把「逐帧看、量机位、量进出画比例、然后模仿」写成
  一份可直接发给导演/画师的提示词，附分镜表；
- `collect_reference()`：一集的参考片整理落 `drama/exports/reference_epNNN/`。

没有 ffmpeg 就明确报缺什么，不静默返回空结果（文章第 2 条：缺什么告诉我）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import project_io
from .logger import get_logger

logger = get_logger(__name__)

REFERENCE_DIR = "reference"
DEFAULT_INTERVAL = 0.5
DEFAULT_MAX_FRAMES = 120
FRAME_WIDTH = 640
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


class ReferenceError(RuntimeError):
    """没有 ffmpeg / 视频不可读 / 抽不出帧。"""


def ffmpeg_binary() -> str:
    return shutil.which("ffmpeg") or ""


def ffprobe_binary() -> str:
    return shutil.which("ffprobe") or ""


def require_ffmpeg() -> str:
    binary = ffmpeg_binary()
    if not binary:
        raise ReferenceError("没装 ffmpeg：参考片抽帧需要 ffmpeg，请先安装并放进 PATH。")
    return binary


def probe_duration(video: str | Path) -> float:
    """读视频时长；ffprobe 优先，没有就解析 ffmpeg 的 stderr。"""
    path = Path(video)
    if not path.is_file():
        raise ReferenceError(f"找不到参考片：{path}")
    probe = ffprobe_binary()
    if probe:
        try:
            proc = subprocess.run(
                [probe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            value = (proc.stdout or "").strip()
            if proc.returncode == 0 and value:
                return max(0.0, float(value))
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    binary = require_ffmpeg()
    try:
        proc = subprocess.run([binary, "-i", str(path)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise ReferenceError("读不出参考片时长：文件可能损坏或不是视频") from None
    match = _DURATION_RE.search((proc.stderr or "") + (proc.stdout or ""))
    if not match:
        raise ReferenceError("读不出参考片时长：文件可能损坏或不是视频")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def extract_frames(
    video: str | Path,
    out_dir: str | Path,
    *,
    every: float = DEFAULT_INTERVAL,
    max_frames: int = DEFAULT_MAX_FRAMES,
    width: int = FRAME_WIDTH,
) -> list[Path]:
    """按间隔抽帧，返回按时间排序的图片路径。"""
    binary = require_ffmpeg()
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    for old in target.glob("frame_*.jpg"):
        try:
            old.unlink()
        except OSError:
            pass
    interval = max(0.05, float(every or DEFAULT_INTERVAL))
    command = [
        binary, "-y", "-i", str(Path(video)),
        "-vf", f"fps=1/{interval}" + (f",scale={int(width)}:-2" if width else ""),
        "-frames:v", str(max(1, int(max_frames))),
        "-q:v", "3",
        str(target / "frame_%04d.jpg"),
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        raise ReferenceError("ffmpeg 抽帧失败：进程无法启动") from None
    frames = sorted(target.glob("frame_*.jpg"))
    if proc.returncode != 0 and not frames:
        raise ReferenceError(f"ffmpeg 抽帧失败：{(proc.stderr or '')[-300:]}")
    return frames


def reference_sheet(frames: list[Path], out_path: str | Path, *, cols: int = 6):
    """帧总表（需要 Pillow）；返回保存路径。"""
    from .drama_sketch import SketchError, contact_sheet

    try:
        from PIL import Image
    except ImportError:
        raise SketchError("没装 Pillow：拼参考片总表需要 `pip install pillow`") from None
    if not frames:
        raise ReferenceError("还没有抽到帧，先 extract_frames()")
    images = []
    for frame in frames:
        try:
            with Image.open(frame) as image:
                images.append(image.convert("RGB"))
        except OSError:
            continue
    if not images:
        raise ReferenceError("抽到的帧都读不出来")
    sheet = contact_sheet(images, cols=max(1, int(cols)), title=f"参考片抽帧 · {len(images)} 张")
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, format="PNG")
    return path


def measurement_table_md(shots: list[dict[str, Any]] | None = None) -> str:
    lines = [
        "| 镜号 | 景别 | 机位 | 运镜 | 进出画比例 | 构图（38%/62%） | 备注 |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, shot in enumerate(shots or [], start=1):
        lines.append(
            "| {sid} | {size} |  |  |  |  |  |".format(
                sid=str(shot.get("shot_id") or f"S{index:03d}"),
                size=str(shot.get("shot_size") or ""),
            )
        )
    if not shots:
        lines.append("|  |  |  |  |  |  |  |")
    return "\n".join(lines)


def measurement_prompt(
    video: str | Path,
    frames: list[Path] | None = None,
    shots: list[dict[str, Any]] | None = None,
) -> str:
    """「逐帧看参考片、量机位、量进出画比例、然后模仿」的提示词。"""
    frame_hint = ""
    if frames:
        frame_hint = f"\n抽帧：`{Path(frames[0]).parent}`（共 {len(frames)} 张，按时间排序，我拼了一张总表）。"
    return "\n".join([
        f"参考片：{Path(video)}。{frame_hint}",
        "",
        "先逐帧看参考片，把机位、进出画比例这些都量出来，然后学习模仿。",
        "",
        "要求：",
        "1. 逐帧看，不要只看缩略图；一张一张说清楚镜头里发生了什么。",
        "2. 量出每个镜头的机位（平视/俯拍/仰拍/背后/过肩/透过东西拍）、景别、运镜。",
        "3. 量出人物进出画的比例：从画面左边/右边第几成位置进、走到第几成位置。",
        "4. 量出构图：头和眼睛落在画面横向大约 38% 还是 62%。",
        "5. 把量到的结果填进下表，再按这张表排我们自己的分镜；不要凭印象写。",
        "",
        "## 测量表",
        "",
        measurement_table_md(shots),
        "",
        "如果某一条量不出来，写「量不出」并说明原因，不要编。",
    ])


def collect_reference(
    store,
    episode: int,
    video: str | Path,
    *,
    every: float = DEFAULT_INTERVAL,
    max_frames: int = DEFAULT_MAX_FRAMES,
) -> dict[str, Any]:
    """整理一集的参考片：抽帧 + 总表 + 测量提示词。"""
    from .drama_production import stamp
    out_dir = Path(store.exports_dir) / f"reference_ep{int(episode):03d}" / stamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(video)
    frames = extract_frames(video, out_dir / "frames", every=every, max_frames=max_frames)
    sheet = ""
    try:
        sheet = str(reference_sheet(frames, out_dir / "总表.png"))
    except Exception as exc:  # Pillow 缺失不该让整个测量失败
        logger.warning("参考片总表没拼成：%s", exc)
    shots = list(store.load_storyboard(int(episode)).get("shots") or [])
    prompt_path = out_dir / "参考片测量.md"
    project_io.write_md(
        prompt_path,
        "# 参考片测量\n\n" + measurement_prompt(video, frames, shots) + "\n",
    )
    return {
        "dir": out_dir,
        "frames": frames,
        "sheet": sheet,
        "prompt": prompt_path,
        "duration": duration,
        "summary": (
            f"参考片 {duration:.1f}s，抽 {len(frames)} 帧 → {out_dir}；"
            f"测量提示词已写 {prompt_path.name}"
        ),
    }
