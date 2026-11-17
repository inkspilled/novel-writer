"""Seedance（火山方舟）视频客户端，提交首尾帧并续取云端任务。"""
from __future__ import annotations

import base64
import mimetypes
import math
import time
from pathlib import Path
from typing import Any

import httpx

from .logger import get_logger
from .seedance_tasks import SeedanceTasks

logger = get_logger(__name__)

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
TASKS_PATH = "/contents/generations/tasks"
DEFAULT_TIMEOUT = 900.0
DEFAULT_POLL_INTERVAL = 3.0
DEFAULT_RESOLUTION = "720p"

DEFAULT_MODEL = "doubao-seedance-2-5-260628"
MINI_MODEL = "doubao-seedance-2-0-mini-260615"
MODELS = (DEFAULT_MODEL, MINI_MODEL)

_STATUS_OK = {"succeeded", "success", "completed", "done"}
_STATUS_BAD = {"failed", "error", "expired", "cancelled", "canceled"}


class SeedanceError(RuntimeError):
    """Seedance 调用失败。"""


class _TaskFailed(SeedanceError):
    """服务商报告任务已失败或取消。"""


def generation_seconds(seconds: float, model: str) -> int:
    seconds = math.ceil(float(seconds))
    minimum, maximum = (2, 12) if "1-0" in model else ((4, 15) if "2-0" in model else (4, 30))
    if seconds <= 0 or seconds > maximum:
        raise SeedanceError(f"模型单镜时长须为 {minimum}–{maximum} 秒；过长请先拆镜")
    return max(minimum, seconds)


def api_key_from_env(cfg: dict[str, Any] | None = None) -> str:
    """只读环境变量优先；本机配置里的 key 作为后备。绝不写日志。"""
    import os

    key = (os.environ.get("ARK_API_KEY") or "").strip()
    if key:
        return key
    cfg = cfg or {}
    return str(cfg.get("api_key") or "").strip()


def config_from_app(cfg: dict | None = None) -> dict[str, Any]:
    """取 `video_generation` 配置段并补齐 Seedance 默认值。"""
    if cfg is None:
        from .app_config import load_config

        cfg = load_config()
    section = dict(cfg.get("video_generation") or {})
    model = str(section.get("model") or DEFAULT_MODEL)
    return {
        "provider": "seedance",
        "base_url": str(section.get("base_url") or ARK_BASE_URL).rstrip("/"),
        "model": model,
        "api_key": api_key_from_env(section),
        "resolution": str(section.get("resolution") or DEFAULT_RESOLUTION),
        "timeout": float(section.get("timeout") or DEFAULT_TIMEOUT),
        "poll_interval": float(section.get("poll_interval") or DEFAULT_POLL_INTERVAL),
        "ratio": str(section.get("ratio") or ""),
        "fps": int(section.get("fps") or 24),
        "length": float(section.get("length") or 3.0),
        "prompt_suffix": str(section.get("prompt_suffix") or ""),
        "extra_parameters": section.get("extra_parameters") or {},
    }


def configuration_error(cfg: dict) -> str:
    """缺什么直说，别等人点了生成才发现没 key。"""
    if not cfg.get("api_key"):
        return (
            "缺少 ARK_API_KEY：到火山引擎开通 Seedance，申请 API Key 后写进环境变量 "
            "ARK_API_KEY。"
        )
    if not cfg.get("model"):
        return "请选择 Seedance 模型（2.5 或 2.0 mini）。"
    return ""


def _data_url(path: str | Path) -> str:
    """首尾帧图 → data URL（不上传到第三方，密钥也不出现在 URL 里）。"""
    file = Path(path)
    raw = file.read_bytes()
    mime = mimetypes.guess_type(file.name)[0] or "image/png"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def build_payload(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    first_frame: str | Path | None = None,
    last_frame: str | Path | None = None,
    resolution: str = DEFAULT_RESOLUTION,
    ratio: str = "",
    duration: float | None = None,
    extra_parameters: dict | None = None,
    prompt_suffix: str = "",
) -> dict[str, Any]:
    """拼请求体（纯函数，单测直接断言字段）。"""
    text = (prompt or "").strip()
    if prompt_suffix.strip():
        text = text + "\n" + prompt_suffix.strip()
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    if first_frame:
        content.append({
            "type": "image_url",
            "image_url": {"url": _data_url(first_frame)},
            "role": "first_frame",
        })
    if last_frame:
        content.append({
            "type": "image_url",
            "image_url": {"url": _data_url(last_frame)},
            "role": "last_frame",
        })
    payload: dict[str, Any] = {"model": model, "content": content}
    parameters = dict(extra_parameters or {})
    if resolution:
        parameters["resolution"] = resolution
    if ratio:
        parameters["ratio"] = ratio
    if duration:
        parameters["duration"] = max(1, math.ceil(float(duration)))
    if set(parameters) & {"model", "content", "frames"}:
        raise ValueError("附加参数不能覆盖 model/content/frames")
    payload.update(parameters)
    return payload


def build_prompt(prompt: str, suffix: str = "") -> str:
    """提示词 + 可选的命令行式参数后缀。"""
    text = (prompt or "").strip()
    if suffix.strip():
        text = text + "\n" + suffix.strip()
    return text


def task_status(payload: dict[str, Any]) -> str:
    return str(payload.get("status") or "").lower()


def task_video_url(payload: dict[str, Any]) -> str:
    """从任务结果里取视频地址：兼容 content.video_url / data.video_url 等写法。"""
    if not isinstance(payload, dict):
        return ""
    for key in ("content", "data", "result", "output"):
        block = payload.get(key)
        if isinstance(block, dict):
            for field in ("video_url", "url", "file_url", "download_url"):
                value = block.get(field)
                if isinstance(value, str) and value:
                    return value
    for field in ("video_url", "url"):
        value = payload.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


def _looks_like_video(data: bytes) -> bool:
    return bool(data) and data[4:8] == b"ftyp"


class SeedanceClient:
    """同步客户端；调用方放线程里跑（和 ComfyUI 客户端保持同一形态）。"""

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        drama_dir: Path | str | None = None,
        http: httpx.Client | None = None,
    ):
        self.cfg = dict(cfg or {})
        self.cfg.setdefault("provider", "seedance")
        self.model = str(self.cfg.get("model") or DEFAULT_MODEL)
        self.resolution = str(self.cfg.get("resolution") or DEFAULT_RESOLUTION)
        self.timeout = float(self.cfg.get("timeout") or DEFAULT_TIMEOUT)
        self.poll_interval = max(0.2, float(self.cfg.get("poll_interval") or DEFAULT_POLL_INTERVAL))
        self.tasks = SeedanceTasks(drama_dir) if drama_dir is not None else None
        self._key = str(self.cfg.get("api_key") or api_key_from_env(self.cfg))
        if not self._key:
            raise SeedanceError(configuration_error({"api_key": "", "model": self.model}))
        self.base_url = str(self.cfg.get("base_url") or ARK_BASE_URL).rstrip("/")
        self._owns_http = http is None
        self.http = http or httpx.Client(timeout=self.timeout)

    def fingerprint(self, prompt, first_frame, last_frame, seconds):
        from .drama_production import digest, file_digest
        return digest({"prompt": prompt, "model": self.model, "duration": generation_seconds(seconds, self.model),
                       "base_url":self.base_url, "ratio":self.cfg.get("ratio"), "suffix":self.cfg.get("prompt_suffix"),
                       "resolution": self.resolution, "extra": self.cfg.get("extra_parameters"),
                       "first": file_digest(Path(first_frame)) if first_frame else "",
                       "last": file_digest(Path(last_frame)) if last_frame else ""})

    def generate(
        self,
        prompt: str,
        first_frame: str | Path | None = None,
        last_frame: str | Path | None = None,
        prefix: str = "shot",
        width: int = 720,
        height: int = 1280,
        seed: int | None = None,
        length: float | None = None,
        *,
        shot_id: str = "",
        episode: int = 0,
    ) -> tuple[bytes, str]:
        seconds = generation_seconds(length if length is not None else self.cfg.get("length") or 3.0, self.model)
        payload = build_payload(
            build_prompt(prompt, str(self.cfg.get("prompt_suffix") or "")),
            model=self.model,
            first_frame=first_frame,
            last_frame=last_frame,
            resolution=self.resolution,
            ratio=("adaptive" if "2-5" in self.model and first_frame else str(self.cfg.get("ratio") or "")),
            duration=seconds,
            extra_parameters=self.cfg.get("extra_parameters") or {},
        )
        if self.tasks is None:
            data = self._fetch_video(self._submit(payload))
        else:
            key = f"{episode}:{shot_id or prefix}"
            input_hash = self.fingerprint(prompt, first_frame, last_frame, seconds)
            with self.tasks.locked() as records:
                data = self._run_task(records, key, input_hash, payload)
        return data, f"{prefix or 'shot'}.mp4"

    def _run_task(self, records, key, input_hash, payload):
        previous = records.get(key, {})
        if previous.get("state") == "submitting":
            raise SeedanceError("上次提交结果不明，请先核对服务商的任务记录")
        task_id = previous.get("task_id")
        if previous.get("input_hash") != input_hash:
            if previous.get("state") == "submitted":
                raise SeedanceError("上次任务尚未结束，请先恢复原任务")
            task_id = None
        if not task_id:
            records[key] = {"input_hash": input_hash, "state": "submitting"}
            self.tasks.save(records)
            try:
                task_id = self._submit(payload)
            except SeedanceError:
                if self._submission_rejected:
                    if previous:
                        records[key] = previous
                    else:
                        records.pop(key, None)
                    self.tasks.save(records)
                raise
            records[key].update(state="submitted", task_id=task_id)
            self.tasks.save(records)
        try:
            data = self._fetch_video(task_id)
        except _TaskFailed:
            records.pop(key)
            self.tasks.save(records)
            raise
        records[key]["state"] = "completed"
        self.tasks.save(records)
        return data

    def query_task(self, task_id: str) -> dict[str, Any]:
        """查一次任务状态（不轮询）：给「查询并认领」遗留任务用。"""
        url = f"{self.base_url}{TASKS_PATH}/{task_id}"
        try:
            response = self.http.get(url, headers={"Authorization": f"Bearer {self._key}"})
        except httpx.RequestError as exc:
            raise SeedanceError(f"Seedance 查询失败：{exc.__class__.__name__}") from None
        body = (response.text if hasattr(response, "text") else "").replace(self._key, "[REDACTED]")
        if response.status_code >= 400:
            self._raise_for_error(response.status_code, body)
        try:
            return response.json()
        except ValueError:
            raise SeedanceError(f"Seedance 查询返回非 JSON：{body[:200]}") from None

    def _fetch_video(self, task_id):
        data = self._download(self._poll(task_id))
        if not _looks_like_video(data):
            raise SeedanceError("下载到的内容不是有效 mp4")
        return data

    # ── HTTP 细节 ──

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

    def _submit(self, payload: dict[str, Any]) -> str:
        self._submission_rejected = False
        url = self.base_url + TASKS_PATH
        try:
            response = self.http.post(url, headers=self._headers(), json=payload)
        except httpx.RequestError as exc:
            raise SeedanceError(f"Seedance 连接失败：{exc.__class__.__name__}") from None
        body = (response.text if hasattr(response, "text") else "").replace(self._key, "[REDACTED]")
        if response.status_code >= 400:
            self._submission_rejected = 400 <= response.status_code < 500
            self._raise_for_error(response.status_code, body)
        try:
            data = response.json()
        except ValueError:
            raise SeedanceError(f"Seedance 返回非 JSON：{body[:200]}") from None
        task_id = ""
        if isinstance(data, dict):
            task_id = str(data.get("id") or data.get("task_id") or "")
            if not task_id and isinstance(data.get("data"), dict):
                task_id = str(data["data"].get("id") or data["data"].get("task_id") or "")
        if not task_id:
            raise SeedanceError("Seedance 没有返回任务 id，请核对服务商的任务记录")
        if self._key in task_id:
            raise SeedanceError("Seedance 返回的任务标识无效")
        logger.info("Seedance 任务已提交：%s", task_id)
        return task_id

    def _poll(self, task_id: str) -> str:
        url = f"{self.base_url}{TASKS_PATH}/{task_id}"
        deadline = time.monotonic() + self.timeout
        last_status = ""
        while time.monotonic() < deadline:
            if getattr(self, "stopped", lambda: False)():
                raise SeedanceError("已停止等待；任务记录已保留，续跑时查询原任务")
            try:
                response = self.http.get(url, headers={"Authorization": f"Bearer {self._key}"})
            except httpx.RequestError as exc:
                raise SeedanceError(f"Seedance 轮询失败：{exc.__class__.__name__}") from None
            body = (response.text if hasattr(response, "text") else "").replace(self._key, "[REDACTED]")
            if response.status_code >= 400:
                self._raise_for_error(response.status_code, body)
            try:
                data = response.json()
            except ValueError:
                raise SeedanceError(f"Seedance 轮询返回非 JSON：{body[:200]}") from None
            status = task_status(data) if isinstance(data, dict) else ""
            last_status = status or last_status
            if status in _STATUS_OK:
                video_url = task_video_url(data)
                if not video_url:
                    raise SeedanceError("任务成功但结果里没有视频地址")
                return video_url
            if status in _STATUS_BAD:
                error = data.get("error") if isinstance(data, dict) else None
                detail = str(error or data).replace(self._key, "[REDACTED]")[:300]
                raise _TaskFailed(f"Seedance 任务失败（{status}）：{detail}")
            time.sleep(self.poll_interval)
        raise SeedanceError(f"Seedance 任务超时（{self.timeout:.0f}s，最后状态 {last_status or '未知'}）")

    def _download(self, url: str) -> bytes:
        try:
            # 视频地址是对象存储签名链接，不带 API key
            response = self.http.get(url, follow_redirects=True)
        except httpx.RequestError as exc:
            raise SeedanceError(f"下载视频失败：{exc.__class__.__name__}") from None
        if response.status_code >= 400:
            raise SeedanceError(f"下载视频失败（HTTP {response.status_code}）")
        return response.content

    def _raise_for_error(self, status_code: int, body: str) -> None:
        body = body.replace(self._key, "[REDACTED]")
        raise SeedanceError(f"Seedance 请求失败（HTTP {status_code}）：{body[:300]}")

    def close(self) -> None:
        if self._owns_http:
            self.http.close()


def _mark_recovered_clip(store, episode: int, shot_id: str, input_hash: str, clip: Path) -> None:
    """认领的片段写进 clips manifest（signature 用云端 input_hash，
    制作流程按「指纹或签名任一命中」跳过，不会重复烧钱重生成）。"""
    from . import project_io
    from .drama_production import file_digest

    path = store.videos_dir / f"ep{int(episode):03d}_clips.json"
    manifest: dict[str, Any] = {}
    if path.is_file():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    manifest[str(shot_id)] = {"signature": input_hash, "file": file_digest(clip), "recovered": True}
    project_io.write_json(path, manifest)


def recover_tasks(cfg: dict[str, Any], store, *, stopped=None) -> list[str]:
    """查询 submitting/submitted 状态的遗留任务并认领结果。

    - submitting（上次提交结果不明）：拿 task_id 查服务商真实状态；
      查到成功就下载认领，查到失败就清记录，没拿到任务号的直接清；
    - submitted：已完成认领，失败清记录，仍在生成继续等。
    返回给人看的结论行；锁被正在运行的任务持有时直接报错。
    """
    tasks = SeedanceTasks(store.root)
    lines: list[str] = []
    with tasks.locked() as records:
        pending = {k: v for k, v in records.items()
                   if isinstance(v, dict) and v.get("state") in ("submitting", "submitted")}
        if not pending:
            return ["没有未完成的 Seedance 云端任务"]
        client = SeedanceClient(cfg, drama_dir=store.root)
        client.stopped = stopped or (lambda: False)
        try:
            for key, record in pending.items():
                task_id = str(record.get("task_id") or "")
                if not task_id:
                    records.pop(key, None)
                    lines.append(f"{key}：提交时没拿到任务号，记录已清，可重新提交")
                    continue
                try:
                    payload = client.query_task(task_id)
                except SeedanceError as exc:
                    lines.append(f"{key}：查询失败（{exc}），记录保留")
                    continue
                status = task_status(payload)
                episode_text, _, shot_id = key.partition(":")
                try:
                    episode = int(episode_text or 0) or 1
                except ValueError:
                    episode = 1
                if status in _STATUS_OK:
                    url = task_video_url(payload)
                    if not url:
                        lines.append(f"{key}：任务成功但没有视频地址，记录保留")
                        continue
                    data = client._download(url)
                    if not _looks_like_video(data):
                        lines.append(f"{key}：下载内容不是有效 mp4，记录保留")
                        continue
                    clip = store.save_clip(episode, shot_id, data)
                    record["state"] = "completed"
                    _mark_recovered_clip(store, episode, shot_id,
                                         str(record.get("input_hash") or ""), clip)
                    lines.append(f"{key}：任务已完成，已认领 → {clip.name}")
                elif status in _STATUS_BAD:
                    records.pop(key, None)
                    detail = str(payload.get("error") or status)[:120] if isinstance(payload, dict) else status
                    lines.append(f"{key}：服务商报告失败（{detail}），记录已清，可重新提交")
                else:
                    record["state"] = "submitted"
                    lines.append(f"{key}：仍在生成（状态 {status or '未知'}），下次再查")
            tasks.save(records)
        finally:
            client.close()
    return lines
