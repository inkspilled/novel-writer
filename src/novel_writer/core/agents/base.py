from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import AsyncIterator

from ..llm.base import BaseLLM, LLMMessage, LLMResponse

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

    async def run(self, user_input: str, context: str = "") -> LLMResponse:
        """执行一次对话，返回完整响应。"""
        messages = self._build_messages(user_input, context)
        response = await self.llm.chat(messages, self.config.temperature, self.config.max_tokens)
        self.history.append(LLMMessage(role="user", content=user_input))
        self.history.append(LLMMessage(role="assistant", content=response.content))
        return response

    async def stream_run(self, user_input: str, context: str = "") -> AsyncIterator[str]:
        """流式执行，逐块返回。"""
        messages = self._build_messages(user_input, context)
        full_response = ""
        async for chunk in self.llm.stream_chat(messages, self.config.temperature, self.config.max_tokens):
            full_response += chunk
            yield chunk
        self.history.append(LLMMessage(role="user", content=user_input))
        self.history.append(LLMMessage(role="assistant", content=full_response))

    def clear_history(self):
        self.history.clear()
