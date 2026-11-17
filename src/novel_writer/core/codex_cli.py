"""Codex 画师：用本机 Codex CLI 生图（文章 L49-L52 的调用口径）。

文章原文：

```text
调用 Codex：
codex exec --skip-git-repo-check -s read-only -i 参考图.png - < 提示词.txt
提示词开头写：Use your image generation tool to create exactly ONE image.
Do not run shell commands.
画好的图在 ~/.codex/generated_images/<日志里的 session id>/。
```

落地要点：

- **只读沙箱**（`-s read-only`）：画师不许改项目文件，产物由本软件取走另存；
- 提示词文件走 stdin，不拼在命令行里（中文/长提示词不会撞参数长度和转义）；
- 一次只画一张（`exactly ONE image`），数量写死，避免模型自作主张画网格图；
- 从 Codex 输出里抓 session id，再去 `~/.codex/generated_images/<id>/` 取最新图片；
- 抓不到 session id 就按修改时间在 `generated_images/` 下兜底找本次任务产生的新图；
- 用量受限时返回 `CodexQuotaError`。

本模块不 import Qt，子进程用列表参数（shell=False），路径全程 pathlib。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

PROMPT_PREFIX = (
    "Use your image generation tool to create exactly ONE image. "
    "Do not run shell commands."
)
DEFAULT_TIMEOUT = 300.0
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
QUOTA_MARKERS = (
    "quota", "rate limit", "额度", "用量已尽", "usage limit",
)
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class CodexError(RuntimeError):
    """Codex 调用失败。"""


class CodexQuotaError(CodexError):
    """Codex 用量受限。"""


def codex_binary() -> str:
    """优先配置里的 `codex_bin`，否则 PATH 里的 `codex`。"""
    return shutil.which("codex") or ""


def generated_root() -> Path:
    """Codex 生图的默认落点：`~/.codex/generated_images`。"""
    return Path.home() / ".codex" / "generated_images"


def codex_command(reference: str | Path | None = None) -> list[str]:
    """文章里的调用命令，换成 shell=False 的列表形态。"""
    binary = codex_binary() or "codex"
    command = [binary, "exec", "--skip-git-repo-check", "-s", "read-only"]
    if reference:
        command += ["-i", str(Path(reference))]
    command.append("-")
    return command


def compose_prompt(prompt: str) -> str:
    """提示词开头固定一句：只画一张、不跑 shell。"""
    body = (prompt or "").strip()
    return PROMPT_PREFIX + "\n\n" + body if body else PROMPT_PREFIX


def parse_session_id(output: str) -> str:
    """从 Codex 输出里抓 session id（uuid）；抓不到返回空串。"""
    found = _UUID_RE.findall(output or "")
    return found[-1] if found else ""


def find_generated_image(
    session_id: str = "", *, since: float | None = None, root: Path | None = None
) -> Path | None:
    """按 session id 找图；没有 id 时按修改时间兜底。"""
    base = Path(root) if root else generated_root()
    if not base.exists():
        return None
    candidates: list[Path] = []
    if session_id:
        folder = base / session_id
        if folder.is_dir():
            candidates = [
                path for path in folder.iterdir()
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
            ]
    if not candidates:
        # 兜底：整个 generated_images 下本次任务之后写入的图
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            try:
                if since is not None and path.stat().st_mtime < since - 1.0:
                    continue
            except OSError:
                continue
            candidates.append(path)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0)
    return candidates[-1]


def _looks_like_image(data: bytes) -> bool:
    if not data:
        return False
    if data.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")):
        return True
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


class CodexImageClient:
    """和 `ImageAPIClient` 同形态的 `generate()`，让上层不用关心画师是谁。"""

    def __init__(self, cfg: dict[str, Any] | None = None, *, runner=None):
        self.cfg = dict(cfg or {})
        self.timeout = float(self.cfg.get("timeout") or DEFAULT_TIMEOUT)
        self.binary = str(self.cfg.get("codex_bin") or codex_binary())
        self._runner = runner or self._run_process

    def available(self) -> bool:
        return bool(self.binary)

    def generate(
        self,
        positive: str,
        negative: str = "",
        prefix: str = "asset",
        width: int = 1024,
        height: int = 1024,
        reference: str | Path | None = None,
    ) -> bytes:
        """调 Codex 画一张，返回图片字节。负向词附在正文末尾（Codex 无独立负面通道）。"""
        if not self.available():
            raise CodexError(
                "没找到 Codex CLI：先安装 codex 并把 `codex` 放进 PATH，"
                "或在「设置 → 图片生成 API」里换成 ComfyUI / 图片 API。"
            )
        prompt = compose_prompt(positive)
        if negative:
            prompt += "\n\n避免：" + negative
        started = time.time()
        output = self._runner(prompt, reference)
        if isinstance(output, bytes):
            data = output
        else:
            text = str(output or "")
            if any(marker in text.lower() for marker in QUOTA_MARKERS):
                raise CodexQuotaError(
                    "Codex 用量受限，请稍后重试。"
                )
            session_id = parse_session_id(text)
            path = find_generated_image(session_id, since=started)
            if path is None:
                raise CodexError(
                    "Codex 没有产出图片：检查提示词是否带了「exactly ONE image」，"
                    "以及 ~/.codex/generated_images 是否可读。"
                )
            data = path.read_bytes()
        if not _looks_like_image(data):
            raise CodexError("Codex 产出的文件不是有效图片")
        return data

    def _run_process(self, prompt: str, reference: str | Path | None) -> str:
        """跑 `codex exec ... -`，提示词从 stdin 喂进去。"""
        command = [self.binary, "exec", "--skip-git-repo-check", "-s", "read-only"]
        if reference:
            command += ["-i", str(Path(reference))]
        command.append("-")
        env = dict(os.environ)
        # Codex 自己的会话目录：避免污染用户正在用的会话
        with tempfile.TemporaryDirectory(prefix="nw-codex-") as tmp:
            prompt_path = Path(tmp) / "prompt.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            try:
                with prompt_path.open("rb") as stdin:
                    proc = subprocess.run(
                        command,
                        stdin=stdin,
                        capture_output=True,
                        timeout=self.timeout,
                        env=env,
                    )
            except FileNotFoundError:
                raise CodexError("没找到 Codex CLI（PATH 里没有 codex）") from None
            except subprocess.TimeoutExpired:
                raise CodexError(f"Codex 超时（{self.timeout:.0f}s）仍未返回") from None
            text = (proc.stdout or b"").decode("utf-8", "replace")
            err = (proc.stderr or b"").decode("utf-8", "replace")
            if proc.returncode != 0:
                combined = (text + "\n" + err).strip()
                if any(marker in combined.lower() for marker in QUOTA_MARKERS):
                    raise CodexQuotaError("Codex 用量受限，请稍后重试。")
                raise CodexError(f"Codex 退出码 {proc.returncode}：{combined[:400]}")
            return text + "\n" + err

    def close(self) -> None:
        """CLI 客户端没有长连接；保留接口和 ImageAPIClient 对齐。"""


def create_client(cfg: dict[str, Any] | None = None) -> CodexImageClient:
    return CodexImageClient(cfg)
