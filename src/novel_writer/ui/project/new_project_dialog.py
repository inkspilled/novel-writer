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

from ...core.logger import get_logger
from ...locales import t

logger = get_logger(__name__)

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
    """后台线程调用 Agent（流式），带超时保护。思考/正文分开发出，UI 可实时看。"""
    # 遮蔽 QThread 内建 finished（无参），用带载荷的 completed
    chunk = Signal(str, str)  # (kind, text)：thinking | text
    completed = Signal(str)   # 正文（不含思考）
    error = Signal(str)

    # API 模型 2 分钟，本地模型 10 分钟
    TIMEOUT_API = 120
    TIMEOUT_LOCAL = 600

    def __init__(self, agent, prompt: str, parent=None):
        super().__init__(parent)
        self.agent = agent
        self.prompt = prompt
        self._cancelled = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        # 本地模型（base_url 含 127.0.0.1/localhost 或 OllamaLLM）给更长超时
        base_url = getattr(agent.llm, 'base_url', '') or ''
        is_local = any(h in base_url for h in ('127.0.0.1', 'localhost', '0.0.0.0'))
        self._timeout = self.TIMEOUT_LOCAL if is_local else self.TIMEOUT_API
        # 防 QThread 运行中被 GC（学 smt：finished 后才释放）
        from ..common.workers import _register_worker
        _register_worker(self)

    def cancel(self):
        self._cancelled = True
        loop, task = self._loop, self._task
        if loop is not None and task is not None and not loop.is_closed() and not task.done():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    def run(self):
        loop = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            async def _call():
                buf: list[str] = []  # 只收正文
                gen = self.agent.stream_run(
                    self.prompt,
                    thinking="auto",      # 允许思考，用户要看见
                    keep_history=False,   # 每次独立，不背历史
                )
                try:
                    async for kind, piece in gen:
                        if self._cancelled:
                            break
                        if kind == "text":
                            buf.append(piece)
                        try:
                            self.chunk.emit(kind, piece)
                        except RuntimeError:
                            break
                finally:
                    # 关闭异步生成器，避免 "aclose never awaited / Task destroyed"
                    aclose = getattr(gen, "aclose", None)
                    if aclose is not None:
                        try:
                            await aclose()
                        except Exception:
                            pass
                return "".join(buf)

            self._task = loop.create_task(_call())
            full = loop.run_until_complete(self._task)
            if not self._cancelled:
                self.completed.emit(full or "")
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if not self._cancelled:
                self.error.emit(f"AI 润色超时（{self._timeout}秒），本地模型较慢请耐心等待或换用更快的模型")
        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e)[:300])
        finally:
            self._task = None
            self._loop = None
            if loop:
                # 清掉残留任务再关 loop，消灭 "Task was destroyed but it is pending"
                try:
                    pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                    for t in pending:
                        t.cancel()
                    if pending:
                        loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                except Exception:
                    pass
                try:
                    loop.close()
                except Exception:
                    pass


class NewProjectDialog(QDialog):
    """新建/编辑项目对话框。"""

    def __init__(self, llm=None, parent=None, project_data=None, agents=None):
        super().__init__(parent)
        self.setWindowTitle("编辑项目" if project_data else t("dialog_new_project"))
        self.setMinimumSize(640, 720)
        self.resize(660, 760)
        self._cover_path = ""
        self._llm = llm
        self._agents = agents or {}  # Agent 实例字典
        self._workers: dict[str, _AIWorker] = {}  # field → worker（同时只跑一个）
        self._ai_busy = False  # 串行闸：True 时禁止再发润色请求
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

        # ── 基本信息 + 题材与风格：左右分栏 ──
        top_row = QHBoxLayout()
        top_row.setSpacing(16)

        # ── 基本信息（左） ──
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
        top_row.addWidget(basic_group, 1)

        # ── 题材与风格（右） ──
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

        top_row.addWidget(genre_group, 1)
        layout.addLayout(top_row)

        # ── 立意与方向（必填） ──
        concept_group = QGroupBox("立意与方向（必填）")
        concept_layout = QVBoxLayout(concept_group)
        concept_layout.setSpacing(16)
        concept_layout.setContentsMargins(20, 28, 20, 20)

        # 核心立意
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("核心立意 *"))
        row1.addStretch()
        self.btn_ai_theme = QPushButton("✨ AI 润色")
        self.btn_ai_theme.setFixedHeight(26)
        self.btn_ai_theme.setStyleSheet("font-size: 11px; padding: 2px 10px;")
        self.btn_ai_theme.setToolTip("先在下方框里写草稿，再点此按钮让 AI 润色提升")
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
        self.btn_ai_dir = QPushButton("✨ AI 润色")
        self.btn_ai_dir.setFixedHeight(26)
        self.btn_ai_dir.setStyleSheet("font-size: 11px; padding: 2px 10px;")
        self.btn_ai_dir.setToolTip("先在下方框里写草稿，再点此按钮让 AI 润色提升")
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

        # ── AI 流式输出（思考/润色实时可见）+ 状态 + 按钮（固定底部） ──
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("font-size: 11px; color: gray;")
        main_layout.addWidget(self._status_label)

        self._stream_view = QTextEdit()
        self._stream_view.setReadOnly(True)
        self._stream_view.setPlaceholderText("点「AI 润色」后，这里实时显示思考过程和润色结果…")
        self._stream_view.setMinimumHeight(0)
        self._stream_view.setMaximumHeight(0)  # 初始收起，润色时展开
        self._stream_view.setStyleSheet("font-size: 12px;")
        self._stream_buf = ""  # 兼容旧字段
        self._stream_chunks: list[tuple[str, str]] = []  # (kind, text)
        main_layout.addWidget(self._stream_view)

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
        """AI 润色立意或方向：先读框里已有草稿，再让 AI 改写提升。

        串行执行：同一时刻只允许一个润色请求，避免并发触发网关 429 限流。
        """
        # 根据字段选择对应 Agent：立意→主编(editor)，方向→策划(planner)
        agent_name = "editor" if field == "theme" else "planner"
        agent = self._agents.get(agent_name)
        if not agent:
            QMessageBox.warning(self, "提示", "请先在设置中配置模型和智能体")
            return

        # 串行闸：仅看忙碌标志。不能用 isRunning()——completed 是在 run()
        # 内发出的，此刻线程尚未退出，会把下一次点击误拦住。
        if self._ai_busy:
            QMessageBox.information(
                self, "请稍候",
                "已有一项润色正在进行，等它完成后再试。\n（并发请求容易被服务端限流，反而更慢）",
            )
            return

        label = "核心立意" if field == "theme" else "规划方向"
        edit = self.theme_edit if field == "theme" else self.direction_edit
        draft = edit.toPlainText().strip()
        logger.info("AI 润色请求: field=%s draft_len=%d", field, len(draft))
        if not draft:
            QMessageBox.information(
                self, "提示",
                f"请先在【{label}】框里写下你的想法（哪怕几句话），再点 AI 润色。",
            )
            return

        # 必填校验：书名/题材/风格会进 prompt，缺了润色必歪
        missing: list[str] = []
        if not self.title_edit.text().strip():
            missing.append("书名")
        if not self.genre_combo.currentText().strip():
            missing.append("题材")
        if not self.style_combo.currentText().strip():
            missing.append("风格")
        if missing:
            QMessageBox.warning(
                self, "请先补全",
                "AI 润色需要对齐下面这些信息，否则会写歪：\n\n"
                + "、".join(missing)
                + "\n\n请先在上方【基本信息 / 题材与风格】里填好，再点润色。",
            )
            return

        # 如果该字段已有正在运行的 worker，先取消
        old = self._workers.get(field)
        if old and old.isRunning():
            old.cancel()
            old.wait(2000)
            del self._workers[field]

        genre = self.genre_combo.currentText().strip()
        style = self.style_combo.currentText().strip()
        title = self.title_edit.text().strip()

        # 互相关联：润色其中一个字段时，注入另一个字段的现有内容作为上下文
        current_theme = self.theme_edit.toPlainText().strip()
        current_direction = self.direction_edit.toPlainText().strip()

        if field == "theme":
            ref = f"\n\n【已有规划方向（必须保持一致）】\n{current_direction}" if current_direction else ""
            prompt = f"""你是小说主编。请润色下面这段核心立意草稿：保留用户原意和关键信息，补全结构、压缩废话、提升表达。必须严格贴合给定的题材与风格，书名相关表述不得偏离。

题材：{genre}  风格：{style}  书名：《{title}》{ref}

用户草稿：
{draft}

严格按以下格式输出，总字数不超过200字：

【核心主题】（10字内的一句话概括）
【立意】（2-3句话阐述核心内涵，不超过80字）
【卖点】（2-3个，每个10字内，用逗号分隔）
【读者】（目标读者群体，20字内）

不要有任何多余解释。"""
        else:
            ref = f"\n\n【已有核心立意（必须严格保持一致的题材和风格）】\n{current_theme}" if current_theme else ""
            prompt = f"""你是小说策划。请润色下面这段规划方向草稿：保留用户原意和卷章安排，补全结构、压缩废话、提升表达。必须严格贴合给定的题材与风格，卷章规划不得偏离书名调性。

题材：{genre}  风格：{style}  书名：《{title}》{ref}

用户草稿：
{draft}

严格按以下格式输出，总字数不超过200字：

【整体构思】（30字内概括）
【分卷】（用"→"连接各卷，格式：卷名(章节数)→卷名(章节数)→…）
【节奏】（20字内说明高潮分布）
【预估字数】（如"约60万字"）

不要有任何多余解释。"""

        self._set_generating(True, field)
        self._ai_busy = True
        logger.info("AI 润色调用: field=%s agent=%s prompt_len=%d", field, agent_name, len(prompt))
        # 不设 parent：对话框关闭时由 closeEvent 等待线程结束后再销毁
        worker = _AIWorker(agent, prompt, None)
        worker.chunk.connect(self._on_ai_chunk)
        worker.completed.connect(lambda text: self._on_ai_done(field, text))
        worker.error.connect(lambda err: self._on_ai_error(err, field))
        # 内建 finished（run 真正返回后）兜底放行，防止 done 信号丢失卡死
        worker.finished.connect(lambda f=field: self._release_ai_worker(f))
        self._workers[field] = worker
        worker.start()

    def _release_ai_worker(self, field: str = ""):
        """请求结束（完成/失败/线程退出）后立刻放行下一个。"""
        if field:
            self._workers.pop(field, None)
        self._ai_busy = False

    def _on_ai_chunk(self, kind: str, piece: str):
        """流式增量：thinking 灰字、text 常规字，自动滚到底。"""
        self._stream_chunks.append((kind, piece))
        self._stream_view.setHtml(self._render_stream_html())
        sb = self._stream_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _render_stream_html(self) -> str:
        """把 (kind,text) 分片合并后渲染：同 kind 连成一段，思考整段一个 💭。

        绝不能每个分片一个 <br/>+💭——流式按 2～3 字切片，那样会碎成渣。
        """
        import html as _html

        def esc(s: str) -> str:
            return _html.escape(s)

        # 合并相邻同 kind
        merged: list[list[str]] = []
        for kind, text in self._stream_chunks:
            if not text:
                continue
            if merged and merged[-1][0] == kind:
                merged[-1][1] += text
            else:
                merged.append([kind, text])

        out: list[str] = []
        for kind, text in merged:
            if kind == "thinking":
                body = esc(text.strip())
                out.append(
                    "<div style='color:#8e8e9a;font-style:italic;margin:2px 0;'>"
                    f"💭 {body}</div>"
                )
            else:
                out.append(
                    "<div style='margin:2px 0;'>"
                    f"{esc(text).replace(chr(10), '<br/>')}</div>"
                )
        return "".join(out)

    # 每个字段只保留自己那几节，防止模型顺手把另一个字段的格式也吐出来
    _FIELD_KEYS = {
        "theme": ("核心主题", "立意", "卖点", "读者"),
        "direction": ("整体构思", "分卷", "节奏", "预估字数"),
    }

    @classmethod
    def _extract_formatted(cls, body: str, field: str = "") -> str:
        """从润色正文里只抽出 【xxx】 格式行，去掉元评论；按 field 过滤小节。"""
        import re
        allowed = cls._FIELD_KEYS.get(field, ())
        kept: list[str] = []
        for line in body.splitlines():
            line = line.strip()
            m = re.match(r"^(【([^】]+)】)(.*)$", line)
            if not m:
                continue
            title, key, content = m.group(1), m.group(2).strip(), m.group(3).strip()
            # 只留本字段的小节（theme 不写整体构思，反之亦然）
            if field and allowed and key not in allowed:
                continue
            # 去开头「30字内：」这类字数提示
            content = re.sub(r"^\d+\s*字内\s*[：:]\s*", "", content)
            # 在「。约20字…」「。控制总字数」处截断元评论
            content = re.split(
                r"[。．]\s*(?=约?\s*\d+\s*字|控制总字数|格式准确|微调|不要有多余)",
                content,
            )[0].strip()
            kept.append(f"{title}{content}")
        return "\n".join(kept) if kept else body.strip()

    def _on_ai_done(self, field: str, text: str):
        logger.info("AI 润色完成: field=%s raw_len=%d", field, len(text or ""))
        self._release_ai_worker(field)
        self._set_generating(False, field)
        # completed 只含正文；按 field 只保留本字段的小节，防止写串
        body = (text or "").strip()
        extracted = self._extract_formatted(body, field=field)
        # 抽不出格式段时回退原文，绝不能出现「润色完了框里是空的」
        text = extracted if extracted.strip() else body
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
        self._status_label.setText("润色完成，结果已填入上方输入框（思考过程见底部）")

    def _on_ai_error(self, error: str, field: str = ""):
        logger.error("AI 润色失败: field=%s err=%s", field, error)
        self._release_ai_worker(field)
        self._set_generating(False, field)
        self._status_label.setText("润色失败")
        QMessageBox.warning(self, "AI 润色失败", error)

    def _set_generating(self, generating: bool, field: str = ""):
        """设置润色状态，field 指定是哪个字段（'theme'/'direction'）。"""
        if generating:
            # 串行：润色期间两个按钮都锁死，防止并发触发 429
            self.btn_ai_theme.setEnabled(False)
            self.btn_ai_dir.setEnabled(False)
            self.btn_ai_theme.setText("润色中...")
            self.btn_ai_dir.setText("润色中...")
            self.btn_create.setEnabled(False)
            self._status_label.setText("AI 正在润色（思考过程见下）…")
            # 展开流式窗并清空
            self._stream_chunks = []
            self._stream_view.clear()
            self._stream_view.setMinimumHeight(140)
            self._stream_view.setMaximumHeight(220)
        else:
            # 恢复所有按钮
            self.btn_ai_theme.setEnabled(True)
            self.btn_ai_dir.setEnabled(True)
            self.btn_create.setEnabled(True)
            self.btn_ai_theme.setText("✨ AI 润色")
            self.btn_ai_dir.setText("✨ AI 润色")

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
        """关闭对话框时清理所有 worker：先取消并等待结束，避免销毁运行中的 QThread。"""
        for w in list(self._workers.values()):
            w.cancel()
        for w in list(self._workers.values()):
            if w.isRunning() and not w.wait(5000):
                # 仍未结束则脱离父子关系并自行回收，不能阻塞 UI 更久
                w.setParent(None)
                w.finished.connect(w.deleteLater)
        self._workers.clear()
        super().closeEvent(event)
