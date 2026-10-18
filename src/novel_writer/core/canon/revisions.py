"""章节修订版本 — 封印后编辑不覆写原件，写入 versions/ 并记正典。"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from .. import project_io
from ..logger import get_logger
from .events import CanonEvent, CanonLog

logger = get_logger(__name__)


def versions_dir(project_dir: Path) -> Path:
    return project_dir / "chapters" / "versions"


def _index_path(project_dir: Path) -> Path:
    return versions_dir(project_dir) / "index.json"


def _read_index(project_dir: Path) -> dict:
    path = _index_path(project_dir)
    if not path.exists():
        return {}
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def latest_revision(project_dir: Path, chapter: int) -> Path | None:
    rel = _read_index(project_dir).get(str(chapter))
    if rel:
        p = versions_dir(project_dir) / rel
        if p.exists():
            return p
    # 索引丢失时按 mtime 兜底
    files = list(versions_dir(project_dir).glob(f"{chapter}_*.txt"))
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def write_revision(
    project_dir: Path,
    chapter: int,
    content: str,
    *,
    reason: str = "manual",
    base_path: Path | None = None,
) -> Path:
    """写一个新版本并更新索引/正典；返回版本文件路径。"""
    versions = versions_dir(project_dir)
    versions.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    name = f"{chapter}_{int(time.time() * 1000)}_{digest[:8]}.txt"
    path = versions / name
    project_io.write_md(path, content)

    index = _read_index(project_dir)
    index[str(chapter)] = name
    project_io.write_json(_index_path(project_dir), index)

    try:
        CanonLog(project_dir).append(CanonEvent(
            chapter=chapter,
            type="chapter_revision",
            actor="system",
            visibility="world",
            payload={
                "chapter": chapter,
                "filename": name,
                "digest": digest,
                "reason": reason,
                "base": base_path.name if base_path else "",
                "source": "revision",
            },
        ))
    except Exception as e:
        logger.warning("修订事件入正典失败（版本文件已写）: %s", e)
    logger.info("第%d章写入修订版本 %s", chapter, name)
    return path
