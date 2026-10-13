from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import AsyncIterator

from ..llm.base import BaseLLM, LLMMessage, LLMResponse

# 历史消息 token 预算：超限从最旧轮次开始丢弃（防御长对话场景，
# 与 workflow/context.estimate_tokens 同一启发式口径；本地实现避免循环导入）
HISTORY_TOKEN_BUDGET = 24_000


def _estimate_messages_tokens(messages: list[LLMMessage]) -> int:
    total = 0
    for m in messages:
        content = m.content or ""
        cn = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
        en = len(content) - cn
        total += int(cn * 1.5 + en * 0.3)
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

    @staticmethod
    def _split_context_for_cache(context: str) -> tuple[str, str]:
        """将上下文拆分为稳定前缀和变量后缀，优化 Prompt Caching。

        稳定前缀：项目元信息 + 世界设定 + 角色状态（跨章节不变）
        变量后缀：叙事记忆 + 工作记忆（每章变化）
        """
        # 标记变量部分的起始关键词
        variable_markers = [
            "=== 剧情摘要 ===",
            "【未解伏笔】",
            "【写作禁忌",
            "【追读力指导",
            "=== 角色推演 ===",
            "=== 章节概要 ===",
            "=== 第",
            "=== RAG",
        ]
        split_pos = len(context)
        for marker in variable_markers:
            pos = context.find(marker)
            if pos >= 0 and pos < split_pos:
                split_pos = pos

        if split_pos == 0:
            return "", context
        if split_pos >= len(context):
            return context, ""
        return context[:split_pos].rstrip(), context[split_pos:]

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
