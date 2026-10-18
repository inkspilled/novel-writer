"""设计系统 — token 与主题色的唯一来源。"""
from .tokens import (
    THEMES, STATUS_COLORS, CUSTOM_KEYS, get_status_colors, get_theme_colors,
)

__all__ = [
    "THEMES", "STATUS_COLORS", "CUSTOM_KEYS",
    "get_status_colors", "get_theme_colors",
]
