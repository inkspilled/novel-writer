"""工作流执行线程 — 后台跑工作流并回传进度信号。"""
from __future__ import annotations

import asyncio
import weakref
import gc
import time

from PySide6.QtCore import Signal, QThread

from ..common.workers import _register_worker
from ...core.logger import get_logger

logger = get_logger(__name__)


class WorkflowThread(QThread):
    """后台线程执行工作流。"""
    step_started = Signal(str, int, str)        # step_id, n, agent_title
    step_finished = Signal(str, int, str, str)  # step_id, n, agent_title, response
    step_error = Signal(str, str)               # step_id, error_msg
    chapter_chunk = Signal(str, int, str, str)  # step_id, n, kind, text
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
        self._step_t0 = 0.0
        self._step_usage: dict = {}
        _register_worker(self)  # 防止 run() 未结束就被 GC

    def _weak_callback(self, name: str):
        """弱引用绑定，避免 runner ↔ QThread 的强引用环让 GC 在子线程里 traverse QThread。"""
        ref = weakref.WeakMethod(getattr(self, name))

        def callback(*args, **kwargs):
            fn = ref()
            if fn is not None:
                return fn(*args, **kwargs)
            return None

        return callback

    def run(self):
        # PySide6 的 QThread 包装器在子线程里被 Python GC traverse 会偶发 SIGSEGV。
        # 工作流执行期间关闭自动 GC，结束回到主线程后再恢复（对象生命周期由 Qt/引用计数管理）。
        gc_was_enabled = gc.isenabled()
        gc.disable()
        try:
            self._run()
        finally:
            if gc_was_enabled:
                gc.enable()

    def _run(self):
        logger.info("WorkflowThread 开始执行")
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self.runner.on_step_start = self._weak_callback("_on_step_start")
        self.runner.on_step_end = self._weak_callback("_on_step_end")
        self.runner.on_error = self._weak_callback("_on_error")
        self.runner.on_event = self._weak_callback("_on_event")
        self.runner.on_chunk = self._weak_callback("_on_chunk")

        try:
            # 开始横幅由 AgentPanel.on_workflow_started 统一输出，避免重复
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
            # 断开 runner → callback → thread 的引用，避免线程结束后 GC 再 traverse 已销毁的 QThread
            for attr in ("on_step_start", "on_step_end", "on_error", "on_event", "on_chunk"):
                try:
                    setattr(self.runner, attr, None)
                except Exception:
                    pass
            try:
                self._loop.close()
            finally:
                self._loop = None
                self._task = None

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
        self._step_t0 = time.monotonic()
        try:
            self._step_usage = dict(self.runner.total_usage)
        except Exception:
            self._step_usage = {}
        self.step_started.emit(step_id, n, agent_title)
        suffix = f" (第{n}章)" if n > 1 else ""
        self.log_message.emit(f"▸ <b>{agent_title}</b> 开始 {step_id}{suffix}")

    def _on_step_end(self, step_id, n, agent_title, output):
        elapsed = max(0.0, time.monotonic() - self._step_t0) if self._step_t0 else 0.0
        total = dict(getattr(self.runner, "total_usage", {}) or {})
        delta = {k: int(total.get(k, 0)) - int(self._step_usage.get(k, 0)) for k in total}
        tok = delta.get("total_tokens") or (delta.get("prompt_tokens", 0) + delta.get("completion_tokens", 0))
        preview = " ".join((output or "").strip().split())[:80]
        self.step_finished.emit(step_id, n, agent_title, output)
        suffix = f" (第{n}章)" if n > 1 else ""
        detail = f" | {elapsed:.1f}s | +{tok} tok | {len(output or '')}字"
        if preview:
            detail += f" | {preview}"
        self.log_message.emit(f"✓ <b>{agent_title}</b> 完成 {step_id}{suffix}{detail}")
        try:
            self.progress["_usage"] = dict(total)
        except Exception:
            pass
        self.progress_updated.emit(self.progress)

    def _on_chunk(self, step_id, n, kind, text):
        self.chapter_chunk.emit(step_id, n, kind, text)

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
