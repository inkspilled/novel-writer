"""后台工作线程 — Agent 流式调用。

线程生存期（学 smt-intelligent）：自定义完成信号会在 run() 内发出，
结果槽里清掉最后一个 Python 引用时 QThread 可能还没走完 C++ 收尾，
GC 触发 `QThread: Destroyed while thread is still running` 直接 abort。
因此全部 worker 挂进模块级强引用注册表，只在内建 finished 之后释放。
"""
from __future__ import annotations

import asyncio

from PySide6.QtCore import QThread, Signal

from ...core.agents.base import BaseAgent
from ...core.logger import get_logger

logger = get_logger(__name__)

# 模块级强引用：防止运行中的 QThread 被 GC 析构
_ALIVE_WORKERS: list[QThread] = []


def _register_worker(worker: QThread) -> None:
    """把 worker 钉在模块级列表里，finished 后再摘除 + deleteLater。"""
    _ALIVE_WORKERS.append(worker)

    def _release():
        try:
            _ALIVE_WORKERS.remove(worker)
        except ValueError:
            pass
        worker.deleteLater()

    # 用内建 finished（run() 真正返回后才发），不用自定义 completed
    worker.finished.connect(_release)


class AgentWorker(QThread):
    """后台线程执行 Agent 调用，支持流式输出。"""
    chunk_received = Signal(str, str)  # (kind, text)：thinking | text
    # 不用 QThread 内建的 finished（无参），自定义带载荷的完成信号
    completed = Signal(str)  # 完成时的正文（不含思考）
    error = Signal(str)

    def __init__(self, agent: BaseAgent, user_input: str, context: str = ""):
        super().__init__()
        self.agent = agent
        self.user_input = user_input
        self.context = context
        self._cancelled = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        _register_worker(self)

    def cancel(self):
        """请求取消进行中的任务。"""
        self._cancelled = True
        if self._loop and self._task and not self._task.done():
            self._loop.call_soon_threadsafe(self._task.cancel)

    def run(self):
        logger.debug("AgentWorker.run: agent=%r, input=%r", self.agent.name, self.user_input[:80])
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            answer_parts: list[str] = []

            async def collect_stream():
                async for kind, chunk in self.agent.stream_run(self.user_input, self.context):
                    if self._cancelled:
                        break
                    if kind == "text":
                        answer_parts.append(chunk)
                    try:
                        self.chunk_received.emit(kind, chunk)
                    except RuntimeError as e:
                        logger.debug("Signal emit failed (widget destroyed): %s", e)
                        break
                return "".join(answer_parts)

            self._task = self._loop.create_task(collect_stream())
            import time as _t
            _t0 = _t.monotonic()
            full_response = self._loop.run_until_complete(self._task)
            logger.info("AgentWorker 完成: 耗时=%.1fs 答案=%d字 cancelled=%s",
                        _t.monotonic() - _t0, len(full_response or ""), self._cancelled)
            if not self._cancelled:
                self.completed.emit(full_response)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if not self._cancelled:
                logger.error("AgentWorker error: %s", e)
                try:
                    self.error.emit(str(e)[:500])
                except RuntimeError:
                    pass
        finally:
            # 不关闭 LLM 客户端 — 它是共享的，由 MainWindow 管理生命周期
            self._loop.close()
            self._loop = None
            self._task = None


class TestConnectionWorker(QThread):
    """后台测试模型连接的 Worker 线程（统一 OpenAI 兼容协议）。"""
    success = Signal(str)  # model_name
    error = Signal(str)    # error_message

    def __init__(self, api_key: str, base_url: str, model: str):
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        _register_worker(self)

    def run(self):
        try:
            base_url = self.base_url.rstrip("/")
            from openai import OpenAI as _OpenAI
            from openai import APIConnectionError, AuthenticationError, APITimeoutError
            client = _OpenAI(api_key=self.api_key or "test", base_url=base_url, timeout=15.0)
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=5,
                temperature=0.1
            )
            self.success.emit(resp.model)
        except Exception as e:
            error_msg = str(e)
            if isinstance(e, APIConnectionError):
                error_msg = "无法连接到服务器，请检查 URL"
            elif isinstance(e, AuthenticationError):
                error_msg = "API Key 无效"
            elif isinstance(e, APITimeoutError) or "timeout" in error_msg.lower():
                error_msg = "连接超时，请检查网络"
            self.error.emit(error_msg)


class FetchModelsWorker(QThread):
    """后台拉取可用模型列表（GET /models）。"""
    success = Signal(list)  # [{"id": "model-name", ...}, ...]
    error = Signal(str)

    def __init__(self, api_key: str, base_url: str):
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
        _register_worker(self)

    def run(self):
        try:
            base_url = self.base_url.rstrip("/")
            from openai import OpenAI as _OpenAI
            from openai import APIConnectionError, AuthenticationError, APITimeoutError
            client = _OpenAI(api_key=self.api_key or "fetch", base_url=base_url, timeout=10.0)
            resp = client.models.list()
            models = [{"id": m.id} for m in resp.data]
            models.sort(key=lambda x: x["id"])
            self.success.emit(models)
        except Exception as e:
            error_msg = str(e)
            if isinstance(e, APIConnectionError):
                error_msg = "无法连接到服务器，请检查 URL"
            elif isinstance(e, AuthenticationError):
                error_msg = "API Key 无效（本地服务通常无需 Key）"
            elif isinstance(e, APITimeoutError) or "timeout" in error_msg.lower():
                error_msg = "连接超时，请检查服务是否启动"
            self.error.emit(error_msg)
