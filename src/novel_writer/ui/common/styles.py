"""应用主题样式 - Qt 样式表生成（色值只来自 designsystem.tokens）。"""
from __future__ import annotations

from ...designsystem import THEMES, get_theme_colors  # noqa: F401 — 兼容旧导入路径


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
    padding: 8px 32px 8px 14px;
    font-size: 13px;
    min-height: 20px;
}}
QComboBox:hover {{
    border-color: {t['fg3']};
}}
QComboBox:focus {{
    border-color: {t['accent']};
}}
QComboBox::drop-down {{
    border: none;
    width: 28px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0;
    height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {t['fg2']};
    margin-right: 10px;
}}
QComboBox::down-arrow:on {{
    border-top-color: {t['accent']};
}}
QComboBox QAbstractItemView {{
    background-color: {t['card']};
    color: {t['fg']};
    border: 1px solid {t['border']};
    border-radius: 10px;
    padding: 6px;
    selection-background-color: {t['accent_bg']};
    selection-color: {t['accent']};
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    padding: 8px 12px;
    border-radius: 6px;
    min-height: 20px;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: {t['border']};
}}
QComboBox QAbstractItemView::item:selected {{
    background-color: {t['accent_bg']};
    color: {t['accent']};
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

/* ──── Tab（正文/规划页签） ──── */
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {t['divider']};
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
    background-color: {t['border']};
}}
QTabBar::tab:selected {{
    color: {t['accent']};
    border-bottom: 2px solid {t['accent']};
    background-color: {t['accent_bg']};
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
    margin-top: 14px;
    padding: 26px 18px 16px 18px;
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

/* ──── 表单标签 ──── */
QFormLayout > QLabel {{
    color: {t['fg2']};
    font-size: 13px;
    font-weight: 500;
    padding: 8px 12px 8px 0;
}}
QFormLayout {{
    spacing: 12px;
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
    border-radius: 10px;
    padding: 4px;
    font-size: 13px;
}}
QMenu::item {{
    padding: 6px 16px;
    border-radius: 6px;
    min-width: 100px;
    font-size: 13px;
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

/* ──── 三栏分区：编辑区是纸面，两侧是工具条 ──── */
QWidget#editorPanel {{
    background-color: {t['bg']};
}}
"""
