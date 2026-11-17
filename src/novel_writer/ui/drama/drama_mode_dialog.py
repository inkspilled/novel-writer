"""短剧工作流模式选择 — 对齐小说的 WorkflowModeDialog：模式 + 生成范围一步选完。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QGroupBox, QHBoxLayout, QLabel,
    QPushButton, QRadioButton, QVBoxLayout,
)

from ...core.app_config import load_config
from ...designsystem import get_theme_colors

_MAIN_MODES = (
    ("full", "准备剧本与分镜"),
    ("production", "制作本集视频"),
)
_ADVANCED_MODES = (
    (
        ("adapt", "重编改编圣经"), ("segment", "智能分集"), ("storyboard", "重新分镜"),
        ("story_review", "故事冷读与确认"), ("assets", "从分镜提取资产"),
    ),
    (
        ("keyframes", "首尾帧提示词"), ("frame_images", "生成首尾帧图片"),
        ("submit_review", "首尾帧与提交审核"),
        ("video", "提交视频生成"),
    ),
    (
        ("post", "后期与片段修复"), ("final_review", "全片复核"), ("retro", "制作复盘"),
    ),
    (
        ("preflight", "开工自检"), ("reference", "参考片分析"), ("sketch", "色块草图与色表"),
    ),
)
_SCOPED_TASKS = {"full", "segment", "storyboard", "keyframes"}

_HINTS = {
    "production": "对当前集完成制作；自动补齐缺少的剧本、分镜、定妆照和首尾帧。审核不通过即停，"
                  "故事和画风确认后继续。每镜逐一提交，已完成且材料未变的跳过。",
    "preflight": "只检查环境并导出开工报告，不生成图片或视频。",
    "story_review": "审核独立冷读本集故事，监督核对要求；通过后请你确认故事。",
    "sketch": "生成彩色/黑白布局示意与逐场色表；示意检查不能替代正式图审核。",
    "reference": "读取企划的参考片路径，抽帧并交给视觉模型分析机位和进出画比例。",
    "frame_images": "审核首尾帧方案后才出图。推镜用裁切放大，其余镜头用尾帧编辑首帧。",
    "submit_review": "逐镜检查首尾帧、叠图、人物定妆照与视频提示词。材料修改后需重审。",
    "post": "按企划中的后期参数生成新版本；设置了 repair 时一并修复指定镜头。",
    "final_review": "模型完整解码并抽帧静态复核；动态与声音仍需你播放确认。",
    "retro": "保存画风、有效提示词、错误和修复办法到 drama/retro/。",
    "full": "整理改编圣经、智能分集、分镜和资产清单。再次运行会从待用章节和缺失环节接续。",
    "segment": "按剧情选择连续章节合为一集,每集先规划再写剧本。\n"
               "已有来源映射会保留;只有剧本成功保存后才标记素材已使用,失败可继续。",
    "adapt": "把小说规划文档 + 滚动摘要重新组织成短剧改编圣经(会覆盖现有圣经)。",
    "storyboard": "逐集调用模型把剧本拆成镜头 JSON;「全部」会重拆已有分镜。",
    "keyframes": "逐集为每个镜头写「首帧画什么 / 尾帧画什么 / 中间发生什么」，"
                 "只写说明不画图；先审再画。",
    "video": "本集已有首尾帧图且提交前审核通过后，逐镜提交 ComfyUI 或 Seedance。"
             "材料变更必须重审；不会在这里静默补画图片。",
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
        for task, text in _MAIN_MODES:
            self._mode_combo.addItem(text, task)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self._mode_combo)
        self._advanced = QCheckBox("显示单步工具")
        self._advanced.toggled.connect(self._show_advanced)
        mode_layout.addWidget(self._advanced)
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

    def _show_advanced(self, show: bool) -> None:
        combo = self._mode_combo
        if show:
            for group in _ADVANCED_MODES:
                combo.insertSeparator(combo.count())
                for task, text in group:
                    combo.addItem(text, task)
        else:
            if combo.currentIndex() >= len(_MAIN_MODES):
                combo.setCurrentIndex(0)
            while combo.count() > len(_MAIN_MODES):
                combo.removeItem(combo.count() - 1)

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
        has_scope = task in _SCOPED_TASKS
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
        elif task == "keyframes":
            self._scope_group.setTitle("首尾帧范围")
            self.radio_current.setText("当前集")
            self.radio_all.setText("全部集（逐集写首尾帧说明）")
        else:  # storyboard
            self._scope_group.setTitle("分镜范围")
            self.radio_current.setText("当前集")
            self.radio_all.setText(self._storyboard_all_label)

    def get_result(self) -> tuple[str, str]:
        """返回 (task, scope);scope 对无范围模式固定 "all"。"""
        task = self._mode_combo.currentData()
        has_scope = task in _SCOPED_TASKS
        scope = ("current" if self.radio_current.isChecked() else "all") if has_scope else "all"
        return task, scope
