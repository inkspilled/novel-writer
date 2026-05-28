from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ChapterStatus(str, Enum):
    OUTLINED = "outlined"    # 已有大纲
    DRAFTED = "drafted"      # 初稿完成
    PROOFREAD = "proofread"  # 已校对
    REVIEWED = "reviewed"    # 已审核
    POLISHED = "polished"    # 已润色
    FINAL = "final"          # 定稿


@dataclass
class Chapter:
    """小说章节 — 轻量元数据，正文存储在文件中。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    number: int = 1
    title: str = ""
    status: ChapterStatus = ChapterStatus.OUTLINED
    # 文件路径（相对于项目目录）
    _content_path: str = ""
    _outline_path: str = ""

    # ── 内容读写（文件 IO）──

    @property
    def content(self) -> str:
        if self._content_path:
            p = Path(self._content_path)
            if p.exists():
                return p.read_text(encoding="utf-8")
        return ""

    @content.setter
    def content(self, value: str) -> None:
        if self._content_path:
            p = Path(self._content_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(value, encoding="utf-8")

    @property
    def outline(self) -> str:
        if self._outline_path:
            p = Path(self._outline_path)
            if p.exists():
                return p.read_text(encoding="utf-8")
        return ""

    @outline.setter
    def outline(self, value: str) -> None:
        if not value:
            return
        if not self._outline_path:
            return
        p = Path(self._outline_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value, encoding="utf-8")

    @property
    def notes(self) -> str:
        return ""

    @notes.setter
    def notes(self, value: str) -> None:
        pass

    def word_count(self) -> int:
        text = self.content
        return len(text.replace(" ", "").replace("\n", ""))
