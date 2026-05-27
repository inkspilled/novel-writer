"""应用主题样式 - Apple / 小米极简风格。"""

# ── 深夜墨 (Midnight) ──
_MIDNIGHT = {
    "bg": "#0f0f14", "surface": "#16161d", "card": "#1c1c26", "elevated": "#242430",
    "border": "rgba(255,255,255,0.06)", "divider": "rgba(255,255,255,0.04)",
    "fg": "#e8e8ed", "fg2": "#8e8e9a", "fg3": "#5a5a66",
    "accent": "#6e8efb", "accent_hover": "#8aa4ff", "accent_bg": "rgba(110,142,251,0.12)",
    "shadow": "0 2px 12px rgba(0,0,0,0.4)",
}

# ── 晨雾白 (Daybreak) ──
_DAYBREAK = {
    "bg": "#f5f5f7", "surface": "#ffffff", "card": "#ffffff", "elevated": "#f0f0f2",
    "border": "rgba(0,0,0,0.08)", "divider": "rgba(0,0,0,0.05)",
    "fg": "#1d1d1f", "fg2": "#6e6e73", "fg3": "#aeaeb2",
    "accent": "#0071e3", "accent_hover": "#0077ed", "accent_bg": "rgba(0,113,227,0.08)",
    "shadow": "0 2px 12px rgba(0,0,0,0.06)",
}

# ── 远山蓝 (Azure) ──
_AZURE = {
    "bg": "#0a1628", "surface": "#0f1d32", "card": "#142240", "elevated": "#1a2a4a",
    "border": "rgba(100,160,255,0.08)", "divider": "rgba(100,160,255,0.04)",
    "fg": "#dce6f5", "fg2": "#7a92b8", "fg3": "#4a6080",
    "accent": "#4da6ff", "accent_hover": "#70b8ff", "accent_bg": "rgba(77,166,255,0.12)",
    "shadow": "0 2px 16px rgba(0,0,0,0.5)",
}

# ── 苍山绿 (Evergreen) ──
_EVERGREEN = {
    "bg": "#0d1a12", "surface": "#122018", "card": "#172a1e", "elevated": "#1e3426",
    "border": "rgba(80,200,120,0.08)", "divider": "rgba(80,200,120,0.04)",
    "fg": "#dceee2", "fg2": "#7aaa8e", "fg3": "#4a7060",
    "accent": "#50c878", "accent_hover": "#6dd494", "accent_bg": "rgba(80,200,120,0.12)",
    "shadow": "0 2px 16px rgba(0,0,0,0.5)",
}

THEMES = {
    "dark": ("深夜墨", _MIDNIGHT),
    "light": ("晨雾白", _DAYBREAK),
    "blue": ("远山蓝", _AZURE),
    "green": ("苍山绿", _EVERGREEN),
    "custom": ("自定义", None),  # 占位，实际值由用户配置
}


def get_theme_colors(name: str, config: dict = None) -> dict:
    """获取主题色，支持自定义主题。"""
    if name == "custom" and config:
        return {
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
        }
    _, t = THEMES.get(name, THEMES["dark"])
    return t


def build_style(theme_name: str, config: dict = None) -> str:
    t = get_theme_colors(theme_name, config)
    return f"""
/* ──── 全局 ──── */
* {{
    font-family: "PingFang SC", "SF Pro Display", "Helvetica Neue", "Microsoft YaHei", sans-serif;
}}
QMainWindow {{
    background-color: {t['bg']};
}}
QWidget {{
    background-color: {t['bg']};
    color: {t['fg']};
}}
/* 需要透明的容器控件，透出父级背景 */
QFrame, QLabel, QSplitter, QSplitterHandle {{
    background-color: transparent;
}}
QScrollArea {{
    background-color: transparent;
}}
QScrollArea > QWidget > QWidget {{
    background-color: {t['bg']};
}}
QScrollArea > QViewport {{
    background-color: {t['bg']};
}}

/* ──── 分割器 ──── */
QSplitter::handle {{
    background-color: {t['divider']};
    width: 1px;
}}

/* ──── 树 / 列表 ──── */
QTreeWidget, QListWidget {{
    background-color: {t['surface']};
    color: {t['fg']};
    border: none;
    border-radius: 12px;
    padding: 4px;
    font-size: 13px;
    outline: none;
}}
QTreeWidget::item, QListWidget::item {{
    padding: 8px 12px;
    border-radius: 8px;
    margin: 1px 2px;
}}
QTreeWidget::item:selected, QListWidget::item:selected {{
    background-color: {t['accent_bg']};
    color: {t['accent']};
}}
QTreeWidget::item:hover, QListWidget::item:hover {{
    background-color: {t['border']};
}}

/* ──── 编辑器 ──── */
QTextEdit, QPlainTextEdit {{
    background-color: {t['surface']};
    color: {t['fg']};
    border: 1px solid {t['border']};
    border-radius: 12px;
    padding: 16px;
    font-size: 15px;
    selection-background-color: {t['accent_bg']};
}}
QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {t['accent']};
}}

/* ──── 标签 ──── */
QLabel {{
    color: {t['fg']};
    font-size: 13px;
    background: transparent;
}}

/* ──── 按钮 ──── */
QPushButton {{
    background-color: {t['elevated']};
    color: {t['fg']};
    border: 1px solid {t['border']};
    border-radius: 10px;
    padding: 8px 20px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {t['card']};
    border-color: {t['fg3']};
}}
QPushButton:pressed {{
    background-color: {t['border']};
}}
QPushButton:disabled {{
    color: {t['fg3']};
    background-color: {t['surface']};
    border-color: {t['divider']};
}}
QPushButton#primary {{
    background-color: {t['accent']};
    color: #ffffff;
    border: none;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background-color: {t['accent_hover']};
}}
QPushButton#danger {{
    background-color: transparent;
    color: #ff6b6b;
    border: 1px solid rgba(255,107,107,0.3);
}}
QPushButton#danger:hover {{
    background-color: rgba(255,107,107,0.1);
}}

/* ──── 输入框 ──── */
QLineEdit {{
    background-color: {t['elevated']};
    color: {t['fg']};
    border: 1px solid {t['border']};
    border-radius: 10px;
    padding: 10px 14px;
    font-size: 13px;
    selection-background-color: {t['accent_bg']};
}}
QLineEdit:focus {{
    border-color: {t['accent']};
}}

/* ──── 下拉框 ──── */
QComboBox {{
    background-color: {t['elevated']};
    color: {t['fg']};
    border: 1px solid {t['border']};
    border-radius: 10px;
    padding: 8px 14px;
    font-size: 13px;
    min-height: 20px;
}}
QComboBox:hover {{
    border-color: {t['fg3']};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: {t['card']};
    color: {t['fg']};
    border: 1px solid {t['border']};
    border-radius: 10px;
    padding: 4px;
    selection-background-color: {t['accent_bg']};
    selection-color: {t['accent']};
    outline: none;
}}

/* ──── 数字输入 ──── */
QDoubleSpinBox, QSpinBox {{
    background-color: {t['elevated']};
    color: {t['fg']};
    border: 1px solid {t['border']};
    border-radius: 10px;
    padding: 8px 14px;
    font-size: 13px;
}}

/* ──── Tab ──── */
QTabWidget::pane {{
    border: none;
    background-color: {t['bg']};
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {t['fg2']};
    padding: 10px 24px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 13px;
    font-weight: 500;
    min-width: 60px;
}}
QTabBar::tab:hover {{
    color: {t['fg']};
}}
QTabBar::tab:selected {{
    color: {t['accent']};
    border-bottom: 2px solid {t['accent']};
}}

/* ──── 状态栏 ──── */
QStatusBar {{
    background-color: {t['surface']};
    color: {t['fg2']};
    font-size: 12px;
    border-top: 1px solid {t['divider']};
    padding: 4px 16px;
}}

/* ──── 滚动条 ──── */
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
}}
QScrollBar::handle:vertical {{
    background-color: {t['fg3']};
    border-radius: 3px;
    min-height: 40px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {t['fg2']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    height: 0;
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 6px;
}}
QScrollBar::handle:horizontal {{
    background-color: {t['fg3']};
    border-radius: 3px;
    min-width: 40px;
}}

/* ──── 分组框 ──── */
QGroupBox {{
    color: {t['fg']};
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 14px;
    margin-top: 12px;
    padding: 24px 16px 14px 16px;
    font-weight: 600;
    font-size: 13px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 16px;
    padding: 0 8px;
    color: {t['fg2']};
    font-weight: 500;
    font-size: 12px;
}}

/* ──── 菜单栏 ──── */
QMenuBar {{
    background-color: {t['surface']};
    color: {t['fg']};
    border-bottom: 1px solid {t['divider']};
    padding: 2px 8px;
    font-size: 13px;
}}
QMenuBar::item {{
    padding: 6px 14px;
    border-radius: 6px;
}}
QMenuBar::item:selected {{
    background-color: {t['accent_bg']};
    color: {t['accent']};
}}
QMenu {{
    background-color: {t['card']};
    color: {t['fg']};
    border: 1px solid {t['border']};
    border-radius: 12px;
    padding: 6px;
}}
QMenu::item {{
    padding: 8px 20px;
    border-radius: 8px;
    min-width: 120px;
}}
QMenu::item:selected {{
    background-color: {t['accent_bg']};
    color: {t['accent']};
}}
QMenu::separator {{
    height: 1px;
    background-color: {t['divider']};
    margin: 4px 12px;
}}

/* ──── 对话框 ──── */
QDialog {{
    background-color: {t['bg']};
}}

/* ──── 消息气泡 ──── */
QLabel#chatBubble {{
    background-color: {t['card']};
    color: {t['fg']};
    border-radius: 16px;
    padding: 12px 16px;
    font-size: 13px;
}}
QLabel#chatBubbleUser {{
    background-color: {t['accent_bg']};
    color: {t['fg']};
    border-radius: 16px;
    padding: 12px 16px;
    font-size: 13px;
}}

/* ──── Agent 面板区域 ──── */
QWidget#agentHeader {{
    background-color: {t['surface']};
    border-bottom: 1px solid {t['divider']};
}}
QWidget#agentInput {{
    background-color: {t['surface']};
    border-top: 1px solid {t['divider']};
}}

/* ──── 编辑区工具栏 ──── */
QWidget#editorToolbar {{
    background-color: {t['surface']};
    border-bottom: 1px solid {t['divider']};
}}

/* ──── 侧边栏 ──── */
QWidget#sidebarWidget {{
    background-color: {t['surface']};
    border-right: 1px solid {t['divider']};
}}
"""

MAIN_STYLE = build_style("dark")
