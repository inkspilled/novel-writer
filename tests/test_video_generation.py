"""视频生成 API：ComfyUI 视频工作流、配置、片段合成。"""
from __future__ import annotations

import json

import pytest

from src.novel_writer.core.comfyui import ComfyError
from src.novel_writer.core.video_generation import (
    ComfyVideoClient,
    concat_clips,
    config_from_app,
    configuration_error,
)


def _workflow() -> dict:
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": "{{first_frame}}"}},
        "2": {"class_type": "LoadImage", "inputs": {"image": "{{last_frame}}"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{positive}}"}},
        "4": {"class_type": "KSampler",
              "inputs": {"seed": "{{seed}}", "fps": "{{fps}}", "length": "{{length}}"}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": "{{width}}", "height": "{{height}}"}},
        "6": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "{{prefix}}"}},
    }


@pytest.fixture()
def cfg(tmp_path):
    path = tmp_path / "video_api.json"
    path.write_text(json.dumps(_workflow(), ensure_ascii=False), encoding="utf-8")
    return {
        "provider": "comfyui", "base_url": "http://127.0.0.1:8188",
        "workflow": str(path), "timeout": 30, "fps": 16, "length": 3.0,
    }


def test_config_defaults_and_validation():
    cfg = config_from_app({"video_generation": {"workflow": "w.json", "fps": 24, "length": 4}})
    assert cfg["workflow"] == "w.json"
    assert cfg["fps"] == 24 and cfg["length"] == 4.0
    assert configuration_error(cfg) == ""
    assert "工作流" in configuration_error({**cfg, "workflow": ""})
    assert "ComfyUI" in configuration_error({**cfg, "provider": "api"})


def test_generate_fills_video_placeholders_and_returns_clip(cfg, monkeypatch):
    client = ComfyVideoClient(cfg)
    captured: dict = {}
    client.client.upload_image = lambda path: f"uploaded/{path}"
    client.client._post_json = lambda path, payload: (captured.update(payload), {"prompt_id": "p1"})[1]
    client.client._get_json = lambda path: {
        "p1": {
            "status": {"completed": True},
            "outputs": {"9": {"gifs": [{"filename": "clip.mp4", "subfolder": "", "type": "output"}]}},
        }
    }
    client.client._get_bytes = lambda path, params: b"MP4DATA"

    data, filename = client.generate(
        "顾言朝画面左走两步", "first.png", "last.png",
        prefix="E01-S001", width=720, height=1280, seed=7, length=3.5,
    )
    prompt = captured["prompt"]
    assert prompt["1"]["inputs"]["image"] == "uploaded/first.png"
    assert prompt["2"]["inputs"]["image"] == "uploaded/last.png"
    assert prompt["3"]["inputs"]["text"] == "顾言朝画面左走两步"
    assert prompt["4"]["inputs"]["seed"] == 7
    assert prompt["4"]["inputs"]["fps"] == 16
    assert prompt["4"]["inputs"]["length"] == 4  # 3.5 秒向上取整成整数秒
    assert prompt["5"]["inputs"]["width"] == 720
    assert prompt["5"]["inputs"]["height"] == 1280
    assert prompt["6"]["inputs"]["filename_prefix"] == "E01-S001"
    assert data == b"MP4DATA" and filename == "clip.mp4"
    client.close()


def test_generate_raises_when_workflow_has_no_output(cfg):
    client = ComfyVideoClient(cfg)
    client.client.upload_image = lambda path: "x.png"
    client.client._post_json = lambda path, payload: {"prompt_id": "p1"}
    client.client._get_json = lambda path: {
        "p1": {"status": {"completed": True}, "outputs": {}}
    }
    with pytest.raises(ComfyError, match="没有输出文件"):
        client.generate("x", "a.png", "b.png")
    client.close()


def test_concat_clips_returns_false_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert concat_clips([tmp_path / "a.mp4"], tmp_path / "ep.mp4") is False
