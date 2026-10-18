"""Agent 工具循环 — 借鉴 deepseek-harness 的「模型请求 → 工具执行 → 再请求」。

与 DSH 的完整驱动器相比，这里刻意只做最小闭环：
- 只读工具、最多 N 轮、每次最多 M 个调用；
- 工具结果回填为 tool 消息，最终答案写一次历史；
- 任何工具失败都转成文本回给模型，不中断步骤；
- 不支持工具调用的模型由 LLMClient 自动降级为纯文本。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..llm.base import LLMMessage, LLMResponse
from ..logger import get_logger

if TYPE_CHECKING:
    from .base import BaseAgent
    from .tools import ToolRegistry

logger = get_logger(__name__)

EventCallback = Callable[[str, dict[str, Any]], None]

_TOOL_MARKUP_RE = re.compile(r"<tool_call|<function=|<parameter=|</tool_call>", re.IGNORECASE)

_DEFAULT_GUARD = """【工具使用】
你可以调用只读工具补充事实。规则：
1. 只在确实缺少具体事实时调用（人物细节、前文原句、伏笔、物品状态）；
2. 优先用已有上下文写作，不要为了「更完整」反复检索；
3. 不要重复调用同一工具同一参数；拿不到就基于现有信息继续；
4. 工具调用预算有限，用完必须直接给出最终答案，不再请求工具。
可用工具：
{catalog}
{skill_hint}"""


@dataclass
class ToolLoopStats:
    rounds: int = 0
    calls: int = 0
    tools_used: list[str] = field(default_factory=list)


class AgentLoop:
    """在 BaseAgent 之上叠加工具调用；不改变 Agent 的历史语义。"""

    def __init__(
        self,
        agent: "BaseAgent",
        toolkit: "ToolRegistry",
        max_rounds: int = 3,
        max_calls_per_round: int = 4,
        on_event: EventCallback | None = None,
    ):
        self.agent = agent
        self.toolkit = toolkit
        self.max_rounds = max(0, max_rounds)
        self.max_calls_per_round = max(1, max_calls_per_round)
        self.on_event = on_event
        self.stats = ToolLoopStats()

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.on_event:
            try:
                self.on_event(kind, payload)
            except Exception:
                logger.debug("工具事件回调失败（忽略）", exc_info=True)

    def _guard_text(self) -> str:
        catalog = "\n".join(
            f"- {s['function']['name']}: {s['function']['description']}"
            for s in self.toolkit.schemas()
        )
        skill_hint = ""
        if "use_skill" in self.toolkit.names():
            skill_hint = "写作前可先 list_skills / use_skill 取对应工艺指南（开篇、对话、战斗、情感、悬念、连续性）。"
        return _DEFAULT_GUARD.format(catalog=catalog, skill_hint=skill_hint)

    async def run(
        self,
        user_input: str,
        context: str = "",
        thinking=None,
        keep_history: bool = True,
    ) -> LLMResponse:
        agent = self.agent
        guard = self._guard_text()
        merged_context = f"{context}\n\n{guard}" if context else guard
        messages = agent._build_messages(user_input, merged_context)
        mode = thinking if thinking is not None else agent.config.thinking
        usage: dict[str, int] = {}
        final = LLMResponse(content="")

        for round_idx in range(self.max_rounds + 1):
            self.stats.rounds = round_idx + 1
            # 最后一轮或预算耗尽：不再给工具，强制收束
            allow_tools = round_idx < self.max_rounds
            response = await agent.llm.chat(
                messages,
                agent.config.temperature,
                agent.config.max_tokens,
                thinking=mode,
                tools=self.toolkit.schemas() if allow_tools else None,
            )
            for k, v in (response.usage or {}).items():
                if isinstance(v, (int, float)):
                    usage[k] = int(usage.get(k, 0) + v)
            if hasattr(agent, "_record_usage"):
                agent._record_usage(response.usage)

            calls = (response.tool_calls or [])[: self.max_calls_per_round]
            if not calls:
                # 有些模型不会走 OpenAI tool_calls，而是把 <tool_call> XML 写进正文。
                # 绝不能把这种文本当章节落盘：禁用工具再请求一次，强制写正文。
                if _TOOL_MARKUP_RE.search(response.content or ""):
                    logger.warning("模型把工具调用写进正文，禁用工具重试: %s", agent.name)
                    response = await agent.llm.chat(
                        messages, agent.config.temperature, agent.config.max_tokens,
                        thinking=mode, tools=None,
                    )
                    for k, v in (response.usage or {}).items():
                        if isinstance(v, (int, float)):
                            usage[k] = int(usage.get(k, 0)) + int(v)
                    if hasattr(agent, "_record_usage"):
                        agent._record_usage(response.usage)
                    if _TOOL_MARKUP_RE.search(response.content or ""):
                        logger.error("禁用工具后仍返回工具标记，丢弃本次输出")
                        final = LLMResponse(content="")
                    else:
                        final = response
                else:
                    final = response
                break
            if not allow_tools:
                final = response
                break

            self._emit("tool_round", {"round": round_idx + 1, "calls": len(calls)})
            messages.append(LLMMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=calls,
            ))
            for tc in calls[: self.max_calls_per_round]:
                name = (tc.get("function") or {}).get("name", "")
                raw_args = (tc.get("function") or {}).get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else {}
                    if not isinstance(args, dict):
                        args = {"input": args}
                except json.JSONDecodeError:
                    args = {"input": raw_args}
                self.stats.calls += 1
                self.stats.tools_used.append(name)
                self._emit("tool_call", {"name": name, "args": args})
                result = await self.toolkit.execute(name, args)
                self._emit("tool_result", {"name": name, "chars": len(result)})
                messages.append(LLMMessage(
                    role="tool",
                    content=result,
                    tool_call_id=tc.get("id", ""),
                ))
        else:  # pragma: no cover - 循环内已 break
            final = LLMResponse(content="")

        final.usage = usage
        if keep_history and final.content:
            agent.history.append(LLMMessage(role="user", content=user_input))
            agent.history.append(LLMMessage(role="assistant", content=final.content))
        if self.stats.calls:
            logger.info(
                "AgentLoop 完成: agent=%s rounds=%d tools=%d (%s)",
                agent.name, self.stats.rounds, self.stats.calls,
                ", ".join(self.stats.tools_used[:8]),
            )
        return final
