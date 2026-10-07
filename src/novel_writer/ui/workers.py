"""后台工作线程 — Agent 流式调用。"""
from __future__ import annotations

import asyncio

from PySide6.QtCore import QThread, Signal

from ..core.agents.base import BaseAgent
from ..core.logger import get_logger

logger = get_logger(__name__)


class AgentWorker(QThread):
    """后台线程执行 Agent 调用，支持流式输出。"""
    chunk_received = Signal(str)  # 流式文本块
    finished = Signal(str)  # 完成时的完整响应
    error = Signal(str)

    def __init__(self, agent: BaseAgent, user_input: str, context: str = ""):
        super().__init__()
        self.agent = agent
        self.user_input = user_input
        self.context = context
        self._cancelled = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

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
            full_response = ""
            async def collect_stream():
                nonlocal full_response
                async for chunk in self.agent.stream_run(self.user_input, self.context):
                    if self._cancelled:
                        break
                    full_response += chunk
                    # 信号发射异常记录日志，不静默吞掉
                    try:
                        self.chunk_received.emit(chunk)
                    except RuntimeError as e:
                        # Widget 可能已销毁，记录并退出
                        logger.debug("Signal emit failed (widget destroyed): %s", e)
                        break
                return full_response

            self._task = self._loop.create_task(collect_stream())
            self._loop.run_until_complete(self._task)
            if not self._cancelled:
                self.finished.emit(full_response)
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
    """后台测试模型连接的 Worker 线程。"""
    success = Signal(str)  # model_name
    error = Signal(str)    # error_message

    def __init__(self, provider: dict, api_key: str, base_url: str, model: str):
        super().__init__()
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    def run(self):
        try:
            base_url = self.base_url
            # Ollama 先检查模型是否存在
            if self.provider.get("type") == "ollama":
                import httpx
                try:
                    tags = httpx.get(base_url + "/api/tags", timeout=3)
                    tags.raise_for_status()
                    available = [m["name"] for m in tags.json().get("models", [])]
                    if self.model not in available:
                        self.error.emit(f"模型不存在。可用: {', '.join(available[:3])}")
                        return
                except Exception as e:
                    self.error.emit(f"无法连接 Ollama: {str(e)}")
                    return
                base_url = base_url.rstrip("/") + "/v1"

            from openai import OpenAI as _OpenAI
            client = _OpenAI(api_key=self.api_key or "test", base_url=base_url, timeout=10.0)
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=5,
                temperature=0.1
            )
            self.success.emit(resp.model)
        except Exception as e:
            error_msg = str(e)
            if "APIConnectionError" in error_msg:
                error_msg = "无法连接到服务器，请检查 URL"
            elif "AuthenticationError" in error_msg:
                error_msg = "API Key 无效"
            elif "timeout" in error_msg.lower():
                error_msg = "连接超时，请检查网络"
            self.error.emit(error_msg)
