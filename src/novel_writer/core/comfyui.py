"""ComfyUI 图像生成接入 — 把「API 格式」工作流当模板，占位符换成本资产的提示词。

约定（刻意保持简单，不解析节点图）：

1. 在 ComfyUI 里搭好流程，用「Save (API Format)」导出 JSON；
2. 把要替换的输入改成占位符：``{{positive}}`` / ``{{negative}}`` / ``{{seed}}`` /
   ``{{prefix}}`` / ``{{width}}`` / ``{{height}}``；
3. 生成时递归替换所有字符串里的占位符，POST ``/prompt``，轮询 ``/history/{id}``，
   再从 ``/view`` 下载图片。

不绑定具体模型/节点名：换 checkpoint、加 LoRA、改采样器都只改模板 JSON。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .logger import get_logger

logger = get_logger(__name__)

DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
DEFAULT_TIMEOUT = 300.0
POLL_INTERVAL = 1.0
PLACEHOLDERS = ("positive", "negative", "seed", "prefix", "width", "height")


class ComfyError(RuntimeError):
    """ComfyUI 调用失败（连不上 / 工作流报错 / 超时）。"""


def load_workflow(path: str | Path) -> dict:
    """读取 ComfyUI「Save (API Format)」导出的工作流 JSON。"""
    p = Path(path)
    if not p.exists():
        raise ComfyError(f"工作流模板不存在：{p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ComfyError(f"工作流模板读取失败：{e}") from e
    if not isinstance(data, dict) or not data:
        raise ComfyError("工作流模板必须是 API 格式的 JSON 对象（不是节点界面导出的 UI 格式）")
    return data


def fill_workflow(template: dict, values: dict[str, Any]) -> dict:
    """递归替换模板里的 ``{{placeholder}}``；只替换字符串叶子。"""
    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            text = node
            for key in PLACEHOLDERS:
                token = "{{" + key + "}}"
                if token in text:
                    text = text.replace(token, str(values.get(key, "")))
            return text
        return node

    return walk(template)


class ComfyUIClient:
    """最小 ComfyUI HTTP 客户端；同步阻塞，调用方放线程里跑。"""

    def __init__(self, base_url: str = "", workflow_path: str | Path = "",
                 timeout: float = DEFAULT_TIMEOUT, seed: int | None = None):
        self.base_url = (base_url or DEFAULT_COMFY_URL).rstrip("/")
        self.workflow_path = str(workflow_path or "")
        self.timeout = float(timeout or DEFAULT_TIMEOUT)
        self.seed = seed
        self._client = httpx.Client(base_url=self.base_url, timeout=30.0)

    # ── 可被测试替换的三处 I/O ──

    def _post_json(self, path: str, payload: dict) -> dict:
        resp = self._client.post(path, json=payload)
        if resp.status_code >= 400:
            raise ComfyError(f"ComfyUI {path} 返回 {resp.status_code}：{resp.text[:300]}")
        return resp.json()

    def _get_json(self, path: str) -> dict:
        resp = self._client.get(path)
        if resp.status_code >= 400:
            raise ComfyError(f"ComfyUI {path} 返回 {resp.status_code}：{resp.text[:300]}")
        return resp.json()

    def _get_bytes(self, path: str, params: dict) -> bytes:
        resp = self._client.get(path, params=params)
        if resp.status_code >= 400:
            raise ComfyError(f"ComfyUI /view 返回 {resp.status_code}")
        return resp.content

    # ── 主流程 ──

    def generate(self, positive: str, negative: str = "", prefix: str = "drama",
                 width: int = 768, height: int = 1344) -> bytes:
        """按模板生成一张图，返回 PNG/JPEG 字节。"""
        template = load_workflow(self.workflow_path)
        seed = self.seed if self.seed is not None else int(time.time() * 1000) % 2_147_483_647
        prompt = fill_workflow(template, {
            "positive": positive,
            "negative": negative,
            "seed": seed,
            "prefix": prefix,
            "width": width,
            "height": height,
        })
        client_id = uuid.uuid4().hex
        queued = self._post_json("/prompt", {"prompt": prompt, "client_id": client_id})
        prompt_id = str(queued.get("prompt_id") or "")
        if not prompt_id:
            raise ComfyError(f"ComfyUI 没有返回 prompt_id：{str(queued)[:300]}")
        images = self._wait_history(prompt_id)
        if not images:
            raise ComfyError("ComfyUI 执行完成但没有输出图片（检查模板里是否接了 SaveImage）")
        first = images[0]
        return self._get_bytes("/view", {
            "filename": first.get("filename", ""),
            "subfolder": first.get("subfolder", ""),
            "type": first.get("type", "output"),
        })

    def _wait_history(self, prompt_id: str) -> list[dict]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            history = self._get_json(f"/history/{prompt_id}")
            entry = history.get(prompt_id) if isinstance(history, dict) else None
            if isinstance(entry, dict):
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise ComfyError(f"ComfyUI 执行失败：{str(status.get('messages'))[:300]}")
                outputs = entry.get("outputs") or {}
                images: list[dict] = []
                for node_out in outputs.values():
                    for image in (node_out or {}).get("images") or []:
                        if isinstance(image, dict) and image.get("filename"):
                            images.append(image)
                if images:
                    return images
                if status.get("completed") is True:
                    return []
            time.sleep(POLL_INTERVAL)
        raise ComfyError(f"ComfyUI 超时（{self.timeout:.0f}s）仍未出图")

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


def config_from_app(cfg: dict | None = None) -> dict:
    """从应用配置取 ComfyUI 段：``{base_url, workflow, timeout}``。"""
    from . import app_config

    cfg = cfg if cfg is not None else app_config.load_config()
    section = cfg.get("comfyui") or {}
    return {
        "base_url": str(section.get("base_url") or DEFAULT_COMFY_URL),
        "workflow": str(section.get("workflow") or ""),
        "timeout": float(section.get("timeout") or DEFAULT_TIMEOUT),
    }
