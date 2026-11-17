"""后期：转场、调色、BT.601→BT.709、放大、卡点、切点抽帧、先修再重拍。

文章 L301-L312 的六条＋L297-L300 的「先修再重拍」，在这里落成 ffmpeg 参数构造器
（纯函数，可单测）＋执行器（没有 ffmpeg 就明确说缺什么，不静默失败）：

1. 转场：运动模糊切 8–16 份、半帧快门；
2. 调色：按故事分段，只调彩度和亮度，不动色相；不加颗粒、柔光、暗角；
3. Seedance 的 mp4 按 BT.601 解码，中间文件用无损格式，交付标 BT.709；
4. 放大：720p 用 lanczos 放大到 1080p，加轻微锐化，不整段插帧；
5. 按小节卡点：拍点只从原曲上测，不要在剪过的文件上测；
6. 抽查切点前后 3 帧。

修复优先级（文章规矩 4）：能剪掉的不重拍、能用代码修的不重拍、非拍不可只拍这一镜。
对应 `trim_clip` / `drop_first_frames` / `freeze_tail` / `rebalance`。
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any, Iterable

from . import project_io
from .logger import get_logger

logger = get_logger(__name__)

DELIVERY_W, DELIVERY_H = 1920, 1080
DEFAULT_FPS = 24
DEFAULT_TRANSITION = 0.4
DEFAULT_SLICES = 12
POST_DIR = "post"

# xfade 支持的转场名（ffmpeg 内置）；「运动模糊」单独走 blend 分支
XFADE_KINDS = {
    "fade", "fadeblack", "fadewhite", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown", "circleopen", "circleclose",
    "dissolve", "pixelize", "radial", "smoothleft", "smoothright",
}


class PostError(RuntimeError):
    """ffmpeg 缺失或后期参数非法。"""


def ffmpeg_binary() -> str:
    return shutil.which("ffmpeg") or ""


def ffprobe_binary() -> str:
    return shutil.which("ffprobe") or ""


def require_ffmpeg() -> str:
    binary = ffmpeg_binary()
    if not binary:
        raise PostError(
            "没装 ffmpeg：转场/调色/BT.601→709/放大/卡点/切点抽帧都用不了；"
            "先安装 ffmpeg 并放进 PATH。"
        )
    return binary


def run(args: list[str], *, timeout: float = 1800.0, stopped=None) -> bool:
    """跑一条 ffmpeg 命令；失败只记日志，不抛（后期可以整体重跑）。"""
    try:
        if stopped is None:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                  encoding="utf-8", errors="replace")
        else:
            import time
            with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8", errors="replace") as child:
                deadline = time.monotonic() + timeout
                while True:
                    if stopped() or time.monotonic() >= deadline:
                        child.kill()
                        child.communicate()
                        return False
                    try:
                        stdout, stderr = child.communicate(timeout=.2)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                proc = subprocess.CompletedProcess(args, child.returncode, stdout, stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ffmpeg 执行失败：%s", exc)
        return False
    if proc.returncode != 0:
        logger.warning("ffmpeg 返回 %s：%s", proc.returncode, (proc.stderr or "")[-400:])
        return False
    return True


def probe_duration(video: str | Path) -> float:
    """读时长；ffprobe 优先，没有就解析 ffmpeg stderr。"""
    path = Path(video)
    if not path.is_file():
        return 0.0
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
    binary = ffmpeg_binary()
    if not binary:
        return 0.0
    try:
        proc = subprocess.run([binary, "-i", str(path)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return 0.0
    import re

    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", (proc.stderr or ""))
    if not match:
        return 0.0
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


# ── 参数构造器（纯函数）──


def concat_args(clips: Iterable[str | Path], output: str | Path, *, listing: str | Path | None = None) -> list[str]:
    """顺序拼接：concat demuxer + 流拷贝（同编码片段最快）。"""
    clips = [Path(clip) for clip in clips]
    out = Path(output)
    list_path = Path(listing) if listing else out.with_suffix(".txt")
    return [
        ffmpeg_binary() or "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_path), "-c", "copy", str(out),
    ]


def write_concat_list(clips: Iterable[str | Path], listing: str | Path) -> Path:
    path = Path(listing)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for clip in clips:
        text = str(Path(clip).resolve()).replace("'", "'\\''")
        lines.append(f"file '{text}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _transition_filter(seconds: float, slices: int, half_shutter: bool) -> str:
    """运动模糊过渡：N 份混合 + 半帧快门（两帧平均近似）。"""
    slices = max(8, min(16, int(slices or DEFAULT_SLICES)))
    blur = f"tmix=frames={slices}"
    if half_shutter:
        blur = "tmix=frames=2:weights='1 1'," + blur
    return f"{blur},format=yuv420p"


def transition_args(
    clip_a: str | Path,
    clip_b: str | Path,
    output: str | Path,
    *,
    kind: str = "motion_blur",
    seconds: float = DEFAULT_TRANSITION,
    slices: int = DEFAULT_SLICES,
    half_shutter: bool = True,
    fps: int = DEFAULT_FPS,
) -> list[str]:
    """两段之间的转场。

    - `motion_blur`：文章原话「运动模糊切 8–16 份，半帧快门」——
      前后各取 `seconds` 秒，用 `blend` 按时间加权混合，再用 `tmix` 把
      8–16 帧叠出运动模糊；半帧快门用两帧平均近似（真快门要在拍摄端拿）。
    - 其余走 `xfade`：fade / wipe / slide / circle 等。
    - `hard`：直接拼，不改画面。
    """
    out = Path(output)
    duration = max(0.1, float(seconds or DEFAULT_TRANSITION))
    if kind == "hard":
        return concat_args([clip_a, clip_b], out)
    if kind == "motion_blur":
        filter_complex = (
            f"[0:v]trim=start=0,setpts=PTS-STARTPTS,{_transition_filter(duration, slices, half_shutter)}[a];"
            f"[1:v]trim=duration={duration:.3f},setpts=PTS-STARTPTS,"
            f"{_transition_filter(duration, slices, half_shutter)}[b];"
            f"[a][b]blend=all_expr='A*(1-T/{duration:.3f})+B*(T/{duration:.3f})'[v]"
        )
        return [
            ffmpeg_binary() or "ffmpeg", "-y", "-i", str(Path(clip_a)), "-i", str(Path(clip_b)),
            "-filter_complex", filter_complex, "-map", "[v]",
            "-r", str(int(fps)), "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
            str(out),
        ]
    transition = kind if kind in XFADE_KINDS else "fade"
    filter_complex = (
        f"[0:v][1:v]xfade=transition={transition}:duration={duration:.3f}:offset=0[v]"
    )
    return [
        ffmpeg_binary() or "ffmpeg", "-y", "-i", str(Path(clip_a)), "-i", str(Path(clip_b)),
        "-filter_complex", filter_complex, "-map", "[v]",
        "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out),
    ]


def transition_chain_args(
    clips: list[str | Path],
    output: str | Path,
    *,
    durations: list[float] | None = None,
    kind: str = "fade",
    seconds: float = DEFAULT_TRANSITION,
    fps: int = DEFAULT_FPS,
) -> list[str]:
    """整集转场链：`[0][1]xfade -> [v01]`，再 `[v01][2]xfade` ……"""
    out = Path(output)
    if len(clips) == 1:
        return [ffmpeg_binary() or "ffmpeg", "-y", "-i", str(Path(clips[0])),
                "-c", "copy", str(out)]
    durations = list(durations or [])
    while len(durations) < len(clips):
        durations.append(seconds)
    parts: list[str] = []
    label = "[0:v]"
    offset = 0.0
    for index in range(1, len(clips)):
        offset += max(0.0, durations[index - 1]) - max(0.0, seconds)
        next_label = f"[v{index:02d}]"
        transition = kind if kind in XFADE_KINDS else "fade"
        parts.append(
            f"{label}[{index}:v]xfade=transition={transition}:duration={seconds:.3f}:"
            f"offset={max(0.0, offset):.3f}{next_label}"
        )
        label = next_label
    args: list[str] = [ffmpeg_binary() or "ffmpeg", "-y"]
    for clip in clips:
        args += ["-i", str(Path(clip))]
    args += ["-filter_complex", ";".join(parts), "-map", label,
             "-r", str(int(fps)), "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
             str(out)]
    return args


def grade_args(
    src: str | Path,
    output: str | Path,
    *,
    saturation: float = 1.04,
    brightness: float = 0.02,
    contrast: float = 1.0,
) -> list[str]:
    """调色：只动彩度和亮度（eq），不动色相、不加颗粒/柔光/暗角。

    强调色与肤色保护靠**限制增益**实现：饱和度上限 1.08、亮度上限 ±0.05，
    超过这个范围就不是「调」而是「改色」了。
    """
    saturation = min(1.08, max(0.92, float(saturation)))
    brightness = min(0.05, max(-0.05, float(brightness)))
    chain = f"eq=saturation={saturation:.3f}:brightness={brightness:.3f}:contrast={contrast:.3f}"
    return [
        ffmpeg_binary() or "ffmpeg", "-y", "-i", str(Path(src)),
        "-vf", chain, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
        "-c:a", "copy", str(Path(output)),
    ]


def bt709_args(src: str | Path, output: str | Path) -> list[str]:
    """按 BT.601 解码，输出标 BT.709（交付标准）。"""
    return [
        ffmpeg_binary() or "ffmpeg", "-y", "-i", str(Path(src)),
        "-vf", "scale=in_color_matrix=bt601:out_color_matrix=bt709:in_range=tv:out_range=tv,"
               "format=yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv", "-c:v", "libx264", "-crf", "16", "-c:a", "copy",
        str(Path(output)),
    ]


def upscale_args(
    src: str | Path,
    output: str | Path,
    *,
    width: int = DELIVERY_W,
    height: int = DELIVERY_H,
    sharpen: float = 0.5,
) -> list[str]:
    """720p → 1080p：lanczos + 轻微锐化，不整段插帧。"""
    chain = f"scale={int(width)}:{int(height)}:flags=lanczos"
    if sharpen > 0:
        chain += f",unsharp=5:5:{float(sharpen):.2f}:5:5:0"
    return [
        ffmpeg_binary() or "ffmpeg", "-y", "-i", str(Path(src)),
        "-vf", chain, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
        "-c:a", "copy", str(Path(output)),
    ]


def trim_args(
    src: str | Path,
    output: str | Path,
    *,
    start: float = 0.0,
    end: float | None = None,
    accurate: bool = True,
) -> list[str]:
    """剪掉有问题的开头/结尾：问题只出现在头几帧或尾几帧时，先剪不重拍。"""
    args = [ffmpeg_binary() or "ffmpeg", "-y"]
    if accurate:
        args += ["-ss", f"{max(0.0, float(start)):.3f}"]
        if end is not None:
            args += ["-to", f"{float(end):.3f}"]
    args += ["-i", str(Path(src))]
    if not accurate:
        if start:
            args += ["-ss", f"{max(0.0, float(start)):.3f}"]
        if end is not None:
            args += ["-to", f"{float(end):.3f}"]
    args += ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", str(Path(output))]
    return args


def drop_first_frames_args(src: str | Path, output: str | Path, *, frames: int = 1) -> list[str]:
    """第一张画面特别锐（Seedance 2.5）：直接把这一张去掉。"""
    frames = max(1, int(frames))
    chain = f"trim=start_frame={frames},setpts=PTS-STARTPTS"
    return [
        ffmpeg_binary() or "ffmpeg", "-y", "-i", str(Path(src)),
        "-vf", chain, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(Path(output)),
    ]


def freeze_tail_args(
    src: str | Path,
    output: str | Path,
    *,
    hold: float = 0.15,
    at: float | None = None,
) -> list[str]:
    """结尾几帧突然跳到尾帧：选一张没问题的画面，后面都停在这一张。

    `at=None` 表示停在最后一张（只延长 `hold` 秒）；给 `at` 就从该时间点起冻结。
    """
    hold = max(0.05, float(hold))
    if at is None:
        chain = f"tpad=stop_mode=clone:stop_duration={hold:.3f}"
        args = [ffmpeg_binary() or "ffmpeg", "-y", "-i", str(Path(src)), "-vf", chain]
    else:
        chain = f"trim=end={max(0.0, float(at)):.3f},setpts=PTS-STARTPTS," \
                f"tpad=stop_mode=clone:stop_duration={hold:.3f}"
        args = [ffmpeg_binary() or "ffmpeg", "-y", "-i", str(Path(src)), "-vf", chain]
    args += ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", str(Path(output))]
    return args


def rebalance_args(
    src: str | Path,
    output: str | Path,
    *,
    brightness: float = 0.0,
    contrast: float = 1.0,
    saturation: float = 1.0,
) -> list[str]:
    """按首帧把颜色调回来（视频越到后面越暗越冷时用）。"""
    chain = (
        f"eq=brightness={max(-0.2, min(0.2, float(brightness))):.3f}:"
        f"contrast={max(0.8, min(1.2, float(contrast))):.3f}:"
        f"saturation={max(0.8, min(1.2, float(saturation))):.3f}"
    )
    return [
        ffmpeg_binary() or "ffmpeg", "-y", "-i", str(Path(src)),
        "-vf", chain, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(Path(output)),
    ]


def extract_frame_args(src: str | Path, output: str | Path, *, at: float = 0.0) -> list[str]:
    return [
        ffmpeg_binary() or "ffmpeg", "-y", "-ss", f"{max(0.0, float(at)):.3f}",
        "-i", str(Path(src)), "-frames:v", "1", "-q:v", "2", str(Path(output)),
    ]


def cut_frames_args(
    src: str | Path,
    output_dir: str | Path,
    *,
    at: float,
    around: int = 3,
    fps: int = DEFAULT_FPS,
) -> list[str]:
    """切点前后各取 3 帧：从 `at - around/fps` 起连取 2*around+1 帧。

    产物名固定 `cut_{frame index}_%02d.png`，交给人工或复核 agent 看。
    """
    around = max(1, int(around))
    start = max(0.0, float(at) - around / max(1, fps))
    out = Path(output_dir) / f"cut_{int(round(float(at) * 1000)):06d}_%02d.png"
    return [
        ffmpeg_binary() or "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(Path(src)),
        "-frames:v", str(2 * around + 1), "-q:v", "2", "-fps_mode", "passthrough", str(out),
    ]


# ── 音频卡点（纯 Python，不依赖 numpy）──


def decode_wav(music: str | Path, *, sample_rate: int = 8000) -> tuple[bytes, int, int]:
    """用 ffmpeg 把音乐解成单声道 16-bit WAV，返回 (PCM 数据, 采样率, 声道数)。"""
    binary = require_ffmpeg()
    path = Path(music)
    if not path.is_file():
        raise PostError(f"找不到音乐：{path}")
    command = [
        binary, "-v", "error", "-i", str(path),
        "-ac", "1", "-ar", str(int(sample_rate)), "-f", "wav", "pipe:1",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PostError(f"解码音乐失败：{exc}") from None
    if proc.returncode != 0 or not proc.stdout:
        raise PostError("解码音乐失败：ffmpeg 没有输出音频")
    import io

    with wave.open(io.BytesIO(proc.stdout), "rb") as reader:
        channels = reader.getnchannels()
        rate = reader.getframerate()
        frames = reader.readframes(reader.getnframes())
    return frames, rate, channels


def detect_beats(
    pcm: bytes,
    sample_rate: int = 8000,
    *,
    window: int = 256,
    min_gap: float = 0.22,
) -> list[float]:
    """能量上升沿找拍点：纯 Python，够用且可复现（拍点只从原曲上测）。"""
    if not pcm:
        return []
    samples = struct.unpack("<%dh" % (len(pcm) // 2), pcm[: len(pcm) // 2 * 2])
    energies: list[float] = []
    for start in range(0, len(samples) - window, window):
        chunk = samples[start:start + window]
        energies.append(sum(value * value for value in chunk) / window)
    if not energies:
        return []
    flux = [0.0]
    for index in range(1, len(energies)):
        flux.append(max(0.0, energies[index] - energies[index - 1]))
    peak = max(flux) or 1.0
    normalized = [value / peak for value in flux]
    mean = sum(normalized) / len(normalized)
    variance = sum((value - mean) ** 2 for value in normalized) / len(normalized)
    threshold = mean + 0.6 * (variance ** 0.5)
    hop_seconds = window / max(1, sample_rate)
    beats: list[float] = []
    last = -1e9
    for index, value in enumerate(normalized):
        if value < threshold:
            continue
        time_sec = index * hop_seconds
        if time_sec - last < min_gap:
            if beats and value > normalized[int(beats[-1] / hop_seconds)]:
                beats[-1] = round(time_sec, 3)
                last = time_sec
            continue
        beats.append(round(time_sec, 3))
        last = time_sec
    return beats


def estimate_bpm(beats: list[float]) -> float:
    """用相邻拍点中位数估 BPM（60 / 中位间隔），给卡点一个整数参考。"""
    if len(beats) < 2:
        return 0.0
    gaps = [b - a for a, b in zip(beats, beats[1:]) if b - a > 0.05]
    if not gaps:
        return 0.0
    gaps.sort()
    median = gaps[len(gaps) // 2]
    bpm = 60.0 / median
    while bpm < 60:
        bpm *= 2
    while bpm > 180:
        bpm /= 2
    return round(bpm, 1)


def analyze_music(music: str | Path) -> dict[str, Any]:
    pcm, rate, _channels = decode_wav(music)
    beats = detect_beats(pcm, rate)
    return {"beats": beats, "bpm": estimate_bpm(beats), "duration": len(pcm) / 2 / max(1, rate)}


def snap_to_beats(times: list[float], beats: list[float], *, tolerance: float = 0.12) -> list[float]:
    """把切点吸到最近的拍点（容差内），保证「按小节卡点」。"""
    if not beats:
        return list(times)
    snapped: list[float] = []
    for time_sec in times:
        nearest = min(beats, key=lambda beat: abs(beat - time_sec))
        snapped.append(nearest if abs(nearest - time_sec) <= tolerance else time_sec)
    return snapped


def cut_plan(clips: list[str | Path], beats: list[float], *, cumulative: bool = True) -> list[float]:
    """按片段时长累计，给出每个切点时间，并吸到拍点。"""
    times: list[float] = []
    total = 0.0
    for index, clip in enumerate(clips):
        if index == 0:
            continue
        total += probe_duration(clips[index - 1])
        times.append(total)
    return snap_to_beats(times, beats) if cumulative else times


# ── 执行 ──


def _post_dir(store, episode: int) -> Path:
    path = Path(store.videos_dir) / f"ep{int(episode):03d}_{POST_DIR}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def clip_paths(store, episode: int) -> list[Path]:
    """按分镜顺序取已有片段；缺的跳过（不拿别的镜凑）。"""
    shots = list(store.load_storyboard(int(episode)).get("shots") or [])
    folder = Path(store.episode_clip_dir(int(episode)))
    paths: list[Path] = []
    for index, shot in enumerate(shots, start=1):
        shot_id = str(shot.get("shot_id") or f"S{index:03d}")
        path = folder / f"{shot_id}.mp4"
        if path.is_file():
            paths.append(path)
    if not paths and folder.is_dir():
        paths = sorted(folder.glob("*.mp4"))
    return paths


def validate_options(value):
    from .drama_post_pipeline import validate_options as validate
    return validate(value)


def process_episode(store, episode, **options):
    from .drama_post_pipeline import run_episode
    return run_episode(store, episode, **options)
