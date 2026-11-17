"""可执行后期流水线：归一化、修复、按原曲卡点、转场、配乐与交付。"""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from . import project_io
from .drama_production import stamp
from .drama_production import file_digest


def validate_options(value):
    if not isinstance(value, dict):
        raise ValueError("需要 JSON 对象")
    allowed = {"transitions", "transition_kind", "transition_seconds", "grade", "to_709",
               "upscale", "repair", "fps", "use_source_audio"}
    if set(value) - allowed:
        raise ValueError("未知参数：" + ", ".join(sorted(set(value) - allowed)))
    for key in ("transitions", "grade", "to_709", "upscale", "use_source_audio"):
        if key in value and not isinstance(value[key], bool):
            raise ValueError(f"{key} 必须是布尔值")
    for key, low, high in (("fps", 1, 60), ("transition_seconds", .05, 2)):
        number = value.get(key, 24 if key == "fps" else .4)
        if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number) or not low <= number <= high:
            raise ValueError(f"{key} 范围 {low}–{high}")
    if int(value.get("fps", 24)) != value.get("fps", 24):
        raise ValueError("fps 必须为整数")
    repair = value.get("repair", {})
    if not isinstance(repair, dict) or set(repair) - {"shots", "drop_first", "freeze_tail", "rebalance"}:
        raise ValueError("repair 支持 shots/drop_first/freeze_tail/rebalance")
    for key in ("drop_first", "freeze_tail", "rebalance"):
        if key in repair and not isinstance(repair[key], bool):
            raise ValueError(f"repair.{key} 必须是布尔值")
    if repair and (not isinstance(repair.get("shots"), list) or not repair["shots"]
                   or not all(isinstance(s, str) for s in repair["shots"])):
        raise ValueError("repair 必须指定非空镜号列表 shots，避免修错镜头")


def run_episode(store, episode, *, transitions=False, transition_kind="fade", transition_seconds=.4,
                grade=True, to_709=False, upscale=True, music=None, repair=None, fps=24,
                use_source_audio=False, stopped=None):
    from . import drama_post as post
    options = dict(transitions=transitions, transition_kind=transition_kind,
                   transition_seconds=transition_seconds, grade=grade, to_709=to_709,
                   upscale=upscale, repair=repair or {}, fps=fps, use_source_audio=use_source_audio)
    validate_options(options)
    if transition_kind not in post.XFADE_KINDS | {"motion_blur"}:
        raise ValueError("不支持的转场类型")
    binary = post.require_ffmpeg()
    probe = post.ffprobe_binary()
    if not probe:
        raise post.PostError("后期需要 ffprobe 读取视频尺寸、时长和音轨")
    episode = int(episode)
    series = store.load_series()
    shots = store.load_storyboard(episode)["shots"]
    clips = [store.episode_clip_dir(episode) / f"{s['shot_id']}.mp4" for s in shots]
    if not shots or any(not p.is_file() for p in clips):
        raise post.PostError("后期需要本集全部镜头片段；缺镜时先补齐，不能静默跳镜")
    repair = repair or {}
    if set(repair.get("shots", [])) - {s["shot_id"] for s in shots}:
        raise ValueError("repair.shots 含本集不存在的镜号")
    out_dir = store.videos_dir / f"ep{episode:03d}_post" / stamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"episode": episode, "ok": False, "output": "", "steps": [], "cuts": [],
              "cut_frames": [], "options": options, "beat": {},
              "limitations": "静态抽帧无法判断动态手感与声音；请播放确认。调色未做语义肤色/强调色蒙版。"}
    report["source_fingerprints"] = {p.name:file_digest(p) for p in clips}

    def execute(args, name):
        ok = post.run(args, stopped=stopped)
        report["steps"].append({"name": name, "ok": ok})
        if not ok:
            project_io.write_json(out_dir / "report.json", report)
            raise post.PostError(f"{name} 失败；已完成产物保留在 {out_dir}")

    def info(path):
        proc = subprocess.run([probe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if proc.returncode:
            raise post.PostError(f"无法读取片段：{path.name}")
        return json.loads(proc.stdout)

    aspect_w, aspect_h = map(int, series.get("aspect", "9:16").split(":"))
    height = 1920 if aspect_h > aspect_w else 1080
    width = round(height * aspect_w / aspect_h / 2) * 2
    if aspect_h == aspect_w:
        width = height = 1080
    if not upscale:
        video = next(s for s in info(clips[0])["streams"] if s["codec_type"] == "video")
        width, height = video["width"], video["height"]
    # 拍点从未剪辑的原曲测量，并实际改变片段目标时长。
    durations = [float(s.get("duration_sec") or 3) for s in shots]
    if music:
        report["beat"] = post.analyze_music(music)
        cumulative, total = [], 0
        for duration in durations[:-1]:
            total += duration
            cumulative.append(total)
        cuts = post.snap_to_beats(cumulative, report["beat"]["beats"])
        edges = [0, *cuts, sum(durations)]
        durations = [b-a for a, b in zip(edges, edges[1:])]
    overlap = min(transition_seconds, min(durations)/3) if transitions and len(clips) > 1 else 0
    if overlap:
        # 补偿转场的重叠，成片切点仍在规划时间上。
        durations = [d + (overlap if i < len(clips)-1 else 0) for i, d in enumerate(durations)]
    normalized = []
    for index, (clip, shot, target) in enumerate(zip(clips, shots, durations)):
        metadata = info(clip)
        source_duration = float(metadata["format"]["duration"])
        if source_duration <= 0:
            raise post.PostError(f"片段时长无效：{clip.name}")
        vf = []
        if to_709:
            vf.append("scale=in_color_matrix=bt601:out_color_matrix=bt709")
        if shot["shot_id"] in repair.get("shots", []):
            if repair.get("drop_first"):
                vf.append(f"trim=start={1/fps:.6f}")
                source_duration -= 1/fps
            if repair.get("freeze_tail"):
                freeze_at = max(.05, source_duration-.2)
                vf.extend([f"trim=duration={freeze_at:.6f}", "setpts=PTS-STARTPTS",
                           "tpad=stop_mode=clone:stop_duration=0.2"])
            if repair.get("rebalance"):
                from PIL import Image, ImageStat
                luminances = []
                for label, at in (("first", 0), ("last", max(0, source_duration-.1))):
                    frame = out_dir / f"{index}_{label}.png"
                    execute(post.extract_frame_args(clip, frame, at=at), f"{shot['shot_id']} 调色取样")
                    with Image.open(frame) as im:
                        luminances.append(ImageStat.Stat(im.convert("L")).mean[0])
                change = max(-.05, min(.05, (luminances[0]-luminances[1])/255))
                vf.append(f"eq=brightness='{change:.6f}*t/{source_duration:.6f}':eval=frame")
        vf.extend(["setpts=PTS-STARTPTS", f"trim=duration={min(target, source_duration):.6f}",
                   f"tpad=stop_mode=clone:stop_duration={max(0,target-source_duration):.6f}",
                   f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos",
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2", "setsar=1", f"fps={fps}",
                   f"settb=1/{fps}"])
        if grade:
            vf.append("eq=saturation=1.02:brightness=0.01")
        if upscale:
            vf.append("unsharp=5:5:0.3:5:5:0")
        normalized_path = out_dir / f"{index:03d}.mkv"
        audio = use_source_audio and any(s["codec_type"] == "audio" for s in metadata["streams"])
        args = [binary, "-y", "-i", str(clip), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                "-map", "0:v:0", "-map", "0:a:0" if audio else "1:a:0", "-vf", ",".join(vf),
                "-af", f"apad,atrim=duration={target:.6f},asetpts=PTS-STARTPTS,aresample=48000",
                "-t", f"{target:.6f}", "-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv420p",
                "-c:a", "pcm_s16le", "-ac", "2", str(normalized_path)]
        execute(args, f"{shot['shot_id']} 归一化与修复（无损中间文件）")
        normalized.append(normalized_path)
    joined = out_dir / "joined.mkv"
    if overlap:
        args = [binary, "-y", "-filter_complex_threads", "1"]
        for clip in normalized:
            args += ["-i", str(clip)]
        filters, total, v, a = [], 0, "[0:v]", "[0:a]"
        for index in range(1, len(normalized)):
            total += durations[index-1]-overlap
            kind = "fade" if transition_kind == "motion_blur" else transition_kind
            filters.append(f"{v}[{index}:v]xfade=transition={kind}:duration={overlap}:offset={total:.6f}[v{index}]")
            filters.append(f"{a}[{index}:a]acrossfade=d={overlap}[a{index}]")
            v, a = f"[v{index}]", f"[a{index}]"
            report["cuts"].append(total)
        if transition_kind == "motion_blur":
            # 只在切点附近混合帧，避免重复整段镜头。
            enables = "+".join(f"between(t,{max(0,t-overlap/2):.6f},{t+overlap/2:.6f})" for t in report["cuts"])
            filters.append(f"{v}tmix=frames=12:enable='{enables}'[blur]")
            v = "[blur]"
        args += ["-filter_complex", ";".join(filters), "-map", v, "-map", a,
                 "-c:v", "ffv1", "-c:a", "pcm_s16le", str(joined)]
        execute(args, "视频与音频转场")
    else:
        listing = post.write_concat_list(normalized, out_dir / "concat.txt")
        execute(post.concat_args(normalized, joined, listing=listing), "拼接")
        report["cuts"] = [sum(durations[:index]) for index in range(1, len(durations))]
    final = out_dir / f"ep{episode:03d}_final.mp4"
    args = [binary, "-y", "-i", str(joined)]
    if music:
        args += ["-i", str(Path(music)), "-map", "0:v", "-map", "1:a:0", "-af", "apad"]
    else:
        args += ["-map", "0:v", "-map", "0:a"]
    filters = []
    if series.get("subtitles"):
        def timecode(seconds):
            ms = int(round(seconds*1000))
            return f"{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}"
        srt, start = [], 0
        for index, (shot, duration) in enumerate(zip(shots, durations)):
            end = start+duration-(overlap if index < len(shots)-1 else 0)
            if str(shot.get("dialogue") or "").strip():
                srt.append(f"{len(srt)+1}\n{timecode(start)} --> {timecode(end)}\n{shot['dialogue']}\n")
            start = end
        subtitle = out_dir / "subtitles.srt"
        subtitle.write_text("\n".join(srt), encoding="utf-8")
        escaped = str(subtitle.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")
        filters.append(f"subtitles=filename='{escaped}'")
    if filters:
        args += ["-vf", ",".join(filters)]
    args += ["-t", f"{sum(durations)-overlap*(len(shots)-1):.6f}", "-c:v", "libx264", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"]
    if to_709:
        args += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    args.append(str(final))
    execute(args, "交付编码与配乐/字幕")
    frames = out_dir / "cut_frames"
    frames.mkdir()
    for at in report["cuts"]:
        execute(post.cut_frames_args(final, frames, at=at, fps=fps), "切点前后各三帧")
    report.update(ok=True, output=str(final), cut_frames=[str(p) for p in frames.glob("*.png")])
    project_io.write_json(out_dir / "report.json", report)
    project_io.write_md(out_dir / "后期报告.md", "# 后期报告\n\n" + json.dumps(report, ensure_ascii=False, indent=2))
    return report
