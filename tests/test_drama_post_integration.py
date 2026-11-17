"""真实 ffmpeg/ffprobe 的离线短片验证；环境未安装时跳过。"""
import json
import shutil
import subprocess

import pytest

from src.novel_writer.core.drama import DramaStore
from src.novel_writer.core.drama_post import process_episode


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="需要 ffmpeg/ffprobe")
@pytest.mark.parametrize("kind", ["hard", "fade", "motion_blur"])
def test_real_post_keeps_portrait_timing_audio_and_versions(tmp_path, kind):
    store = DramaStore(tmp_path)
    store.ensure({"title":"后期"})
    store.save_series({"aspect":"9:16", "subtitles":True})
    store.save_script(1, "## 第1场 房间 · 日\n顾言：走。")
    shots = [{"shot_id":f"E01-S00{i+1}", "duration_sec":1, "dialogue":"顾言：走。"} for i in range(3)]
    store.save_storyboard(1, {"shots":shots})
    for index, color in enumerate(("red", "green", "blue")):
        args = [shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", f"color=c={color}:s=90x160:r=24",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1.3",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                str(store.episode_clip_dir(1) / f"E01-S00{index+1}.mp4")]
        subprocess.run(args, capture_output=True, check=True)
    report = process_episode(store, 1, transitions=kind != "hard", transition_kind="fade" if kind == "hard" else kind,
                             upscale=False, use_source_audio=True, grade=False,
                             repair={"shots":["E01-S001"], "drop_first":True, "freeze_tail":True})
    assert report["ok"] and len(report["cuts"]) == 2
    assert len(report["cut_frames"]) == 14
    proc = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", report["output"]],
                          capture_output=True, text=True, check=True)
    metadata = json.loads(proc.stdout)
    video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (90,160)
    assert abs(float(metadata["format"]["duration"])-3) < .15
    assert any(s["codec_type"] == "audio" for s in metadata["streams"])
    assert (store.episode_clip_dir(1) / "E01-S001.mp4").is_file()


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="需要 ffmpeg/ffprobe")
def test_upscale_delivers_portrait_1080_with_original_music(tmp_path):
    store = DramaStore(tmp_path)
    store.ensure({"title":"竖屏"})
    store.save_series({"aspect":"9:16"})
    store.save_storyboard(1, {"shots":[{"shot_id":"E01-S001", "duration_sec":.5}]})
    clip = store.episode_clip_dir(1)/"E01-S001.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", "color=c=red:s=90x160:r=24", "-t", "0.6", str(clip)],
                   capture_output=True, check=True)
    music = tmp_path / "original.wav"
    subprocess.run([shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1", str(music)],
                   capture_output=True, check=True)
    report = process_episode(store, 1, music=music)
    proc = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_streams", "-of", "json", report["output"]],
                          capture_output=True, text=True, check=True)
    video = next(s for s in json.loads(proc.stdout)["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080,1920)
