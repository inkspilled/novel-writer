"""工作流启动/进度/停止"""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QMessageBox

from ..workflow.workflow_panel import WorkflowThread
from ..project.project_dialogs import WorkflowModeDialog
from ...core.workflow import (
    WorkflowRunner, WorkflowDef, DEFAULT_WORKFLOW, WorkflowMode, build_workflow,
    PLANNING_STEP_IDS,
)
from ...core import project_io
from ...locales import t
from ...core.logger import get_logger

logger = get_logger(__name__)

class WorkflowMixin:
    def _setup_workflow(self):
        """初始化工作流：加载定义，连接信号。"""
        bar = self.agent_panel.workflow_bar
        bar.start_requested.connect(self._workflow_start)
        bar.stop_requested.connect(self._workflow_stop)
        self._wf_runner: WorkflowRunner | None = None
        self._wf_thread: WorkflowThread | None = None
        self._wf_def: WorkflowDef | None = None
        self._wf_progress: dict = {}

    def _default_wf_data(self) -> dict:
        """默认工作流定义（带当前项目信息）。"""
        return {
            "name": DEFAULT_WORKFLOW["name"],
            "description": DEFAULT_WORKFLOW["description"],
            "steps": DEFAULT_WORKFLOW["steps"],
            "project": {
                "title": self.project.title,
                "genre": self.project.genre,
                "style": self.project.style,
                "target_chapters": self.project.target_chapters or 100,
            },
        }

    def _load_workflow_for_project(self):
        """为当前项目加载工作流定义。"""
        if not self.project.project_dir:
            return
        saved_wf = project_io.load_workflow(self.project.project_dir)
        self._wf_progress = saved_wf.get("progress", {})
        wf_data = saved_wf.get("workflow")
        if wf_data:
            self._wf_def = WorkflowDef.from_dict(wf_data)
        else:
            self._wf_def = WorkflowDef.from_dict(self._default_wf_data())
        self.agent_panel.workflow_bar.set_has_workflow(True)
        self.agent_panel.workflow_bar.set_progress(
            self._wf_percent(self._wf_progress, self._wf_def)
        )
        self.agent_panel.workflow_bar.set_summary(
            self._wf_summary(self._wf_progress), self._wf_body_percent()
        )

    def _workflow_start(self):
        """开始执行工作流 — 先弹出模式选择对话框。"""
        if not self.project.title:
            QMessageBox.information(self, t("dialog_prompt"), t("workflow_no_project"))
            return
        if not self.agents:
            QMessageBox.information(self, t("dialog_prompt"), t("workflow_no_agents"))
            return

        # 断点恢复：检测上次未完成的进度，询问用户（返回 True 表示已处理，直接返回）
        if self._try_resume_workflow():
            return

        dialog = WorkflowModeDialog(self.project.project_dir, self.project.target_chapters or 100, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        mode, start_ch, end_ch = dialog.get_result()

        # 保存目标章节数到项目配置
        if end_ch > 0:
            self.project.target_chapters = end_ch
            self.project.save()

        # 新书模式：检查是否已有章节内容
        if mode in (WorkflowMode.NEW_BOOK, WorkflowMode.NEW_BOOK_PLANNING) and self.project.project_dir:
            chapters = project_io.scan_chapters(self.project.project_dir)
            has_content = any(c["content_path"].stat().st_size > 0 for c in chapters)
            if has_content:
                QMessageBox.warning(
                    self, t("dialog_prompt"),
                    "该项目已有章节内容，不能执行新书工作流。\n请使用「续写」或「查漏补缺」模式。"
                )
                return

        # 构建工作流
        project_info = {
            "title": self.project.title,
            "genre": self.project.genre,
            "style": self.project.style,
            "theme": self.project.theme,
            "target_chapters": end_ch,
            "seal_chapters": self.config.get("seal_chapters", False),
            "enable_offscreen_tick": self.config.get("enable_offscreen_tick", True),
            "_project_dir": str(self.project.project_dir),
        }
        self._wf_def = build_workflow(mode, project_info, start_ch, end_ch)
        self._wf_progress = {}

        # 保存工作流定义
        wf_dict = self._wf_def.to_dict()
        project_io.save_workflow(self.project.project_dir, {
            "workflow": wf_dict,
            "progress": {},
        })

        self._launch_workflow(self._wf_def, self._wf_progress)

    def _try_resume_workflow(self) -> bool:
        """检测 workflow.json 中的未完成进度，询问是否从断点继续。

        Returns:
            True 表示启动流程已被处理（已续跑或用户取消），调用方直接返回；
            False 表示无进度或用户选择重新开始，走正常新建流程。
        """
        if not self.project.project_dir:
            return False
        saved = project_io.load_workflow(self.project.project_dir)
        progress = saved.get("progress") or {}
        wf_data = saved.get("workflow")
        if not wf_data or not self._progress_incomplete(progress):
            return False
        ret = QMessageBox.question(
            self, t("dialog_prompt"),
            "检测到上次工作流未完成，是否从断点继续？\n（选择「No」将重新开始并覆盖进度）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
        )
        if ret == QMessageBox.StandardButton.Cancel:
            return True
        if ret != QMessageBox.StandardButton.Yes:
            return False
        self._wf_def = WorkflowDef.from_dict(wf_data)
        self._wf_progress = dict(progress)
        self._launch_workflow(self._wf_def, self._wf_progress)
        return True

    @staticmethod
    def _progress_incomplete(progress: dict) -> bool:
        """进度里是否存在未完成的步骤（循环步骤看 current 是否跑满；下划线开头为内部键）。"""
        for k, v in progress.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict):
                if v.get("current", 0) < v.get("total", 1):
                    return True
            elif v != "done":
                return True
        return False

    def _embedding_config(self) -> dict | None:
        """解析 embedding 配置：model 为空 = 不启用语义检索；base_url/key 留空回落当前供应商。"""
        cfg = dict(self.config.get("embedding") or {})
        if not cfg.get("model"):
            return None
        provider = self.config.get("current_provider") or {}
        if not cfg.get("base_url"):
            cfg["base_url"] = provider.get("base_url", "")
        if not cfg.get("api_key"):
            cfg["api_key"] = provider.get("api_key", "")
        return cfg

    def _launch_workflow(self, wf_def, progress: dict):
        """创建工作流执行器与后台线程并启动。"""
        from ...core.rag import configure_embedding
        configure_embedding(self._embedding_config())
        max_context_tokens = self.config.get("max_context_tokens", 48_000)
        self._wf_runner = WorkflowRunner(
            agents=self.agents,
            project_dir=self.project.project_dir,
            project_info=wf_def.project,
            max_context_tokens=max_context_tokens,
            rotate_agents=self.config.get("rotate_agents", False),
            enable_tools=self.config.get("enable_tools", True),
        )

        self._wf_thread = WorkflowThread(
            self._wf_runner, wf_def, progress
        )

        # 连接线程信号（WorkflowThread.run() 会覆盖 runner 回调，所以连信号）
        self._wf_thread.step_started.connect(self._on_wf_step_started)
        self._wf_thread.step_finished.connect(self._on_wf_step_finished)
        self._wf_thread.step_error.connect(self._on_wf_step_error)
        self._wf_thread.progress_updated.connect(self._on_wf_progress)
        self._wf_thread.log_message.connect(self._on_wf_log)
        self._wf_thread.chapter_chunk.connect(self._on_wf_chapter_chunk)
        self._wf_thread.workflow_done.connect(self._on_wf_done)
        self._wf_thread.workflow_stopped.connect(self._on_wf_stopped)

        self.agent_panel.workflow_bar.set_usage({})
        self.agent_panel.on_workflow_started()
        self._wf_thread.start()

    def _on_wf_chapter_chunk(self, step_id: str, n: int, kind: str, text: str):
        if step_id != "chapter" or kind != "text" or not text:
            return
        self.editor.append_stream_chapter(n, text)

    def _on_wf_step_started(self, step_id: str, n: int, agent_title: str):
        self.agent_panel.on_workflow_step_started(step_id, n, agent_title)
        self.agent_panel.workflow_bar.set_current_step(step_id, agent_title, n, 0)
        # 工作流日志只走 WorkflowMiniBar / runs，不写聊天记录，避免污染 chat.db

    def _on_wf_step_finished(self, step_id: str, n: int, agent_title: str, response: str):
        self.agent_panel.on_workflow_step_finished(step_id, n, agent_title)
        # 章节步骤完成后：结束流式预览，刷新侧边栏并载入最终文件
        if step_id == "chapter":
            self.editor.end_stream_chapter()
            self._refresh_chapters()
            for ch in self.project.chapters:
                if ch.number == n:
                    self.editor.load_chapter(ch)
                    break
        # 规划步骤 / 规划反哺（含章节细纲）完成后刷新编辑器
        if step_id in PLANNING_STEP_IDS or step_id.startswith("update_"):
            self.editor.refresh_planning()

    def _on_wf_step_error(self, step_id: str, error: str):
        if step_id == "chapter":
            self.editor.end_stream_chapter()
        self.agent_panel.on_workflow_step_error(step_id, error)

    def _on_wf_log(self, msg: str):
        self.agent_panel.workflow_bar.append_log(msg)
        self.statusBar().showMessage(msg, 5000)

    def _workflow_stop(self):
        if self._wf_thread and self._wf_thread.isRunning():
            self._wf_thread.request_stop()

    def _wf_summary(self, progress: dict | None = None) -> str:
        """两段式摘要：规划完成数 + 正文完成数/目标数。"""
        progress = progress or self._wf_progress or {}
        steps = getattr(self._wf_def, "steps", None) or []
        planning = [s for s in steps if s.id in PLANNING_STEP_IDS]
        if planning:
            done = sum(1 for s in planning if progress.get(s.id) == "done")
            plan_text = f"规划 {done}/{len(planning)}"
        else:
            plan_text = "规划 —"
        chapters = len(self.project.chapters)
        target = self.project.target_chapters or 0
        return f"{plan_text} · 正文 {chapters}/{target}" if target else f"{plan_text} · 正文 {chapters}"

    def _wf_body_percent(self) -> int:
        target = self.project.target_chapters or 0
        if target <= 0:
            return 0
        return int(min(1.0, len(self.project.chapters) / target) * 100)

    @staticmethod
    def _wf_percent(progress: dict, wf_def) -> int:
        """按步骤完成度计算百分比：循环步按 current/total 折算，周期键不参与。

        旧实现只数 "done" 字符串，循环步是 dict 永远不计入；periodic 的
        `sim_3=done` 之类的键又会虚增，进度条会乱跳/提前满格。
        """
        # 只统计主步骤（every==0）；周期步骤的完成键是 `id_n`，不进总进度
        steps = [s for s in (getattr(wf_def, "steps", None) or []) if s.every == 0]
        if not steps:
            return 0
        score = 0.0
        for step in steps:
            value = progress.get(step.id)
            if step.repeat > 0:
                if isinstance(value, dict):
                    total = max(1, int(value.get("total") or step.repeat or 1))
                    score += min(1.0, max(0.0, int(value.get("current") or 0) / total))
            elif value == "done":
                score += 1.0
        return int(score / len(steps) * 100)

    def _on_wf_progress(self, progress: dict):
        self._wf_progress = progress
        self.agent_panel.workflow_bar.set_usage(progress.get("_usage") or {})
        self.agent_panel.on_workflow_progress(
            self._wf_percent(progress, self._wf_def)
        )
        self.agent_panel.workflow_bar.set_summary(
            self._wf_summary(progress), self._wf_body_percent()
        )

    def _on_wf_done(self):
        self._park_wf_thread()
        self.agent_panel.on_workflow_finished()
        self.agent_panel.workflow_bar.set_progress(100)
        self._refresh_chapters()
        self.editor.refresh_planning()
        self.statusBar().showMessage(t("workflow_finished"))

    def _on_wf_stopped(self):
        self._park_wf_thread()
        # 与完成路径同样解锁自由对话（停止也必须恢复聊天）
        self.agent_panel.on_workflow_finished()
        self.agent_panel.workflow_bar.set_running(False)
        self.agent_panel.workflow_bar.append_log("── <b>工作流已停止</b> ──")
        self._refresh_chapters()
        self.editor.refresh_planning()
        self.statusBar().showMessage(t("workflow_stopped"))

    def _park_wf_thread(self):
        """把即将结束的 WorkflowThread 挂到 _old_workers，避免 GC 销毁仍在 run() 里的 QThread。"""
        t = self._wf_thread
        self._wf_thread = None
        if t is not None:
            self._old_workers.append(t)
        self._cleanup_workers()

    def _open_workflow_panel(self):
        """打开工作流面板（保留兼容，实际通过 workflow_bar 操作）。"""
        if not self.project.title:
            QMessageBox.information(self, t("dialog_prompt"), t("workflow_no_project"))
            return
        self._workflow_start()

    def _load_default_workflow(self):
        """加载默认工作流到当前项目。"""
        if not self.project.title:
            QMessageBox.information(self, t("dialog_prompt"), t("workflow_no_project"))
            return
        project_io.save_workflow(self.project.project_dir, {
            "workflow": self._default_wf_data(),
            "progress": {},
        })
        self._load_workflow_for_project()
        self.statusBar().showMessage(t("workflow_generated"))

