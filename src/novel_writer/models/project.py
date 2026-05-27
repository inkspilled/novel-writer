from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .chapter import Chapter
from .character import Character


@dataclass
class Project:
    """小说项目。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    genre: str = ""  # 题材：玄幻、都市、科幻...
    style: str = ""  # 风格：轻松、严肃、悬疑...
    theme: str = ""  # 核心主题
    target_words: int = 200000  # 目标字数
    synopsis: str = ""  # 简介/立意
    chapters: list[Chapter] = field(default_factory=list)
    characters: list[Character] = field(default_factory=list)
    world_setting: str = ""  # 世界观设定
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def add_chapter(self, title: str = "") -> Chapter:
        ch = Chapter(number=len(self.chapters) + 1, title=title or f"第{len(self.chapters) + 1}章")
        self.chapters.append(ch)
        self.updated_at = datetime.now().isoformat()
        return ch

    def add_character(self, name: str, **kwargs) -> Character:
        char = Character(name=name, **kwargs)
        self.characters.append(char)
        self.updated_at = datetime.now().isoformat()
        return char

    def total_words(self) -> int:
        return sum(ch.word_count() for ch in self.chapters)
