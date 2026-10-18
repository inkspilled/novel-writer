"""Agent 对话与流式输出"""
from __future__ import annotations

import shutil
from functools import partial
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from ..common.workers import AgentWorker
from ..agent.agent_dialog import AgentDialog
from ...core import project_io
from ...core.agents import load_agents
from ...core.workflow import build_character_constraint
from ...locales import t
from ...core.logger import get_logger

logger = get_logger(__name__)

class ChatMixin:
    def _on_agent_run(self, agent_name: str, user_input: str):
        agent = self.agents.get(agent_name)
        logger.debug("_on_agent_run: agent=%r, has_agent=%s, input=%r", agent_name, agent is not None, user_input[:80])
        if not agent:
            QMessageBox.warning(self, t("dialog_error"), t("msg_agent_not_init", agent_name))
            return
        self._stop_worker()
        self._chat_target_chapter = self._parse_target_chapter(user_input)
        context = self._build_context(agent_name)
        logger.debug("context_len=%d, context_preview=%r", len(context), context[:200])
        self.agent_panel.set_working(True, agent_name)
        toolkit = None
        if self.project.project_dir:
            try:
                from ...core.agents.tools import NovelToolkit
                chapter_n = (
                    self._parse_target_chapter(user_input)
                    or getattr(getattr(self.editor, "_current_chapter", None), "number", 0)
                    or len(self.project.chapters) + 1
                )
                toolkit = NovelToolkit(
                    self.project.project_dir,
                    {"title": self.project.title, "genre": self.project.genre,
                     "target_chapters": self.project.target_chapters},
                    chapter_n,
                ).build(None)
            except Exception as e:
                logger.warning("自由对话工具集创建失败（降级纯文本）: %s", e)
        self._worker = AgentWorker(agent, user_input, context, toolkit=toolkit)
        self._worker.chunk_received.connect(partial(self._on_agent_stream, agent_name))
        self._worker.completed.connect(partial(self._on_agent_finished, agent_name))
        self._worker.error.connect(self._on_agent_error)
        self._worker.start()

    @staticmethod
    def _parse_target_chapter(text: str) -> int | None:
        """从用户输入解析目标章节号：第3章 / 第 3 章 / 章节3 / 本章。"""
        import re
        m = re.search(r"第\s*(\d+)\s*章", text)
        if m:
            return int(m.group(1))
        m = re.search(r"章节\s*(\d+)", text)
        if m:
            return int(m.group(1))
        return None

    def _stop_worker(self):
        """安全停止后台工作线程，取消进行中的请求。不无限阻塞 GUI。"""
        if self._worker is not None:
            if self._worker.isRunning():
                self._worker.cancel()
                if not self._worker.wait(2000):
                    logger.debug("AgentWorker 2s 内未退出，转入后台收尾")
            self._old_workers.append(self._worker)
            self._worker = None
        self._cleanup_workers()

    def _cleanup_workers(self):
        """清理旧 worker 列表引用。

        运行中/未收尾的 worker 由 workers._ALIVE_WORKERS 强引用保活到
        内建 finished，这里丢列表引用不会触发 GC 析构崩溃。
        """
        alive = []
        for w in self._old_workers:
            if w is None:
                continue
            try:
                if w.isRunning():
                    alive.append(w)
            except RuntimeError:
                pass  # C++ 对象已销毁
        self._old_workers = alive

    def _build_context(self, agent_name: str) -> str:
        parts = []
        if self.project.title:
            meta = f"项目: {self.project.title}"
            if self.project.genre:
                meta += f" | 题材: {self.project.genre}"
            if self.project.style:
                meta += f" | 风格: {self.project.style}"
            if self.project.theme:
                meta += f" | 主题: {self.project.theme}"
            parts.append(meta)

        # 读取核心规划文档（只读大纲/人物/立意，不读全部）
        if self.project.project_dir:
            planning_dir = self.project.project_dir / "planning"
            if planning_dir.exists():
                core_files = ["大纲.md", "人物设定.md", "立意.md"]
                char_content = ""
                for fname in core_files:
                    f = planning_dir / fname
                    if f.exists():
                        content = project_io.read_md(f)
                        if content:
                            parts.append(f"=== {f.stem} ===\n{content}")
                            if fname == "人物设定.md":
                                char_content = content
                # 角色约束
                if char_content:
                    constraint = build_character_constraint(char_content)
                    if constraint:
                        parts.append(constraint)

        # 只读最近3章内容（不读全部章节）
        if self.project.project_dir:
            chapters_dir = self.project.project_dir / "chapters"
            if chapters_dir.exists():
                recent_chapters = self.project.chapters[-3:] if len(self.project.chapters) > 3 else self.project.chapters
                for ch in recent_chapters:
                    if ch.content:
                        parts.append(f"=== 第{ch.number}章 {ch.title} ===\n{ch.content}")

        return "\n\n".join(parts)

    def _on_agent_finished(self, agent_name: str, response: str):
        try:
            self.agent_panel.set_working(False, agent_name)
            self.agent_panel.finalize_stream_message(agent_name)
            self.agent_panel.save_history()
            agents_cfg = load_agents()
            title = agents_cfg.get(agent_name, {}).get("title", agent_name)
            self.statusBar().showMessage(t("status_agent_done", title))
            # 对话改章节：提到第 N 章时，给出「替换第 N 章」动作条
            n = getattr(self, "_chat_target_chapter", None)
            self._chat_target_chapter = None
            if n and response and len(response.strip()) >= 50:
                self.agent_panel.show_apply_actions(n, response.strip())
        except Exception as e:
            self.agent_panel.set_working(False)
            self.statusBar().showMessage(f"{t('dialog_error')}: {e}")

    def _on_chapter_apply(self, chapter_n: int, content: str):
        """把 AI 输出写进指定章节（确认后落盘）。"""
        logger.info("对话改章节: 第%d章 content_len=%d", chapter_n, len(content or ""))
        reply = QMessageBox.question(
            self, "替换章节",
            f"确定要用 AI 的内容替换第 {chapter_n} 章吗？\n（原文件会先备份为 .bak）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ch = None
        for c in self.project.chapters:
            if c.number == chapter_n:
                ch = c
                break
        if ch is None:
            QMessageBox.warning(self, "提示", f"找不到第 {chapter_n} 章")
            return
        # 备份
        try:
            p = Path(ch._content_path) if ch._content_path else None
            if p and p.exists():
                bak = p.with_suffix(".bak.txt")
                if not bak.exists():
                    shutil.copy2(p, bak)
        except Exception:
            pass
        ch.content = content
        self._save_project()
        logger.info("章节已替换落盘: 第%d章", chapter_n)
        # 若正编辑该章，刷新编辑器
        cur = getattr(self.editor, "_current_chapter", None)
        if cur is not None and cur.number == chapter_n:
            self.editor.load_chapter(ch)
        self._show_toast(f"已替换第 {chapter_n} 章")
        self.statusBar().showMessage(f"第 {chapter_n} 章已更新", 3000)

    def _on_agent_error(self, error: str):
        logger.error("Agent 调用出错: %s", error)
        self.agent_panel.set_working(False)
        self.statusBar().showMessage(f"{t('dialog_error')}: {error}")
        try:
            QMessageBox.critical(self, t("dialog_error"), error)
        except Exception as e:
            logger.debug("弹错误框失败（忽略）: %s", e)

    def _on_agent_stream(self, agent_name: str, kind: str, chunk: str):
        """接收流式文本块，更新UI。kind = thinking | text。"""
        try:
            self.agent_panel.append_stream_chunk(chunk, agent_name, kind=kind)
        except Exception as e:
            logger.debug("流式 UI 更新失败: %s", e)

    # ── 设置对话框 ──

    def _open_character_manager(self):
        """切到主界面的「人物」页。"""
        if not self.project.project_dir:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, t("menu_character_manage"), t("msg_no_project"))
            return
        self.editor.tab_bar.setCurrentIndex(2)

    def _open_agent_manage(self):
        """打开 Agent 管理对话框。"""
        dialog = AgentDialog(self.config, self)
        if dialog.exec() == AgentDialog.DialogCode.Accepted:
            self.config = dialog.get_config()
            self._save_config()
            self._init_agents()
            self.statusBar().showMessage(t("status_settings_saved"))

