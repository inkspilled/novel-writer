"""工作流执行线程 — 后台跑工作流并回传进度信号。"""
from __future__ import annotations

import asyncio

from PySide6.QtCore import Signal, QThread

from ..common.workers import _register_worker
from ...core.logger import get_logger

logger = get_logger(__name__)


class WorkflowThread(QThread):
    """后台线程执行工作流。"""
    step_started = Signal(str, int, str)        # step_id, n, agent_title
    step_finished = Signal(str, int, str, str)  # step_id, n, agent_title, response
    step_error = Signal(str, str)               # step_id, error_msg
    progress_updated = Signal(dict)             # progress dict
    log_message = Signal(str)                   # 日志消息
    workflow_done = Signal()                    # 全部完成
    workflow_stopped = Signal()                 # 被停止

    def __init__(self, runner, workflow, progress, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.workflow = workflow
        self.progress = progress
        self._loop = None
        self._task = None
        _register_worker(self)  # 防止 run() 未结束就被 GC

    def run(self):
        logger.info("WorkflowThread 开始执行")
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self.runner.on_step_start = self._on_step_start
        self.runner.on_step_end = self._on_step_end
        self.runner.on_error = self._on_error
        self.runner.on_event = self._on_event

        try:
            self.log_message.emit("工作流开始执行...")
            self._task = self._loop.create_task(
                self.runner.run(self.workflow, self.progress)
            )
            result = self._loop.run_until_complete(self._task)
            self.progress = result
            if self.runner.journal_path:
                self.log_message.emit(f"运行日志: {self.runner.journal_path}")
            if self.runner._stop:
                logger.info("工作流已被用户停止")
                self.workflow_stopped.emit()
            else:
                logger.info("工作流执行完成")
                self.workflow_done.emit()
        except asyncio.CancelledError:
            logger.info("工作流被用户取消（进行中的 LLM 调用已中断）")
            self.workflow_stopped.emit()
        except Exception as e:
            import traceback
            logger.error("工作流执行异常: %s", e, exc_info=True)
            self.runner._journal_record("run_error", error=str(e))
            self.log_message.emit(f"错误: {e}\n{traceback.format_exc()}")
            self.workflow_stopped.emit()
        finally:
            self._loop.close()

    def _on_event(self, kind: str, payload: dict):
        """工具调用/重试等结构化事件 → 工作流日志（DSH 风格的 trajectory）。"""
        try:
            if kind == "tool_call":
                args = payload.get("args") or {}
                text = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
                self.log_message.emit(f"  🔧 {payload.get('name')}({text})")
            elif kind == "tool_result":
                self.log_message.emit(f"  ↳ {payload.get('name')} 返回 {payload.get('chars', 0)} 字")
            elif kind == "step_retry":
                self.log_message.emit(
                    f"  ↻ {payload.get('step')} 第{payload.get('attempt')}次重试"
                    f"（{payload.get('delay', 0):.0f}s）"
                )
        except Exception:
            logger.debug("工作流事件格式化失败（忽略）", exc_info=True)

    def _on_step_start(self, step_id, n, agent_title):
        self.step_started.emit(step_id, n, agent_title)
        self.log_message.emit(f"▸ {agent_title} 开始 {step_id}" + (f" (第{n}章)" if n > 1 else ""))

    def _on_step_end(self, step_id, n, agent_title, output):
        self.step_finished.emit(step_id, n, agent_title, output)
        self.log_message.emit(f"✓ {agent_title} 完成 {step_id}" + (f" (第{n}章)" if n > 1 else ""))
        self.progress_updated.emit(self.progress)

    def _on_error(self, step_id, error):
        self.step_error.emit(step_id, error)
        self.log_message.emit(f"✗ {step_id}: {error}")

    def request_stop(self):
        """请求停止：置优雅停止标志，并取消进行中的 LLM 调用（秒级生效）。"""
        self.runner.stop()
        loop, task = self._loop, self._task
        if loop is not None and task is not None and not loop.is_closed():
            if not task.done():
                loop.call_soon_threadsafe(task.cancel)
