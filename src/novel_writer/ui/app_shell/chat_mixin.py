"""智能体管理、上下文与后台线程清理。"""
from __future__ import annotations



from ..agent.agent_dialog import AgentDialog
from ...core import project_io
from ...core.workflow import build_character_constraint
from ...locales import t
from ...core.logger import get_logger

logger = get_logger(__name__)

class ChatMixin:


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
            if self.project.narrative_voice:
                meta += f" | 语言质感: {self.project.narrative_voice}"
            if self.project.meme_level:
                meta += f" | 梗密度: {self.project.meme_level}"
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





    # ── 设置对话框 ──

    def _open_agent_manage(self):
        """打开 Agent 管理对话框。"""
        dialog = AgentDialog(self.config, self)
        dialog.domain_tabs.setCurrentIndex(self.mode_tabs.currentIndex())
        if dialog.exec() == AgentDialog.DialogCode.Accepted:
            self.config = dialog.get_config()
            self._save_config()
            self._init_agents()
            self.statusBar().showMessage(t("status_settings_saved"))

