"""角色层：注册表、生命周期、POV 视图。"""
from .registry import (
    CharacterRecord,
    CharacterRegistry,
    CharacterStatus,
    CharacterTier,
)
from .offscreen import run_offscreen_tick
from .panel import run_character_panel
from .selection import offscreen_candidates, select_scene_cast
from .view import (
    CharacterView,
    CharacterViewBuilder,
    build_character_system_prompt,
    validate_knowledge_used,
)

__all__ = [
    "CharacterRecord", "CharacterRegistry", "CharacterStatus", "CharacterTier",
    "CharacterView", "CharacterViewBuilder",
    "run_character_panel", "run_offscreen_tick", "select_scene_cast", "offscreen_candidates",
    "build_character_system_prompt", "validate_knowledge_used",
]
