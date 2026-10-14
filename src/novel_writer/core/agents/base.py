from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import AsyncIterator

from ..llm.base import BaseLLM, LLMMessage, LLMResponse, ThinkingMode
from ..logger import get_logger

logger = get_logger(__name__)

# 历史消息 token 预算：超限从最旧轮次开始丢弃（防御长对话场景，
# 与 workflow/context.estimate_tokens 同一启发式口径；本地实现避免循环导入）
HISTORY_TOKEN_BUDGET = 24_000


def _estimate_messages_tokens(messages: list[LLMMessage]) -> int:
    # 与 workflow/context.estimate_tokens 同口径：中文≈1.5/字，英文单词≈1.3
    import re
    total = 0
    for m in messages:
        content = m.content or ""
        cn = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
        en_words = len(re.findall(r'[a-zA-Z]+', content))
        total += int(cn * 1.5 + en_words * 1.3)
    return total


@dataclass
class AgentConfig:
    """Agent 配置。"""
    name: str
    role: str  # 角色标识
    title: str  # 中文标题
    system_prompt: str
    skills: list[str] = field(default_factory=list)  # 技能标签
    model: str = ""  # 空则用默认模型
    temperature: float = 0.7
    max_tokens: int = 4096
    thinking: ThinkingMode = "auto"  # 工具类 Agent 可默认 off


class BaseAgent(ABC):
    """Agent 基类。"""

    def __init__(self, config: AgentConfig, llm: BaseLLM):
        self.config = config
        self.llm = llm
        self.history: list[LLMMessage] = []

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def title(self) -> str:
        return self.config.title

    def _build_messages(self, user_input: str, context: str = "") -> list[LLMMessage]:
        # 合并 system_prompt 和 context 为单条 system 消息，兼容 Qwen 等要求单一 system 的模板
        system_parts = [self.config.system_prompt]
        if context:
            system_parts.append(context)
        system_content = "\n\n".join(system_parts)
        history = self._trim_history(self.history)
        messages = [LLMMessage(role="system", content=system_content)]
        messages.extend(history)
        messages.append(LLMMessage(role="user", content=user_input))
        return messages

    @staticmethod
    def _trim_history(history: list[LLMMessage]) -> list[LLMMessage]:
        """按 token 预算裁剪历史：保留最新轮次，丢最旧的（成对丢弃 user/assistant）。"""
        if _estimate_messages_tokens(history) <= HISTORY_TOKEN_BUDGET:
            return history
        trimmed: list[LLMMessage] = []
        total = 0
        # 从最新往旧收集，成对保留，保证交替结构
        pair: list[LLMMessage] = []
        for m in reversed(history):
            pair.append(m)
            if m.role == "user":
                pair_tokens = _estimate_messages_tokens(pair)
                if total + pair_tokens > HISTORY_TOKEN_BUDGET and trimmed:
                    break
                total += pair_tokens
                trimmed = pair[::-1] + trimmed
                pair = []
        return trimmed

    async def run(
        self,
        user_input: str,
        context: str = "",
        thinking: ThinkingMode | None = None,
        keep_history: bool = True,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """执行一次对话，返回完整响应。

        keep_history=False 时不写入对话历史——工作流工具类步骤
        （概要/世界状态/沉淀/校对）共享同一 Agent 时，历史跨步骤累积
        会在一章内重复计费 2～3 份上下文。
        max_tokens 可临时压低输出配额（如 200 字润色不必给 4096）。
        """
        import time as _time
        messages = self._build_messages(user_input, context)
        mode: ThinkingMode = thinking if thinking is not None else self.config.thinking
        limit = self.config.max_tokens if max_tokens is None else max_tokens
        t0 = _time.monotonic()
        logger.info(
            "Agent.run 开始: agent=%s thinking=%s max_tokens=%s keep_history=%s input_len=%d history_msgs=%d",
            self.name, mode, limit, keep_history, len(user_input), len(self.history),
        )
        response = await self.llm.chat(messages, self.config.temperature, limit, thinking=mode)
        elapsed = _time.monotonic() - t0
        logger.info(
            "Agent.run 完成: agent=%s 耗时=%.1fs 回复=%d字 usage=%s",
            self.name, elapsed, len(response.content or ""), response.usage,
        )
        if keep_history:
            self.history.append(LLMMessage(role="user", content=user_input))
            self.history.append(LLMMessage(role="assistant", content=response.content))
        return response

    async def stream_run(
        self,
        user_input: str,
        context: str = "",
        thinking: ThinkingMode | None = None,
        keep_history: bool = True,
    ) -> AsyncIterator[tuple[str, str]]:
        """流式执行，逐块返回 (kind, text)。kind: thinking | text。"""
        messages = self._build_messages(user_input, context)
        mode: ThinkingMode = thinking if thinking is not None else self.config.thinking
        answer_parts: list[str] = []
        async for kind, chunk in self.llm.stream_chat(
            messages, self.config.temperature, self.config.max_tokens, thinking=mode
        ):
            if kind == "text":
                answer_parts.append(chunk)
            yield kind, chunk
        if keep_history:
            # 历史只存正文，思考不回灌（省 token）
            self.history.append(LLMMessage(role="user", content=user_input))
            self.history.append(LLMMessage(role="assistant", content="".join(answer_parts)))

    def clear_history(self):
        self.history.clear()
