"""短剧工作流模式选择 — 对齐小说的 WorkflowModeDialog：模式 + 生成范围一步选完。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QGroupBox, QHBoxLayout, QLabel,
    QPushButton, QRadioButton, QVBoxLayout,
)

from ...core.app_config import load_config
from ...designsystem import get_theme_colors

# 模式 → (下拉文案, 需要生成范围)
_MODES: list[tuple[str, str, bool]] = [
    ("full", "🎬 全流程 — 圣经 → 智能分集(剧本)→ 分镜 → 资产", True),
    ("segment", "🧩 智能分集 — 按剧情冲突合并章节并写剧本", True),
    ("adapt", "📖 重编圣经 — 重新生成短剧改编圣经", False),
    ("storyboard", "🎥 一键分镜 — 把剧本拆成可拍摄的镜头表", True),
    ("assets", "🎨 提取资产 — 从分镜提取角色/地点(本地,免模型)", False),
]

_HINTS = {
    "full": "已有的圣经/分镜自动跳过;中断后再跑,智能分集从待用章节继续。",
    "segment": "按剧情选择连续章节合为一集,每集先规划再写剧本。\n"
               "已有来源映射会保留;只有剧本成功保存后才标记素材已使用,失败可继续。",
    "adapt": "把小说规划文档 + 滚动摘要重新组织成短剧改编圣经(会覆盖现有圣经)。",
    "storyboard": "逐集调用模型把剧本拆成镜头 JSON;「全部」会重拆已有分镜。",
    "assets": "本地规则提取,不调用模型;从已有分镜提取角色/地点资产占位。",
}


class DramaModeDialog(QDialog):
    """短剧「执行模式」对话框,返回 (task, scope)。"""

    def __init__(self, storyboard_all_label: str = "全部集", full_hint: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("短剧工作流")
        self.setMinimumWidth(420)
        self._storyboard_all_label = storyboard_all_label
        self._full_hint = full_hint
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # 模式选择
        mode_group = QGroupBox("工作流模式")
        mode_layout = QVBoxLayout(mode_group)
        self._mode_combo = QComboBox()
        for task, text, _has_scope in _MODES:
            self._mode_combo.addItem(text, task)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self._mode_combo)
        layout.addWidget(mode_group)

        # 生成范围(按模式显隐/换文案)
        self._scope_group = QGroupBox("生成范围")
        scope_layout = QVBoxLayout(self._scope_group)
        self.radio_current = QRadioButton("")
        self.radio_current.setChecked(True)
        self.radio_all = QRadioButton("")
        group = QButtonGroup(self)
        group.addButton(self.radio_current)
        group.addButton(self.radio_all)
        scope_layout.addWidget(self.radio_current)
        scope_layout.addWidget(self.radio_all)
        layout.addWidget(self._scope_group)
        self._apply_scope_style()

        # 模式说明
        self._hint_label = QLabel("")
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet("font-size: 11px; color: palette(placeholderText);")
        layout.addWidget(self._hint_label)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_ok = QPushButton("开始生成")
        btn_ok.setObjectName("primary")
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

        self._on_mode_changed(0)

    def _apply_scope_style(self) -> None:
        """把单选做成「整行卡片」：选中行有底色 + 粗体 + 实心圆点，避免看不出选哪个。"""
        cfg = load_config()
        theme = cfg.get("theme", "dark")
        colors = get_theme_colors(theme, cfg if theme == "custom" else None)
        accent = colors.get("accent", "#0071e3")
        accent_bg = colors.get("accent_bg", "rgba(0,113,227,0.12)")
        fg3 = colors.get("fg3", "#8a8f98")
        self._scope_group.setStyleSheet(
            f"""
            QRadioButton {{
                spacing: 10px;
                padding: 9px 12px;
                border-radius: 8px;
                min-height: 22px;
            }}
            QRadioButton:hover {{ background: {accent_bg}; }}
            QRadioButton:checked {{ background: {accent_bg}; font-weight: 700; }}
            QRadioButton::indicator {{
                width: 16px; height: 16px; border-radius: 10px;
                border: 2px solid {fg3}; background: transparent;
            }}
            QRadioButton::indicator:checked {{
                border: 2px solid {accent}; background: {accent};
            }}
            """
        )
        for radio in (self.radio_current, self.radio_all):
            radio.setCursor(Qt.CursorShape.PointingHandCursor)

    def _on_mode_changed(self, _idx: int) -> None:
        task = self._mode_combo.currentData()
        has_scope = next((has for key, _t, has in _MODES if key == task), False)
        self._scope_group.setVisible(has_scope)
        hint = _HINTS.get(task, "")
        if task == "full" and self._full_hint:
            hint = self._full_hint
        self._hint_label.setText(hint)
        if not has_scope:
            return
        if task in ("full", "segment"):
            self._scope_group.setTitle("分集范围")
            self.radio_current.setText("生成下一集(最多读5章,剩余素材顺延)")
            self.radio_all.setText("持续分集(处理全部待用正文,可随时停止)")
        else:  # storyboard
            self._scope_group.setTitle("分镜范围")
            self.radio_current.setText("当前集")
            self.radio_all.setText(self._storyboard_all_label)

    def get_result(self) -> tuple[str, str]:
        """返回 (task, scope);scope 对无范围模式固定 "all"。"""
        task = self._mode_combo.currentData()
        has_scope = next((has for key, _t, has in _MODES if key == task), False)
        scope = ("current" if self.radio_current.isChecked() else "all") if has_scope else "all"
        return task, scope
