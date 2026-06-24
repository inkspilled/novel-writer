"""新建项目对话框 — 完整的项目初始化流程，支持 AI 生成。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTextEdit, QComboBox, QSpinBox, QPushButton, QFileDialog,
    QGroupBox, QFormLayout, QInputDialog, QMessageBox, QWidget,
    QScrollArea,
)
from PySide6.QtCore import Qt, Signal, QThread
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


class _AIWorker(QThread):
    """后台线程调用 LLM，带超时保护。"""
    finished = Signal(str)
    error = Signal(str)

    TIMEOUT_SEC = 60  # 60秒超时

    def __init__(self, llm, prompt: str, parent=None):
        super().__init__(parent)
        self.llm = llm
        self.prompt = prompt
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        loop = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            from ..core.llm.base import LLMMessage
            messages = [LLMMessage(role="user", content=self.prompt)]

            # 带超时的 LLM 调用
            async def _call():
                return await asyncio.wait_for(
                    self.llm.chat(messages, temperature=0.7, max_tokens=300),
                    timeout=self.TIMEOUT_SEC,
                )

            resp = loop.run_until_complete(_call())
            if not self._cancelled:
                self.finished.emit(resp.content)
        except asyncio.TimeoutError:
            if not self._cancelled:
                self.error.emit(f"AI 生成超时（{self.TIMEOUT_SEC}秒），请检查网络或模型配置")
        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e)[:300])
        finally:
            if loop:
                try:
                    # 关闭 LLM 客户端连接
                    loop.run_until_complete(self._cleanup_llm())
                except Exception:
                    pass
                loop.close()

    async def _cleanup_llm(self):
        try:
            if hasattr(self.llm, 'client') and hasattr(self.llm.client, 'close'):
                await self.llm.client.close()
        except Exception:
            pass


class NewProjectDialog(QDialog):
    """新建/编辑项目对话框。"""

    def __init__(self, llm=None, parent=None, project_data=None):
        super().__init__(parent)
        self.setWindowTitle("编辑项目" if project_data else t("dialog_new_project"))
        self.setMinimumSize(640, 720)
        self.resize(660, 760)
        self._cover_path = ""
        self._llm = llm
        self._worker = None
        self._project_data = project_data
        self._setup_ui()
        if project_data:
            self._load_data(project_data)

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 16)
        main_layout.setSpacing(12)

        # 整体滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(20)
        layout.setContentsMargins(4, 4, 4, 4)

        # ── 基本信息 ──
        basic_group = QGroupBox("基本信息")
        basic_form = QFormLayout(basic_group)
        basic_form.setSpacing(14)
        basic_form.setContentsMargins(20, 28, 20, 20)
        basic_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("请输入小说名称")
        self.title_edit.setMinimumHeight(36)
        if self._project_data:
            self.title_edit.setEnabled(False)
        basic_form.addRow("书名 *", self.title_edit)

        # 封面
        cover_row = QHBoxLayout()
        cover_row.setSpacing(14)
        self.cover_label = QLabel("无封面")
        self.cover_label.setFixedSize(90, 120)
        self.cover_label.setStyleSheet(
            "border: 1px dashed rgba(255,255,255,0.15); "
            "border-radius: 8px; font-size: 11px; color: gray;"
        )
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_row.addWidget(self.cover_label)

        cover_btn_col = QVBoxLayout()
        cover_btn_col.setSpacing(8)
        self.btn_cover = QPushButton("选择封面")
        self.btn_cover.setFixedHeight(32)
        self.btn_cover.clicked.connect(self._pick_cover)
        cover_btn_col.addWidget(self.btn_cover)
        self.btn_clear_cover = QPushButton("清除")
        self.btn_clear_cover.setFixedHeight(32)
        self.btn_clear_cover.clicked.connect(self._clear_cover)
        cover_btn_col.addWidget(self.btn_clear_cover)
        cover_btn_col.addStretch()
        cover_row.addLayout(cover_btn_col)
        basic_form.addRow("封面", cover_row)
        layout.addWidget(basic_group)

        # ── 题材与风格 ──
        genre_group = QGroupBox("题材与风格")
        genre_form = QFormLayout(genre_group)
        genre_form.setSpacing(14)
        genre_form.setContentsMargins(20, 28, 20, 20)
        genre_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.genre_combo = QComboBox()
        self.genre_combo.addItems(GENRES)
        self.genre_combo.setEditable(True)
        self.genre_combo.setMinimumHeight(34)
        genre_form.addRow("题材 *", self.genre_combo)

        self.style_combo = QComboBox()
        self.style_combo.addItems(STYLES)
        self.style_combo.setEditable(True)
        self.style_combo.setMinimumHeight(34)
        genre_form.addRow("风格 *", self.style_combo)

        self.words_spin = QSpinBox()
        self.words_spin.setRange(10000, 10000000)
        self.words_spin.setSingleStep(50000)
        self.words_spin.setValue(200000)
        self.words_spin.setSuffix(" 字")
        self.words_spin.setMinimumHeight(34)
        genre_form.addRow("目标字数", self.words_spin)

        self.chapters_spin = QSpinBox()
        self.chapters_spin.setRange(10, 99999)
        self.chapters_spin.setSingleStep(50)
        self.chapters_spin.setValue(100)
        self.chapters_spin.setSuffix(" 章")
        self.chapters_spin.setMinimumHeight(34)
        genre_form.addRow("目标章节数", self.chapters_spin)

        layout.addWidget(genre_group)

        # ── 立意与方向（必填） ──
        concept_group = QGroupBox("立意与方向（必填）")
        concept_layout = QVBoxLayout(concept_group)
        concept_layout.setSpacing(16)
        concept_layout.setContentsMargins(20, 28, 20, 20)

        # 核心立意
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("核心立意 *"))
        row1.addStretch()
        self.btn_ai_theme = QPushButton("✨ AI 生成")
        self.btn_ai_theme.setFixedHeight(26)
        self.btn_ai_theme.setStyleSheet("font-size: 11px; padding: 2px 10px;")
        self.btn_ai_theme.clicked.connect(lambda: self._ai_generate("theme"))
        row1.addWidget(self.btn_ai_theme)
        concept_layout.addLayout(row1)

        hint1 = QLabel("小说要表达什么？核心卖点是什么？")
        hint1.setStyleSheet("font-size: 11px; color: gray; margin-bottom: 2px;")
        concept_layout.addWidget(hint1)

        self.theme_edit = QTextEdit()
        self.theme_edit.setPlaceholderText(
            "例：在末世废墟中，一个失去记忆的少年通过破解一个个诡异规则，\n"
            "逐渐发现自己是唯一能终结这场灾难的关键。核心卖点是规则解密+身份悬疑。"
        )
        self.theme_edit.setMinimumHeight(90)
        self.theme_edit.setMaximumHeight(140)
        concept_layout.addWidget(self.theme_edit)

        # 规划方向
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("规划方向 *"))
        row2.addStretch()
        self.btn_ai_dir = QPushButton("✨ AI 生成")
        self.btn_ai_dir.setFixedHeight(26)
        self.btn_ai_dir.setStyleSheet("font-size: 11px; padding: 2px 10px;")
        self.btn_ai_dir.clicked.connect(lambda: self._ai_generate("direction"))
        row2.addWidget(self.btn_ai_dir)
        concept_layout.addLayout(row2)

        hint2 = QLabel("整体构思、卷数规划、节奏安排")
        hint2.setStyleSheet("font-size: 11px; color: gray; margin-bottom: 2px;")
        concept_layout.addWidget(hint2)

        self.direction_edit = QTextEdit()
        self.direction_edit.setPlaceholderText(
            "例：全书分3卷，第1卷（30章）新手村生存，第2卷（50章）势力对抗，\n"
            "第3卷（40章）终极真相。前期密集解谜，中期格局扩大，后期情感爆发。"
        )
        self.direction_edit.setMinimumHeight(90)
        self.direction_edit.setMaximumHeight(140)
        concept_layout.addWidget(self.direction_edit)
        layout.addWidget(concept_group)

        # ── 简介 ──
        synopsis_group = QGroupBox("简介（可选）")
        syn_layout = QVBoxLayout(synopsis_group)
        syn_layout.setContentsMargins(20, 28, 20, 20)
        self.synopsis_edit = QTextEdit()
        self.synopsis_edit.setPlaceholderText("一句话或一段话介绍你的故事（可后续补充）")
        self.synopsis_edit.setMinimumHeight(70)
        self.synopsis_edit.setMaximumHeight(120)
        syn_layout.addWidget(self.synopsis_edit)
        layout.addWidget(synopsis_group)

        layout.addStretch()

        scroll.setWidget(content)
        main_layout.addWidget(scroll, 1)

        # ── 状态栏 + 按钮（固定在底部） ──
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("font-size: 11px; color: gray;")
        main_layout.addWidget(self._status_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()
        self.btn_cancel = QPushButton(t("settings_cancel"))
        self.btn_cancel.setFixedHeight(36)
        self.btn_cancel.setFixedWidth(90)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)
        self.btn_create = QPushButton("创建项目" if not self._project_data else "保存")
        self.btn_create.setObjectName("primary")
        self.btn_create.setFixedHeight(36)
        self.btn_create.setFixedWidth(110)
        self.btn_create.clicked.connect(self._on_create)
        btn_row.addWidget(self.btn_create)
        main_layout.addLayout(btn_row)

    def _ai_generate(self, field: str):
        """AI 生成立意或方向。"""
        if not self._llm:
            QMessageBox.warning(self, "提示", "请先在设置中配置模型")
            return

        # 如果有正在运行的 worker，先取消
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
            self._worker = None

        label = "核心立意" if field == "theme" else "规划方向"
        hint = "一个少年穿越到异世界" if field == "theme" else "分3卷，前期升级，中期争霸，后期收尾"

        one_liner, ok = QInputDialog.getText(
            self, f"AI 生成{label}",
            f"用一句话描述你的想法，AI 会帮你展开：\n（例：{hint}）",
        )
        if not ok or not one_liner.strip():
            return

        genre = self.genre_combo.currentText()
        style = self.style_combo.currentText()
        title = self.title_edit.text().strip() or "未定"

        if field == "theme":
            prompt = f"""你是小说主编。根据一句话想法写核心立意。

题材：{genre}  风格：{style}  书名：{title}
用户想法：{one_liner}

严格按以下格式输出，总字数不超过200字：

【核心主题】（10字内的一句话概括）
【立意】（2-3句话阐述核心内涵，不超过80字）
【卖点】（2-3个，每个10字内，用逗号分隔）
【读者】（目标读者群体，20字内）

不要有任何多余解释。"""
        else:
            prompt = f"""你是小说策划。根据一句话方向写规划方向。

题材：{genre}  风格：{style}  书名：{title}
用户想法：{one_liner}

严格按以下格式输出，总字数不超过200字：

【整体构思】（30字内概括）
【分卷】（用"→"连接各卷，格式：卷名(章节数)→卷名(章节数)→…）
【节奏】（20字内说明高潮分布）
【预估字数】（如"约60万字"）

不要有任何多余解释。"""

        self._set_generating(True)
        self._worker = _AIWorker(self._llm, prompt, self)
        self._worker.finished.connect(lambda text: self._on_ai_done(field, text))
        self._worker.error.connect(self._on_ai_error)
        self._worker.start()

    def _on_ai_done(self, field: str, text: str):
        self._set_generating(False)
        text = text.strip()
        if len(text) > 300:
            for sep in ["\n\n", "\n", "。", "；"]:
                idx = text.rfind(sep, 0, 300)
                if idx > 100:
                    text = text[:idx + 1]
                    break
            else:
                text = text[:300]
        if field == "theme":
            self.theme_edit.setPlainText(text)
        else:
            self.direction_edit.setPlainText(text)
        self._status_label.setText("")

    def _on_ai_error(self, error: str):
        self._set_generating(False)
        self._status_label.setText("")
        QMessageBox.warning(self, "AI 生成失败", error)

    def _set_generating(self, generating: bool):
        self.btn_ai_theme.setEnabled(not generating)
        self.btn_ai_dir.setEnabled(not generating)
        self.btn_create.setEnabled(not generating)
        if generating:
            self._status_label.setText("AI 正在生成...")
            self.btn_ai_theme.setText("生成中...")
            self.btn_ai_dir.setText("生成中...")
        else:
            self.btn_ai_theme.setText("✨ AI 生成")
            self.btn_ai_dir.setText("✨ AI 生成")

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

    def _load_data(self, data: dict):
        """加载项目数据到对话框。"""
        self.title_edit.setText(data.get("title", ""))
        self.genre_combo.setCurrentText(data.get("genre", ""))
        self.style_combo.setCurrentText(data.get("style", ""))
        self.words_spin.setValue(data.get("target_words", 200000))
        self.theme_edit.setPlainText(data.get("theme", ""))
        self.direction_edit.setPlainText(data.get("direction", ""))
        self.synopsis_edit.setPlainText(data.get("synopsis", ""))
        # 加载封面
        cover = data.get("cover", "")
        if cover:
            project_dir = data.get("_project_dir")
            if project_dir:
                cover_path = Path(project_dir) / cover
                if cover_path.exists():
                    self._cover_path = str(cover_path)
                    pixmap = QPixmap(str(cover_path))
                    if not pixmap.isNull():
                        scaled = pixmap.scaled(
                            80, 100,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        self.cover_label.setPixmap(scaled)

    def _on_create(self):
        title = self.title_edit.text().strip()
        theme = self.theme_edit.toPlainText().strip()
        direction = self.direction_edit.toPlainText().strip()

        if not title:
            QMessageBox.warning(self, "提示", "请输入书名")
            return
        if not theme:
            QMessageBox.warning(self, "提示", "请填写核心立意（或用 AI 生成）")
            return
        if not direction:
            QMessageBox.warning(self, "提示", "请填写规划方向（或用 AI 生成）")
            return

        self.accept()

    def get_data(self) -> dict:
        """返回对话框数据。"""
        return {
            "title": self.title_edit.text().strip(),
            "genre": self.genre_combo.currentText(),
            "style": self.style_combo.currentText(),
            "target_words": self.words_spin.value(),
            "target_chapters": self.chapters_spin.value(),
            "theme": self.theme_edit.toPlainText().strip(),
            "direction": self.direction_edit.toPlainText().strip(),
            "synopsis": self.synopsis_edit.toPlainText().strip(),
            "cover": self._cover_path,
        }

    def closeEvent(self, event):
        """关闭对话框时清理 worker。"""
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        super().closeEvent(event)
