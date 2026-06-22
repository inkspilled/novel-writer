from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import AsyncIterator

from ..llm.base import BaseLLM, LLMMessage, LLMResponse


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
        messages = [LLMMessage(role="system", content=self.config.system_prompt)]
        if context:
            # 拆分上下文为稳定前缀（可缓存）和变量后缀
            stable, variable = self._split_context_for_cache(context)
            if stable:
                messages.append(LLMMessage(role="system", content=stable))
            if variable:
                messages.append(LLMMessage(role="system", content=variable))
        messages.extend(self.history)
        messages.append(LLMMessage(role="user", content=user_input))
        return messages

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
