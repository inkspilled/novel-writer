"""章节封印 — 已定稿正文的不可变锚点。

封印记录章节文件 SHA256 到正典事件；封印后工作流不得覆写该章。
注意：当前 UI 手动编辑仍走 Chapter.content setter，要做到“手动也版本化”
需要下一步把编辑器写入接到版本层。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..logger import get_logger
from .. import project_io
from .events import CanonEvent, CanonLog
from .revisions import latest_revision

logger = get_logger(__name__)


class ChapterSealedError(Exception):
    pass


def chapter_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sealed_info(project_dir: Path, chapter: int) -> dict | None:
    canon = CanonLog(project_dir)
    latest = None
    for event in canon.for_chapter(chapter):
        if event.type == "chapter_sealed":
            latest = event
    return (latest.payload or {}) if latest else None


def is_sealed(project_dir: Path, chapter: int) -> bool:
    return sealed_info(project_dir, chapter) is not None


def seal_chapter(project_dir: Path, chapter: int) -> CanonEvent | None:
    """封印第 chapter 章；已封印且内容一致则幂等返回 None。"""
    filename = project_io.find_chapter_file(project_dir, chapter)
    if not filename:
        return None
    base_path = project_dir / "chapters" / filename
    path = latest_revision(project_dir, chapter) or base_path
    if not path.exists() or path.stat().st_size == 0:
        return None
    digest = chapter_digest(path)
    info = sealed_info(project_dir, chapter)
    if info and info.get("digest") == digest:
        return None
    # 修订版重新封印：保留父摘要，最新 seal 生效
    event = CanonLog(project_dir).append(CanonEvent(
        chapter=chapter,
        type="chapter_sealed",
        actor="system",
        visibility="world",
        payload={
            "chapter": chapter,
            "filename": path.name,
            "digest": digest,
            "parent": (info or {}).get("digest", ""),
            "source": "seal",
        },
    ))
    logger.info("第%d章已封印 (%s)", chapter, digest[:12])
    return event
