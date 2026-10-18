"""正典层：append-only 事件日志 + 感知抽取。"""
from .events import CanonEvent, CanonLog, seed_event
from .extract import apply_percept_payload, build_percept_prompt, extract_percepts
from .projection import project_world_state
from .seal import ChapterSealedError, is_sealed, seal_chapter

__all__ = [
    "CanonEvent", "CanonLog", "seed_event",
    "apply_percept_payload", "build_percept_prompt", "extract_percepts",
    "project_world_state", "ChapterSealedError", "is_sealed", "seal_chapter",
]
