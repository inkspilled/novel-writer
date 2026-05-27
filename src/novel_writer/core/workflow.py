from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from .agents.base import AgentConfig, BaseAgent
from .llm.base import BaseLLM, LLMMessage


@dataclass
class WorkflowStep:
    """工作流步骤。"""
    agent: BaseAgent
    prompt_template: str = "{input}"  # {input} 和 {context} 会被替换
    pass_output_as_context: bool = True  # 是否将输出传递给下一步作为上下文


class WorkflowEngine:
    """工作流引擎 - 串联多个 Agent 执行。"""

    def __init__(self):
        self.steps: list[WorkflowStep] = []
        self.on_step_start: Callable[[int, str], None] | None = None
        self.on_step_end: Callable[[int, str, str], None] | None = None
        self._stop = False

    def add_step(self, agent: BaseAgent, prompt_template: str = "{input}", pass_output: bool = True) -> WorkflowEngine:
        self.steps.append(WorkflowStep(
            agent=agent,
            prompt_template=prompt_template,
            pass_output_as_context=pass_output,
        ))
        return self

    def stop(self):
        self._stop = True

    async def run(self, initial_input: str) -> str:
        """执行完整工作流，返回最终输出。"""
        self._stop = False
        current_input = initial_input
        context = ""

        for i, step in enumerate(self.steps):
            if self._stop:
                break

            if self.on_step_start:
                self.on_step_start(i, step.agent.title)

            prompt = step.prompt_template.format(input=current_input, context=context)
            response = await step.agent.run(prompt, context=context if step.pass_output_as_context else "")

            if step.pass_output_as_context:
                context = response.content
            current_input = response.content

            if self.on_step_end:
                self.on_step_end(i, step.agent.title, response.content)

        return current_input

    async def run_step(self, step_index: int, user_input: str, context: str = "") -> AsyncIterator[str]:
        """执行单个步骤，流式返回。"""
        step = self.steps[step_index]
        prompt = step.prompt_template.format(input=user_input, context=context)
        async for chunk in step.agent.stream_run(prompt, context=context):
            yield chunk
