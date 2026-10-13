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
    """小说章节 — 读写走文件，内存缓存避免每次击键全量读盘。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    number: int = 1
    title: str = ""
    status: ChapterStatus = ChapterStatus.OUTLINED
    # 文件路径（相对于项目目录）
    _content_path: str = ""
    _outline_path: str = ""
    # 内存缓存：None 表示尚未从磁盘加载
    _content_cache: str | None = field(default=None, repr=False, compare=False)
    _outline_cache: str | None = field(default=None, repr=False, compare=False)

    # ── 内容读写（文件 IO + 内存缓存）──

    def _read_file(self, path_str: str) -> str:
        if not path_str:
            return ""
        p = Path(path_str)
        if p.exists():
            return p.read_text(encoding="utf-8")
        return ""

    def _write_file(self, path_str: str, value: str) -> None:
        if not path_str:
            return
        from ..core.project_io import write_md
        write_md(Path(path_str), value)

    @property
    def content(self) -> str:
        if self._content_cache is not None:
            return self._content_cache
        self._content_cache = self._read_file(self._content_path)
        return self._content_cache

    @content.setter
    def content(self, value: str) -> None:
        self._content_cache = value
        self._write_file(self._content_path, value)

    def invalidate_content_cache(self) -> None:
        """外部改过磁盘文件后调用，强制下次重读。"""
        self._content_cache = None

    @property
    def outline(self) -> str:
        if self._outline_cache is not None:
            return self._outline_cache
        self._outline_cache = self._read_file(self._outline_path)
        return self._outline_cache

    @outline.setter
    def outline(self, value: str) -> None:
        if not value:
            return
        self._outline_cache = value
        self._write_file(self._outline_path, value)

    @property
    def notes(self) -> str:
        return ""

    @notes.setter
    def notes(self, value: str) -> None:
        pass

    def word_count(self) -> int:
        text = self.content
        return len(text.replace(" ", "").replace("\n", ""))
