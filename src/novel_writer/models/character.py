from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class Character:
    """小说人物。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    aliases: list[str] = field(default_factory=list)  # 别名
    gender: str = ""
    age: str = ""
    personality: str = ""     # 性格特征
    background: str = ""      # 背景故事
    appearance: str = ""      # 外貌描述
    relationships: dict[str, str] = field(default_factory=dict)  # 与其他人物的关系
    arc: str = ""             # 人物弧线/成长轨迹
    notes: str = ""
