"""新建项目对话框 — 完整的项目初始化流程。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTextEdit, QComboBox, QSpinBox, QPushButton, QFileDialog,
    QGroupBox, QFormLayout, QFrame, QMessageBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap

from ..locales import t

GENRES = [
    "玄幻", "仙侠", "都市", "科幻", "历史", "武侠",
    "悬疑", "恐怖", "言情", "军事", "游戏", "体育",
    "灵异", "同人", "奇幻", "末世", "修真", "架空",
    "轻小说", "其他",
]

STYLES = [
    "轻松幽默", "热血爽文", "严肃深沉", "悬疑烧脑",
    "甜宠日常", "黑暗压抑", "史诗宏大", "文艺清新",
    "快节奏", "慢热",
]


class NewProjectDialog(QDialog):
    """新建项目对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("dialog_new_project"))
        self.setMinimumWidth(520)
        self._cover_path = ""
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ── 基本信息 ──
        basic_group = QGroupBox("基本信息")
        basic_form = QFormLayout(basic_group)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("请输入小说名称")
        basic_form.addRow("书名 *:", self.title_edit)

        # 封面
        cover_row = QHBoxLayout()
        self.cover_label = QLabel("无封面")
        self.cover_label.setFixedSize(80, 100)
        self.cover_label.setStyleSheet(
            "border: 1px dashed rgba(255,255,255,0.2); "
            "border-radius: 4px; font-size: 11px; color: gray;"
        )
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_row.addWidget(self.cover_label)

        cover_btn_col = QVBoxLayout()
        self.btn_cover = QPushButton("选择封面")
        self.btn_cover.setFixedHeight(28)
        self.btn_cover.clicked.connect(self._pick_cover)
        cover_btn_col.addWidget(self.btn_cover)
        self.btn_clear_cover = QPushButton("清除")
        self.btn_clear_cover.setFixedHeight(28)
        self.btn_clear_cover.clicked.connect(self._clear_cover)
        cover_btn_col.addWidget(self.btn_clear_cover)
        cover_btn_col.addStretch()
        cover_row.addLayout(cover_btn_col)
        basic_form.addRow("封面:", cover_row)

        layout.addWidget(basic_group)

        # ── 题材与风格 ──
        genre_group = QGroupBox("题材与风格")
        genre_form = QFormLayout(genre_group)

        self.genre_combo = QComboBox()
        self.genre_combo.addItems(GENRES)
        self.genre_combo.setEditable(True)
        genre_form.addRow("题材 *:", self.genre_combo)

        self.style_combo = QComboBox()
        self.style_combo.addItems(STYLES)
        self.style_combo.setEditable(True)
        genre_form.addRow("风格 *:", self.style_combo)

        self.words_spin = QSpinBox()
        self.words_spin.setRange(10000, 10000000)
        self.words_spin.setSingleStep(50000)
        self.words_spin.setValue(200000)
        self.words_spin.setSuffix(" 字")
        genre_form.addRow("目标字数:", self.words_spin)

        layout.addWidget(genre_group)

        # ── 立意与方向（必填） ──
        concept_group = QGroupBox("立意与方向（必填）")
        concept_layout = QVBoxLayout(concept_group)

        concept_layout.addWidget(QLabel("核心立意 *：小说要表达什么？核心卖点是什么？"))
        self.theme_edit = QTextEdit()
        self.theme_edit.setPlaceholderText(
            "例：在末世废墟中，一个失去记忆的少年通过破解一个个诡异规则，"
            "逐渐发现自己是唯一能终结这场灾难的关键。核心卖点是规则解密+身份悬疑。"
        )
        self.theme_edit.setMaximumHeight(80)
        concept_layout.addWidget(self.theme_edit)

        concept_layout.addWidget(QLabel("规划方向 *：整体构思、卷数规划、节奏安排。"))
        self.direction_edit = QTextEdit()
        self.direction_edit.setPlaceholderText(
            "例：全书分3卷，第1卷（30章）新手村生存，第2卷（50章）势力对抗，"
            "第3卷（40章）终极真相。前期密集解谜，中期格局扩大，后期情感爆发。"
        )
        self.direction_edit.setMaximumHeight(80)
        concept_layout.addWidget(self.direction_edit)

        layout.addWidget(concept_group)

        # ── 简介 ──
        synopsis_group = QGroupBox("简介")
        syn_layout = QVBoxLayout(synopsis_group)
        self.synopsis_edit = QTextEdit()
        self.synopsis_edit.setPlaceholderText("一句话或一段话介绍你的故事（可后续补充）")
        self.synopsis_edit.setMaximumHeight(60)
        syn_layout.addWidget(self.synopsis_edit)
        layout.addWidget(synopsis_group)

        # ── 按钮 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_cancel = QPushButton(t("settings_cancel"))
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)
        self.btn_create = QPushButton("创建项目")
        self.btn_create.setObjectName("primary")
        self.btn_create.setFixedHeight(34)
        self.btn_create.setFixedWidth(100)
        self.btn_create.clicked.connect(self._on_create)
        btn_row.addWidget(self.btn_create)
        layout.addLayout(btn_row)

    def _pick_cover(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择封面图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if path:
            self._cover_path = path
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    80, 100,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.cover_label.setPixmap(scaled)
            else:
                self.cover_label.setText(Path(path).name)

    def _clear_cover(self):
        self._cover_path = ""
        self.cover_label.clear()
        self.cover_label.setText("无封面")

    def _on_create(self):
        title = self.title_edit.text().strip()
        theme = self.theme_edit.toPlainText().strip()
        direction = self.direction_edit.toPlainText().strip()

        if not title:
            QMessageBox.warning(self, "提示", "请输入书名")
            return
        if not theme:
            QMessageBox.warning(self, "提示", "请填写核心立意")
            return
        if not direction:
            QMessageBox.warning(self, "提示", "请填写规划方向")
            return

        self.accept()

    def get_data(self) -> dict:
        """返回对话框数据。"""
        return {
            "title": self.title_edit.text().strip(),
            "genre": self.genre_combo.currentText(),
            "style": self.style_combo.currentText(),
            "target_words": self.words_spin.value(),
            "theme": self.theme_edit.toPlainText().strip(),
            "direction": self.direction_edit.toPlainText().strip(),
            "synopsis": self.synopsis_edit.toPlainText().strip(),
            "cover": self._cover_path,
        }
