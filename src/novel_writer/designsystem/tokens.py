"""设计系统 token — 主题色、字号、间距的唯一来源（对标 smt-intelligent/designsystem）。

Qt 样式表生成在 `ui.common.styles`，只读这里的 token，不再各处硬编码色值。
"""
from __future__ import annotations

# ── 深夜墨 (Midnight) ──
MIDNIGHT = {
    "bg": "#0f0f14", "surface": "#16161d", "card": "#1c1c26", "elevated": "#242430",
    "border": "rgba(255,255,255,0.06)", "divider": "rgba(255,255,255,0.04)",
    "fg": "#e8e8ed", "fg2": "#8e8e9a", "fg3": "#5a5a66",
    "accent": "#6e8efb", "accent_hover": "#8aa4ff", "accent_bg": "rgba(110,142,251,0.12)",
    "shadow": "0 2px 12px rgba(0,0,0,0.4)",
}

# ── 晨雾白 (Daybreak) ──
DAYBREAK = {
    "bg": "#f5f5f7", "surface": "#ffffff", "card": "#ffffff", "elevated": "#f0f0f2",
    "border": "rgba(0,0,0,0.08)", "divider": "rgba(0,0,0,0.05)",
    "fg": "#1d1d1f", "fg2": "#6e6e73", "fg3": "#aeaeb2",
    "accent": "#0071e3", "accent_hover": "#0077ed", "accent_bg": "rgba(0,113,227,0.08)",
    "shadow": "0 2px 12px rgba(0,0,0,0.06)",
}

# ── 远山蓝 (Azure) ──
AZURE = {
    "bg": "#0a1628", "surface": "#0f1d32", "card": "#142240", "elevated": "#1a2a4a",
    "border": "rgba(100,160,255,0.08)", "divider": "rgba(100,160,255,0.04)",
    "fg": "#dce6f5", "fg2": "#7a92b8", "fg3": "#4a6080",
    "accent": "#4da6ff", "accent_hover": "#70b8ff", "accent_bg": "rgba(77,166,255,0.12)",
    "shadow": "0 2px 16px rgba(0,0,0,0.5)",
}

# ── 苍山绿 (Evergreen) ──
EVERGREEN = {
    "bg": "#0d1a12", "surface": "#122018", "card": "#172a1e", "elevated": "#1e3426",
    "border": "rgba(80,200,120,0.08)", "divider": "rgba(80,200,120,0.04)",
    "fg": "#dceee2", "fg2": "#7aaa8e", "fg3": "#4a7060",
    "accent": "#50c878", "accent_hover": "#6dd494", "accent_bg": "rgba(80,200,120,0.12)",
    "shadow": "0 2px 16px rgba(0,0,0,0.5)",
}

# 主题名 → (显示名, 色板)
THEMES = {
    "dark": ("深夜墨", MIDNIGHT),
    "light": ("晨雾白", DAYBREAK),
    "blue": ("远山蓝", AZURE),
    "green": ("苍山绿", EVERGREEN),
    "custom": ("自定义", None),  # 占位，实际值由用户配置
}

# 自定义主题的配置键 → token 键（用户配置写入 config.json）
CUSTOM_KEYS = {
    "custom_bg": "bg",
    "custom_surface": "surface",
    "custom_elevated": "elevated",
    "custom_fg": "fg",
    "custom_fg2": "fg2",
    "custom_accent": "accent",
}


def get_theme_colors(name: str, config: dict | None = None) -> dict:
    """获取主题色 token，支持自定义主题。"""
    if name == "custom" and config:
        colors = dict(MIDNIGHT)
        colors.update({
            "bg": config.get("custom_bg", "#1a1a2e"),
            "surface": config.get("custom_surface", "#16213e"),
            "card": config.get("custom_surface", "#16213e"),
            "elevated": config.get("custom_elevated", "#0f3460"),
            "border": "rgba(255,255,255,0.08)",
            "divider": "rgba(255,255,255,0.04)",
            "fg": config.get("custom_fg", "#e0e0e0"),
            "fg2": config.get("custom_fg2", "#a0a0a0"),
            "fg3": "#606060",
            "accent": config.get("custom_accent", "#e94560"),
            "accent_hover": "#ff6b81",
            "accent_bg": "rgba(233,69,96,0.12)",
            "shadow": "0 2px 16px rgba(0,0,0,0.5)",
        })
        return colors
    _, t = THEMES.get(name, THEMES["light"])
    return t
