from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum


class ChapterStatus(str, Enum):
    OUTLINED = "outlined"    # 已有大纲
    DRAFTED = "drafted"      # 初稿完成
    PROOFREAD = "proofread"  # 已校对
    REVIEWED = "reviewed"    # 已审核
    POLISHED = "polished"    # 已润色
    FINAL = "final"          # 定稿


@dataclass
class Chapter:
    """小说章节。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    number: int = 1
    title: str = ""
    outline: str = ""     # 章节大纲
    content: str = ""     # 正文内容
    notes: str = ""       # 备注
    status: ChapterStatus = ChapterStatus.OUTLINED
    review_comments: str = ""   # 审核意见
    proofread_notes: str = ""   # 校对备注

    def word_count(self) -> int:
        return len(self.content.replace(" ", "").replace("\n", ""))
