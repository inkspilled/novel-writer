"""视频生成：把首尾帧 + 提示词交给视频服务，取回片段。

两种 provider：

- ``comfyui``：本机 ComfyUI 视频工作流。
- ``seedance``：火山方舟 Seedance。

工作流占位符约定（和图片工作流同一套递归替换）：

``{{positive}}`` ``{{first_frame}}`` ``{{last_frame}}`` ``{{seed}}`` ``{{prefix}}``
``{{width}}`` ``{{height}}`` ``{{fps}}`` ``{{length}}``

- ``{{first_frame}}`` / ``{{last_frame}}`` 填上传后的图片文件名，接 LoadImage；
- ``{{width}}`` / ``{{height}}`` / ``{{fps}}`` 是整数，``{{length}}`` 是秒数（浮点）；
- 输出节点用 SaveVideo / VHS_VideoCombine 都行，客户端从 history 的
  gifs / videos / images 三个键里找第一个文件。
"""
from __future__ import annotations

import time
import uuid

from .comfyui import ComfyUIClient, ComfyError, fill_workflow, load_workflow
from .logger import get_logger

logger = get_logger(__name__)

DEFAULT_VIDEO_URL = "http://127.0.0.1:8188"
DEFAULT_TIMEOUT = 900.0
POLL_INTERVAL = 1.0
VIDEO_OUTPUT_KEYS = ("gifs", "videos", "images")


def config_from_app(cfg: dict | None = None) -> dict:
    """取 ``video_generation`` 配置段；按 provider 补默认值。"""
    if cfg is None:
        from .app_config import load_config

        cfg = load_config()
    section = dict(cfg.get("video_generation") or {})
    provider = str(section.get("provider") or "comfyui")
    if provider == "seedance":
        from .seedance import config_from_app as seedance_config

        return seedance_config(cfg)
    return {
        "provider": "comfyui",
        "base_url": str(section.get("base_url") or DEFAULT_VIDEO_URL),
        "workflow": str(section.get("workflow") or ""),
        "timeout": float(section.get("timeout") or DEFAULT_TIMEOUT),
        "fps": int(section.get("fps") or 16),
        "length": float(section.get("length") or 3.0),
    }


def configuration_error(cfg: dict) -> str:
    if cfg.get("provider") == "seedance":
        from .seedance import configuration_error as seedance_error

        return seedance_error(cfg)
    if cfg.get("provider") != "comfyui":
        return "视频服务只支持 ComfyUI 工作流或 Seedance（火山方舟）。"
    if not cfg.get("workflow"):
        return "请配置 ComfyUI 视频工作流。"
    return ""


class ComfyVideoClient:
    """按视频工作流逐镜生成片段；同步阻塞，调用方放线程里跑。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.fps = int(cfg.get("fps") or 16)
        self.length = float(cfg.get("length") or 3.0)
        self.client = ComfyUIClient(
            base_url=cfg.get("base_url", ""),
            workflow_path=cfg.get("workflow", ""),
            timeout=float(cfg.get("timeout") or DEFAULT_TIMEOUT),
        )
        self._workflow = load_workflow(self.client.workflow_path)

    def generate(
        self,
        prompt: str,
        first_frame: str | None = None,
        last_frame: str | None = None,
        prefix: str = "shot",
        width: int = 720,
        height: int = 1280,
        seed: int | None = None,
        length: float | None = None,
    ) -> tuple[bytes, str]:
        """生成一个片段，返回 ``(视频字节, 文件名)``。"""
        first = self.client.upload_image(first_frame) if first_frame else ""
        last = self.client.upload_image(last_frame) if last_frame else ""
        seed = seed if seed is not None else int(time.time() * 1000) % 2_147_483_647
        # length 统一成整数秒：ComfyUI 视频节点的帧数/时长字段只认 int，
        # 传 "3.5" 这种字符串会直接判非法。
        seconds = max(1, int(float(length if length is not None else self.length) + 0.5))
        payload = fill_workflow(self._workflow, {
            "positive": prompt,
            "negative": "",
            "first_frame": first,
            "last_frame": last,
            "seed": seed,
            "prefix": prefix,
            "width": int(width),
            "height": int(height),
            "fps": self.fps,
            "length": seconds,
        })
        queued = self.client._post_json(
            "/prompt", {"prompt": payload, "client_id": uuid.uuid4().hex}
        )
        prompt_id = str(queued.get("prompt_id") or "")
        if not prompt_id:
            raise ComfyError(f"ComfyUI 没有返回 prompt_id：{str(queued)[:300]}")
        outputs = self._wait_outputs(prompt_id)
        if not outputs:
            raise ComfyError(
                "视频工作流执行完成但没有输出文件（检查 SaveVideo / VHS_VideoCombine）"
            )
        item = outputs[0]
        data = self.client._get_bytes("/view", {
            "filename": item.get("filename", ""),
            "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output"),
        })
        return data, str(item.get("filename") or f"{prefix}.mp4")

    def _wait_outputs(self, prompt_id: str) -> list[dict]:
        deadline = time.monotonic() + self.client.timeout
        while time.monotonic() < deadline:
            if getattr(self, "stopped", lambda: False)():
                raise ComfyError("已停止等待视频输出；ComfyUI 中已提交任务可能仍在运行")
            history = self.client._get_json(f"/history/{prompt_id}")
            entry = history.get(prompt_id) if isinstance(history, dict) else None
            if isinstance(entry, dict):
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise ComfyError(f"ComfyUI 视频工作流失败：{str(status.get('messages'))[:300]}")
                found: list[dict] = []
                for node_out in (entry.get("outputs") or {}).values():
                    if not isinstance(node_out, dict):
                        continue
                    for key in VIDEO_OUTPUT_KEYS:
                        for item in node_out.get(key) or []:
                            if isinstance(item, dict) and item.get("filename"):
                                found.append(item)
                if found:
                    return found
                if status.get("completed") is True:
                    return []
            time.sleep(POLL_INTERVAL)
        raise ComfyError(f"ComfyUI 视频工作流超时（{self.client.timeout:.0f}s）仍未出片段")

    def close(self) -> None:
        self.client.close()


def create_client(cfg: dict, *, drama_dir=None):
    if cfg.get("provider") == "seedance":
        from .seedance import SeedanceClient

        return SeedanceClient(cfg, drama_dir=drama_dir)
    return ComfyVideoClient(cfg)


def concat_clips(clips: list, output) -> bool:
    """用 ffmpeg 把片段按顺序拼成整集；没装 ffmpeg 或失败返回 False。"""
    import shutil
    import subprocess
    from pathlib import Path

    ffmpeg = shutil.which("ffmpeg")
    out = Path(output)
    if not ffmpeg or not clips:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    listing = out.parent / "clips.txt"
    lines = []
    for clip in clips:
        text = str(Path(clip)).replace("'", "'\\''")
        lines.append(f"file '{text}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-c", "copy", str(out)],
            capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and out.exists()
