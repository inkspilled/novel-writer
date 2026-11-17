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
# 网络类错误（连不上/读超时）自动重试：只重试传输层，工作流执行错误不重试
NETWORK_RETRIES = 1
RETRY_DELAY = 1.5
PLACEHOLDERS = (
    "positive", "negative", "seed", "prefix", "width", "height", "reference_image",
    # 视频工作流：首尾帧、帧率、时长
    "first_frame", "last_frame", "fps", "length",
)


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
            # 整个值就是一个需要数字的占位符时，直接给数字类型：
            # ComfyUI 的 KSampler.seed / EmptyLatentImage 只认 int，字符串会被判非法。
            for key in ("seed", "width", "height", "fps", "length"):
                token = "{{" + key + "}}"
                if node.strip() == token and key in values:
                    return values[key]
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
                 timeout: float = DEFAULT_TIMEOUT, seed: int | None = None,
                 reference_workflow: str = "", retries: int = NETWORK_RETRIES):
        self.base_url = (base_url or DEFAULT_COMFY_URL).rstrip("/")
        self.workflow_path = str(workflow_path or "")
        self.timeout = float(timeout or DEFAULT_TIMEOUT)
        self.seed = seed
        self.reference_workflow = reference_workflow
        self.retries = max(0, int(retries))
        self._client = httpx.Client(base_url=self.base_url, timeout=30.0)

    # ── 可被测试替换的三处 I/O ──

    def _retry(self, send):
        """传输层错误（连不上/超时/中断）自动重试；HTTP 4xx/5xx 属业务结果不重试。"""
        for attempt in range(self.retries + 1):
            try:
                return send()
            except httpx.TransportError as exc:
                if attempt >= self.retries:
                    raise ComfyError(
                        f"ComfyUI 连接失败（已重试 {self.retries} 次）：{exc.__class__.__name__}"
                    ) from exc
                logger.warning("ComfyUI 网络错误，%ss 后重试：%s", RETRY_DELAY, exc)
                time.sleep(RETRY_DELAY)

    def _post_json(self, path: str, payload: dict) -> dict:
        resp = self._retry(lambda: self._client.post(path, json=payload))
        if resp.status_code >= 400:
            raise ComfyError(f"ComfyUI {path} 返回 {resp.status_code}：{resp.text[:300]}")
        return resp.json()

    def _get_json(self, path: str) -> dict:
        resp = self._retry(lambda: self._client.get(path))
        if resp.status_code >= 400:
            raise ComfyError(f"ComfyUI {path} 返回 {resp.status_code}：{resp.text[:300]}")
        return resp.json()

    def _get_bytes(self, path: str, params: dict) -> bytes:
        resp = self._retry(lambda: self._client.get(path, params=params))
        if resp.status_code >= 400:
            raise ComfyError(f"ComfyUI /view 返回 {resp.status_code}")
        return resp.content

    # ── 主流程 ──

    def generate(self, positive: str, negative: str = "", prefix: str = "drama",
                 width: int = 512, height: int = 896,
                 reference_image: str | Path | None = None, seed: int | None = None) -> bytes:
        """按模板生成一张图，返回 PNG/JPEG 字节。"""
        use_reference = bool(reference_image and self.reference_workflow)
        template = load_workflow(self.reference_workflow if use_reference else self.workflow_path)
        uploaded = self.upload_image(reference_image) if use_reference else ""
        seed = seed if seed is not None else self.seed
        seed = seed if seed is not None else int(time.time() * 1000) % 2_147_483_647
        prompt = fill_workflow(template, {
            "positive": positive,
            "negative": negative,
            "seed": seed,
            "prefix": prefix,
            "width": width,
            "height": height,
            "reference_image": uploaded,
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

    def upload_image(self, path: str | Path) -> str:
        """上传到 ComfyUI input，返回 LoadImage 接受的文件名；不覆盖已有文件。"""
        try:
            def send():
                with Path(path).open("rb") as source:
                    return self._client.post("/upload/image",
                        files={"image": (Path(path).name, source, "application/octet-stream")},
                        data={"type": "input", "subfolder": "novel_writer", "overwrite": "false"})
            resp = self._retry(send)
            resp.raise_for_status()
            result = resp.json()
            return "/".join(part for part in (result.get("subfolder", ""), result["name"]) if part)
        except (OSError, httpx.HTTPError, ValueError, KeyError) as exc:
            raise ComfyError("参考图上传失败，请检查图片文件和 ComfyUI 连接") from exc

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
        "reference_workflow": str(section.get("reference_workflow") or ""),
        "timeout": float(section.get("timeout") or DEFAULT_TIMEOUT),
    }
