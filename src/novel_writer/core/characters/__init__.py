"""角色层：注册表、生命周期、POV 视图。"""
from .registry import (
    CharacterRecord,
    CharacterRegistry,
    CharacterStatus,
    CharacterTier,
)
from .view import (
    CharacterView,
    CharacterViewBuilder,
    build_character_system_prompt,
    validate_knowledge_used,
)

__all__ = [
    "CharacterRecord", "CharacterRegistry", "CharacterStatus", "CharacterTier",
    "CharacterView", "CharacterViewBuilder",
    "build_character_system_prompt", "validate_knowledge_used",
]
