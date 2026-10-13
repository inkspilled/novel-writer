"""Agent 对话与流式输出"""
from __future__ import annotations

from functools import partial

from PySide6.QtWidgets import QMessageBox

from ..common.workers import AgentWorker
from ..agent.agent_dialog import AgentDialog
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
        context = self._build_context(agent_name)
        logger.debug("context_len=%d, context_preview=%r", len(context), context[:200])
        self.agent_panel.set_working(True, agent_name)
        self._worker = AgentWorker(agent, user_input, context)
        self._worker.chunk_received.connect(partial(self._on_agent_stream, agent_name))
        self._worker.completed.connect(partial(self._on_agent_finished, agent_name))
        self._worker.error.connect(self._on_agent_error)
        self._worker.start()

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
        """清理已结束的旧 worker 引用。"""
        self._old_workers = [w for w in self._old_workers if w.isRunning()]

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
        except Exception as e:
            self.agent_panel.set_working(False)
            self.statusBar().showMessage(f"{t('dialog_error')}: {e}")

    def _on_agent_error(self, error: str):
        logger.error("Agent 调用出错: %s", error)
        self.agent_panel.set_working(False)
        self.statusBar().showMessage(f"{t('dialog_error')}: {error}")
        try:
            QMessageBox.critical(self, t("dialog_error"), error)
        except Exception as e:
            logger.debug("弹错误框失败（忽略）: %s", e)

    def _on_agent_stream(self, agent_name: str, chunk: str):
        """接收流式文本块，更新UI"""
        try:
            self.agent_panel.append_stream_chunk(chunk, agent_name)
        except Exception as e:
            logger.debug("流式 UI 更新失败: %s", e)

    # ── 设置对话框 ──

    def _open_agent_manage(self):
        """打开 Agent 管理对话框。"""
        dialog = AgentDialog(self.config, self)
        if dialog.exec() == AgentDialog.DialogCode.Accepted:
            self.config = dialog.get_config()
            self._save_config()
            self._init_agents()
            self.statusBar().showMessage(t("status_settings_saved"))

