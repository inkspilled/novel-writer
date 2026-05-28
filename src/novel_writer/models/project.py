from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .chapter import Chapter, ChapterStatus
from .character import Character
from ..core import project_io


@dataclass
class Project:
    """小说项目 — 基于目录的存储结构。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    genre: str = ""  # 题材：玄幻、都市、科幻...
    style: str = ""  # 风格：轻松、严肃、悬疑...
    theme: str = ""  # 核心主题
    target_words: int = 200000  # 目标字数
    synopsis: str = ""  # 简介/立意
    world_setting: str = ""  # 世界观设定
    chapters: list[Chapter] = field(default_factory=list)
    characters: list[Character] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    # 项目目录路径（运行时设置，不序列化）
    _project_dir: Path | None = field(default=None, repr=False)

    @property
    def project_dir(self) -> Path | None:
        return self._project_dir

    def set_project_dir(self, path: Path) -> None:
        self._project_dir = path

    # ── 章节管理 ──

    def add_chapter(self, title: str = "") -> Chapter:
        number = len(self.chapters) + 1
        ch_title = title or f"第{number}章"
        ch = Chapter(number=number, title=ch_title)
        if self._project_dir:
            from ..core.project_io import chapter_path, chapter_outline_path, chapter_filename, chapter_outline_filename
            ch._content_path = str(chapter_path(self._project_dir, number, ch_title))
            ch._outline_path = str(chapter_outline_path(self._project_dir, number, ch_title))
            # 创建空文件
            project_io.write_md(Path(ch._content_path), "")
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

    # ── 持久化 ──

    def save(self) -> None:
        """保存项目到目录。"""
        if not self._project_dir:
            return
        self.updated_at = datetime.now().isoformat()

        # 初始化目录结构
        project_io.init_project_dir(self._project_dir)

        # 保存 meta.json
        meta = {
            "id": self.id,
            "title": self.title,
            "genre": self.genre,
            "style": self.style,
            "theme": self.theme,
            "target_words": self.target_words,
            "synopsis": self.synopsis,
            "world_setting": self.world_setting,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "characters": [
                {
                    "id": c.id, "name": c.name, "aliases": c.aliases,
                    "gender": c.gender, "age": c.age, "personality": c.personality,
                    "background": c.background, "appearance": c.appearance,
                    "relationships": c.relationships, "arc": c.arc, "notes": c.notes,
                }
                for c in self.characters
            ],
            "chapter_meta": [
                {
                    "id": ch.id, "number": ch.number, "title": ch.title,
                    "status": ch.status.value if hasattr(ch.status, 'value') else ch.status,
                }
                for ch in self.chapters
            ],
        }
        project_io.save_meta(self._project_dir, meta)

    @classmethod
    def load(cls, project_dir: Path) -> Project:
        """从目录加载项目。"""
        meta = project_io.load_meta(project_dir)
        if not meta:
            raise FileNotFoundError(f"项目目录不存在或无效: {project_dir}")

        proj = cls(
            id=meta.get("id", ""),
            title=meta.get("title", ""),
            genre=meta.get("genre", ""),
            style=meta.get("style", ""),
            theme=meta.get("theme", ""),
            target_words=meta.get("target_words", 200000),
            synopsis=meta.get("synopsis", ""),
            world_setting=meta.get("world_setting", ""),
            created_at=meta.get("created_at", ""),
            updated_at=meta.get("updated_at", ""),
        )
        proj._project_dir = project_dir

        # 加载人物
        for char_data in meta.get("characters", []):
            char = Character(
                id=char_data.get("id", ""),
                name=char_data.get("name", ""),
                aliases=char_data.get("aliases", []),
                gender=char_data.get("gender", ""),
                age=char_data.get("age", ""),
                personality=char_data.get("personality", ""),
                background=char_data.get("background", ""),
                appearance=char_data.get("appearance", ""),
                relationships=char_data.get("relationships", {}),
                arc=char_data.get("arc", ""),
                notes=char_data.get("notes", ""),
            )
            proj.characters.append(char)

        # 加载章节
        scanned = project_io.scan_chapters(project_dir)
        for item in scanned:
            ch = Chapter(
                number=item["number"],
                title=item["title"],
                _content_path=str(item["content_path"]),
                _outline_path=str(item["outline_path"]) if item["outline_path"] else "",
            )
            for ch_meta in meta.get("chapter_meta", []):
                if ch_meta.get("number") == ch.number:
                    ch.id = ch_meta.get("id", ch.id)
                    try:
                        ch.status = ChapterStatus(ch_meta.get("status", "outlined"))
                    except ValueError:
                        pass
                    break
            proj.chapters.append(ch)

        return proj
