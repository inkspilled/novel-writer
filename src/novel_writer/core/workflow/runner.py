"""工作流执行器 — 技能匹配 + 文件传递。"""
from __future__ import annotations

import asyncio
import itertools
import json
import re
import shutil
from pathlib import Path
from typing import Callable, Iterator

from .. import project_io
from ..agents.base import BaseAgent
from ..llm.base import LLMResponse
from ..logger import get_logger
from ..text_clean import clean_model_text
from ..memory import MemoryScratchpad
from .builtin_steps import BUILTIN_STEP_HANDLERS, _fallback_agent
from .constants import (
    CHAPTER_CONTENT_STEP_IDS,
    NO_HISTORY_STEP_IDS,
    NO_THINKING_STEP_IDS,
    PLANNING_UPDATE_STEP_PREFIX,
    POST_CHAPTER_PERIODIC_IDS,
    PRE_CHAPTER_PERIODIC_IDS,
    SKILL_PROOFREAD,
    SKILL_REVIEW,
)
from .context import ContextBuilder
from .definition import WorkflowDef, WorkflowStep
from .prompts import build_memory_sediment_prompt, build_pacing_context, build_title_constraint
from .sediment import extract_chapter_title, sediment_chapter
from ..json_extract import extract_json_object

logger = get_logger(__name__)


class WorkflowError(Exception):
    pass


class ChapterWriteRejected(Exception):
    """章正文写入被拒（校验失败/空内容），不得推进断点。"""


class WorkflowRunner:
    """工作流执行器 — 技能匹配 + 文件传递。"""

    def __init__(
        self,
        agents: dict[str, BaseAgent],
        project_dir: Path,
        project_info: dict,
        max_context_tokens: int = 48_000,
        rotate_agents: bool = False,
        enable_tools: bool = True,
    ):
        self.agents = agents
        self.project_dir = project_dir
        self.project_info = project_info
        self.max_context_tokens = max_context_tokens
        # 只读工具（规划/前文/记忆/技能）开关；模型不支持时自动降级为纯文本
        self.enable_tools = enable_tools
        self._skill_registry = None
        # 同技能多 Agent 轮换开关：默认关闭。规划链（大纲→人物→世界观）
        # 依赖同一 Agent 实例的对话历史传递，轮换会打断该机制，故需显式开启
        self.rotate_agents = rotate_agents
        self._agent_by_skill: dict[str, list[BaseAgent]] = {}
        self._skill_cycles: dict[str, Iterator[BaseAgent]] = {}
        self._build_skill_index()
        self._stop = False
        self.total_usage: dict[str, int] = {}  # 本次运行累计 token 用量
        self._journal = None  # RunJournal（run_start 时创建）
        self._context_builder = ContextBuilder(project_dir, project_info, max_context_tokens)
        # 周期步骤触发时机（章前/章后）；未声明的自定义周期步骤默认按章后执行
        self._pre_ids = set(PRE_CHAPTER_PERIODIC_IDS)
        self._post_ids = set(POST_CHAPTER_PERIODIC_IDS)
        # 回调
        self.on_step_start: Callable[[str, int, str], None] | None = None  # (step_id, n, agent_title)
        self.on_step_end: Callable[[str, int, str, str], None] | None = None  # (step_id, n, agent_title, output)
        self.on_error: Callable[[str, str], None] | None = None  # (step_id, error)
        # 结构化事件（工具调用/重试/阶段），UI 可转日志或进度
        self.on_event: Callable[[str, dict], None] | None = None
        # 章节流式输出 (step_id, n, kind, text)
        self.on_chunk: Callable[[str, int, str, str], None] | None = None

    def _build_skill_index(self):
        for agent in self.agents.values():
            for skill in agent.config.skills:
                self._agent_by_skill.setdefault(skill, []).append(agent)
            # 所有 Agent 的用量都汇总到 runner（含内置步骤与工具循环）
            agent.usage_sink = self._record_usage
        self._skill_cycles = {skill: itertools.cycle(items) for skill, items in self._agent_by_skill.items()}

    def _record_usage(self, usage: dict | None) -> None:
        for k, v in (usage or {}).items():
            if isinstance(v, (int, float)):
                self.total_usage[k] = int(self.total_usage.get(k, 0) + v)

    def _emit_event(self, kind: str, **payload) -> None:
        self._journal_record(kind, **payload)
        if self.on_event:
            try:
                self.on_event(kind, payload)
            except Exception:
                logger.debug("on_event 回调失败（忽略）", exc_info=True)

    def _usage_delta(self, before: dict) -> dict:
        return {
            k: int(v - before.get(k, 0))
            for k, v in self.total_usage.items()
            if int(v - before.get(k, 0)) != 0
        }

    def _journal_record(self, event: str, **fields) -> None:
        if self._journal is not None:
            self._journal.record(event, **fields)

    @property
    def journal_path(self) -> str:
        return str(self._journal.path) if self._journal is not None else ""

    def _skill_text(self, names: list[str]) -> str:
        if not names:
            return ""
        if self._skill_registry is None:
            from ..skills import SkillRegistry
            self._skill_registry = SkillRegistry(self.project_dir)
        skills = self._skill_registry.select(names)
        if not skills:
            return ""
        parts = ["=== 写作技能包 ==="]
        for skill in skills:
            parts.append(f"【{skill.name}】\n{skill.body}")
        return "\n\n".join(parts)

    async def _execute_step(self, step: WorkflowStep, project: dict, n: int, progress: dict):
        """单步执行 + 超时 + 指数退避重试。配置错误/写盘被拒不重试。"""
        attempts = max(1, step.retry + 1)
        import time as _time
        for attempt in range(attempts):
            t0 = _time.monotonic()
            usage_before = dict(self.total_usage)
            self._journal_record("step_start", step=step.id, n=n, attempt=attempt + 1,
                                 phase=step.phase or None, tools=step.tools or None)
            try:
                coro = self._run_single(step, project, n, progress)
                if step.timeout and step.timeout > 0:
                    result = await asyncio.wait_for(coro, timeout=step.timeout)
                else:
                    result = await coro
                self._journal_record("step_end", step=step.id, n=n,
                                     duration_s=round(_time.monotonic() - t0, 2),
                                     usage=self._usage_delta(usage_before))
                return result
            except (WorkflowError, ChapterWriteRejected, asyncio.CancelledError) as e:
                self._journal_record("step_error", step=step.id, n=n, error=str(e),
                                     duration_s=round(_time.monotonic() - t0, 2),
                                     usage=self._usage_delta(usage_before))
                raise
            except Exception as e:
                if attempt >= attempts - 1:
                    self._journal_record("step_error", step=step.id, n=n, error=str(e),
                                         duration_s=round(_time.monotonic() - t0, 2),
                                         usage=self._usage_delta(usage_before))
                    raise
                delay = max(0.0, step.retry_delay) * (2 ** attempt)
                logger.warning("步骤 %s 第%d次失败，%.1fs 后重试: %s", step.id, attempt + 1, delay, e)
                self._emit_event("step_retry", step=step.id, n=n, attempt=attempt + 1, delay=delay, error=str(e))
                await asyncio.sleep(delay)

    def find_agent(self, skill: str) -> BaseAgent | None:
        candidates = self._agent_by_skill.get(skill, [])
        if not candidates:
            return None
        if self.rotate_agents and len(candidates) > 1:
            return next(self._skill_cycles[skill])
        return candidates[0]

    def stop(self):
        logger.info("工作流收到停止信号")
        self._stop = True

    def _find_chapter_file(self, n: int) -> str | None:
        """查找第 n 章的已有文件名，找到则返回文件名（不含目录）。"""
        return project_io.find_chapter_file(self.project_dir, n)

    async def run(self, workflow: WorkflowDef, progress: dict | None = None) -> dict:
        """执行完整工作流，返回进度状态。"""
        logger.info("工作流开始执行: %s (%d 步骤)", workflow.name, len(workflow.steps))
        self._stop = False
        if progress is None:
            progress = {}
        from .journal import RunJournal
        self._journal = RunJournal(self.project_dir, workflow.name)
        self._journal_record("run_start", workflow=workflow.name,
                             steps=len(workflow.steps), resumed=bool(progress))

        # 预处理：分离定时步骤和普通步骤
        periodic_steps = [s for s in workflow.steps if s.every > 0]
        normal_steps = [s for s in workflow.steps if s.every == 0]

        # 未声明触发时机的自定义周期步骤默认按章后执行（原先会被静默丢弃）
        for ps in periodic_steps:
            if ps.id not in self._pre_ids and ps.id not in self._post_ids:
                logger.warning("周期步骤 %s 未声明章前/章后触发时机，默认按章后执行", ps.id)
                self._post_ids.add(ps.id)

        step_groups = self._build_step_groups(normal_steps)

        for group in step_groups:
            if self._stop:
                break
            if len(group) == 1:
                # 断点恢复：已完成（或循环已跑满）的步骤直接跳过，不重复烧 LLM 调用
                step = group[0]
                if self._step_completed(step, progress, self.project_dir):
                    logger.info("[跳过] 步骤 %s 已完成（断点恢复）", step.id)
                    continue
                await self._run_step_or_repeat(group[0], workflow.project, progress, periodic_steps)
            else:
                # 并发执行独立步骤（如规划阶段的人物/世界观/时间线/主线/支线/伏笔）
                logger.info("并发执行 %d 个独立步骤: %s", len(group), [s.id for s in group])
                # 固定「步骤 ↔ 任务」映射：先算出待跑列表再 gather/zip，
                # 避免任务完成后 progress 变 done 导致错位
                pending_steps = [
                    s for s in group
                    if not self._step_completed(s, progress, self.project_dir)
                ]
                tasks = [
                    self._run_step_or_repeat(s, workflow.project, progress, periodic_steps)
                    for s in pending_steps
                ]
                if tasks:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for s, r in zip(pending_steps, results):
                        if isinstance(r, Exception):
                            logger.error("并发步骤失败: %s - %s", s.id, r)
                            if self.on_error:
                                self.on_error(s.id, str(r))
                            raise r
                        progress[s.id] = "done"
                        self._save_progress(progress)

        self._journal_record("run_end", workflow=workflow.name, usage=self.total_usage,
                             errors=progress.get("_errors", []), stopped=self._stop)
        logger.info(
            "工作流执行完成: %s (累计 token 用量: %s, 日志: %s)",
            workflow.name,
            self.total_usage or "未上报",
            self.journal_path,
        )
        return progress

    def _build_step_groups(self, steps: list[WorkflowStep]) -> list[list[WorkflowStep]]:
        """普通步骤分组。

        未声明 depends_on 时保持原语义：连续 parallel_ok 步骤并发；
        任一步声明 depends_on 时切换为拓扑分层，同层并发、层间串行。
        """
        if not any(s.depends_on for s in steps):
            groups: list[list[WorkflowStep]] = []
            for step in steps:
                if step.parallel_ok and groups and groups[-1] and groups[-1][0].parallel_ok:
                    groups[-1].append(step)
                else:
                    groups.append([step])
            return groups

        ids = {s.id for s in steps}
        for step in steps:
            for dep in step.depends_on:
                if dep not in ids:
                    raise WorkflowError(f"步骤 {step.id} 依赖未知步骤 {dep}")
        remaining = list(steps)
        done: set[str] = set()
        groups = []
        while remaining:
            ready = [s for s in remaining if all(d in done for d in s.depends_on)]
            if not ready:
                raise WorkflowError("工作流存在循环依赖: " + ", ".join(s.id for s in remaining))
            groups.append(ready)
            for s in ready:
                done.add(s.id)
            remaining = [s for s in remaining if s.id not in done]
        return groups

    @staticmethod
    def _step_completed(step: WorkflowStep, progress: dict, project_dir=None) -> bool:
        """步骤是否已完成（普通步骤看 done；循环步骤看 current 是否跑满）。

        标成 done 但产出文件已被删除/清空时视为未完成，允许自动重跑
        （例如用户删了 planning/章节细纲.md 想重新生成）。
        """
        prog = progress.get(step.id, "pending")
        if prog == "done":
            if project_dir and step.output and "{" not in step.output:
                out_path = Path(project_dir) / step.output
                if not out_path.exists() or out_path.stat().st_size == 0:
                    logger.info("步骤 %s 产出 %s 缺失，将重跑", step.id, step.output)
                    return False
            return True
        if isinstance(prog, dict):
            return prog.get("current", 0) >= prog.get("total", step.repeat or 1)
        return False

    async def _run_step_or_repeat(
        self,
        step: WorkflowStep,
        project: dict,
        progress: dict,
        periodic_steps: list[WorkflowStep],
    ):
        """执行单个步骤（含 repeat 循环逻辑）。"""
        if step.repeat > 0:
            default_start = project.get("_start_chapter", 1)
            # current 记录的是「已完成的最后一章」，恢复时从下一章继续
            step_progress = progress.get(step.id, "pending")
            if isinstance(step_progress, dict):
                last_done = int(step_progress.get("current", default_start - 1) or 0)
                start = max(last_done + 1, default_start)
            else:
                start = default_start
            gaps = project.get("_gaps", [])
            chapters_to_write = gaps if gaps else list(range(start, step.repeat + 1))
            # 首个写盘被拒的章：后续章可以继续写，但 current 不得越过这个洞，
            # 否则断点恢复会直接从 n+1 开始，把失败章永久跳过。
            blocked_at: int | None = None
            for n in chapters_to_write:
                if self._stop:
                    break
                # 每轮迭代清空全部 Agent 历史：跨章连贯性由 ContextBuilder
                # （前文/概要/记忆卡）负责，历史跨章累积会在几十章后撑爆上下文
                # （规划链在进入循环前已完成，其历史不该带进循环）。
                # UI 聊天记录走独立的 chat_history 存储，不受影响。
                for agent in self.agents.values():
                    agent.clear_history()
                for ps in periodic_steps:
                    if self._stop:
                        break
                    if ps.id in self._pre_ids and n % ps.every == 0:
                        if progress.get(f"{ps.id}_{n}") != "done":
                            await self._run_periodic(ps, project, n, progress)
                try:
                    await self._execute_step(step, project, n, progress)
                except ChapterWriteRejected as e:
                    # 内容校验失败/写盘被拒：章文件仍是旧版本，不能标成已完成，
                    # 否则断点恢复会跳过这一章。_write_output 已回调过 on_error。
                    logger.error("第%d章写入被拒（不推进进度）: %s", n, e)
                    progress.setdefault("_errors", []).append(f"第{n}章: {e}")
                    if blocked_at is None:
                        blocked_at = n
                    continue
                except WorkflowError:
                    raise  # 配置类错误（如找不到智能体），继续重试无意义
                except Exception as e:
                    # 单章失败不终止整跑：记录后跳到下一章，结尾汇总
                    logger.error("章节步骤失败（跳过继续）: 第%d章 - %s", n, e, exc_info=True)
                    progress.setdefault("_errors", []).append(f"第{n}章: {e}")
                    if self.on_error:
                        self.on_error(step.id, f"第{n}章写作失败: {e}")
                    continue
                for ps in periodic_steps:
                    if self._stop:
                        break
                    if ps.id in self._post_ids and n % ps.every == 0:
                        if progress.get(f"{ps.id}_{n}") != "done":
                            await self._run_periodic(ps, project, n, progress)
                # 写后沉淀：等全部章后步骤（含润色改写正文）跑完后，
                # 从最终落盘正文提取记忆——草稿不是正典，润色前的文本不入账
                sediment_ok = await self._run_sediment(n, progress)
                if not sediment_ok:
                    # 沉淀失败：留洞，恢复时重试本章；本章不推进 current
                    if blocked_at is None:
                        blocked_at = n
                    continue
                if self._stop:
                    # 章后步骤被停止打断：不推进 current，恢复时补跑本章章后链路
                    break
                # current 必须等章后链路全部跑完才落盘：否则在章后步骤/沉淀中途
                # 崩溃或停止，恢复会从 n+1 开始，第 n 章的记忆与报告永久缺失。
                if blocked_at is None:
                    progress[step.id] = {"current": n, "total": step.repeat}
                    self._save_progress(progress)
                    tokens = self.total_usage.get("total_tokens") or sum(
                        v for k, v in self.total_usage.items() if k.endswith("tokens")
                    )
                    logger.info(
                        "[LONGRUN] 第%d章完成 current=%d/%d tokens=%s errors=%d",
                        n, n, step.repeat, tokens, len(progress.get("_errors") or []),
                    )
            if blocked_at is not None:
                logger.warning("第%d章写盘被拒，进度停在 %d，恢复时会重试该章",
                               blocked_at, blocked_at - 1)
            errors = progress.get("_errors")
            if errors:
                logger.warning("工作流循环完成，共 %d 章失败: %s", len(errors), "；".join(errors))
                if self.on_error:
                    self.on_error(step.id, f"共 {len(errors)} 章失败: {'；'.join(errors[-5:])}")
        else:
            await self._execute_step(step, project, 1, progress)
            progress[step.id] = "done"
            self._save_progress(progress)

    async def _run_periodic(self, ps: WorkflowStep, project: dict, n: int, progress: dict):
        """执行章前/章后定时步骤 — 失败降级为记录错误并继续，不终止整跑。"""
        ps_key = f"{ps.id}_{n}"
        try:
            await self._execute_step(ps, project, n, progress)
        except Exception as e:
            logger.error("定时步骤失败（跳过继续）: %s 第%d章 - %s", ps.id, n, e, exc_info=True)
            progress[ps_key] = "error"  # 恢复时 != "done" 会自动重试
            if self.on_error:
                self.on_error(ps.id, f"第{n}章 {ps.id} 失败: {e}")
            return
        progress[ps_key] = "done"
        self._save_progress(progress)

    async def _run_sediment(self, n: int, progress: dict) -> bool:
        """写后沉淀：从第 n 章最终正文提取记忆项。

        在该章全部章后步骤（含润色）完成后调用，读最终落盘文件而非 LLM 原始输出。
        LLM 语义提取为主通道，失败/无 Agent 时正则兜底。
        断点键 sediment_{n}：失败不标记，恢复时自动重试；失败不中断章节循环。
        """
        if progress.get(f"sediment_{n}") == "done":
            return True
        chapter_file = self._find_chapter_file(n)
        if not chapter_file:
            return True  # 章节写作失败或被跳过，无可沉淀
        content = project_io.read_md(self.project_dir / "chapters" / chapter_file)
        if not content:
            return True
        llm_data = await self._llm_sediment(n, content)
        try:
            sediment_chapter(self.project_dir, n, content, llm_data=llm_data)
            progress[f"sediment_{n}"] = "done"
            self._save_progress(progress)
            logger.debug("第%d章写后沉淀完成（%s）", n, "LLM" if llm_data else "正则兜底")
            return True
        except Exception as e:
            logger.error("第%d章写后沉淀失败（恢复时重试）: %s", n, e, exc_info=True)
            if self.on_error:
                self.on_error("sediment", f"第{n}章沉淀失败: {e}")
            return False

    async def _llm_sediment(self, n: int, content: str) -> dict | None:
        """LLM 记忆沉淀（主通道）。

        Returns:
            沉淀 JSON；无可用 Agent、返回非 JSON 或调用失败时返回 None
            （sediment_chapter 据此回退正则，不抛异常）。
        """
        agent = _fallback_agent(self, (SKILL_REVIEW, SKILL_PROOFREAD))
        if not agent:
            return None
        mem = MemoryScratchpad(self.project_dir)
        active_loops = mem.get_open_loops(limit=15, current_chapter=n)
        prompt = build_memory_sediment_prompt(n, active_loops, content)
        try:
            # 沉淀是结构化抽取：关思考、不写历史，避免与本章其他工具步重复计费
            response = await agent.run(prompt, thinking="off", keep_history=False)
        except Exception as e:
            logger.warning("第%d章 LLM 沉淀调用失败，回退正则提取: %s", n, e)
            return None
        data = extract_json_object(response.content or "")
        if not data:
            # 预期回退路径（正则兜底），不是故障，降为 debug 防刷屏
            logger.debug("第%d章 LLM 沉淀返回非 JSON，回退正则提取", n)
            return None
        return data

    async def _run_single(self, step: WorkflowStep, project: dict, n: int, progress: dict):
        # ── 内置特殊步骤（不走标准 LLM 流程） ──
        builtin = BUILTIN_STEP_HANDLERS.get(step.id)
        if builtin:
            await builtin(self, step, n)
            return

        agent = self.find_agent(step.needs)
        if not agent:
            if step.optional:
                logger.debug("步骤 %s 未找到匹配智能体，已跳过（optional=True）", step.id)
                return
            raise WorkflowError(f"没有智能体能做「{step.needs}」")

        # 章节写作：提前检查是否已有内容，跳过 LLM 调用
        if step.id == "chapter":
            existing = self._find_chapter_file(n)
            if existing:
                existing_path = self.project_dir / "chapters" / existing
                if existing_path.exists() and existing_path.stat().st_size > 0:
                    logger.info("[跳过] 第%d章已有内容，不覆写", n)
                    if self.on_step_start:
                        self.on_step_start(step.id, n, agent.title)
                    if self.on_step_end:
                        self.on_step_end(step.id, n, agent.title, f"[跳过] 第{n}章已有内容，不覆写")
                    return

        logger.info("执行步骤: %s (第%d章) -> %s", step.id, n, agent.title)
        # 注意：不清空 agent 历史 — 步骤间需要上下文关联
        # （大纲→人物→世界观 通过对话历史传递，清空会导致各写各的）
        if self.on_step_start:
            self.on_step_start(step.id, n, agent.title)

        context = self._context_builder.build(step.input_files, n, step_id=step.id)
        skill_text = self._skill_text(step.skills)
        if skill_text:
            context = f"{context}\n\n{skill_text}" if context else skill_text
        prompt = self._format_prompt(step.prompt, project, n)

        # 注入节奏控制
        total = project.get("target_chapters", 0)
        if total > 0 and step.id == "chapter":
            prompt += "\n\n" + build_pacing_context(n, total, for_update=False)
        if total > 0 and step.id.startswith(PLANNING_UPDATE_STEP_PREFIX):
            prompt += "\n\n" + build_pacing_context(n, total, for_update=True)

        # 章节写作：注入已有标题约束，防止 LLM 每次生成不同标题
        if step.id == "chapter":
            existing = self._find_chapter_file(n)
            if existing:
                # 从文件名提取已有标题: "3_黎明前夜.txt" -> "黎明前夜"
                m = project_io._CHAPTER_RE.match(existing)
                if m:
                    prompt += build_title_constraint(m.group(2))

        try:
            thinking = step.thinking if getattr(step, "thinking", "auto") != "auto" else (
                "off" if step.id in NO_THINKING_STEP_IDS else "auto"
            )
            keep_history = step.id not in NO_HISTORY_STEP_IDS
            if (step.id == "chapter" and self.on_chunk is not None
                    and getattr(agent.llm, "supports_content_stream", True)):
                # 正文流式：边出边进编辑器；此分支不挂工具，换取实时可见
                parts: list[str] = []
                # 有些模型 thinking=off 时流式只推 reasoning、不推正文；正文流式用 auto 拿 text
                stream_thinking = "auto" if thinking == "off" else thinking
                async for kind, chunk in agent.stream_run(
                    prompt, context=context, thinking=stream_thinking, keep_history=keep_history,
                ):
                    if kind == "text" and chunk:
                        parts.append(chunk)
                        self.on_chunk(step.id, n, kind, chunk)
                if not parts:
                    logger.warning("流式未返回正文，回退非流式: %s (第%d章)", step.id, n)
                    response = await agent.run(
                        prompt, context=context, thinking=thinking, keep_history=keep_history,
                    )
                else:
                    response = LLMResponse(content="".join(parts))
                logger.info("步骤完成（流式）: %s (第%d章)", step.id, n)
            else:
                if step.id == "chapter" and self.on_chunk is not None:
                    logger.info("模型不支持正文流式，本章使用非流式生成: %s", agent.name)
                use_tools = self.enable_tools and bool(step.tools) and step.tool_rounds > 0
                if use_tools:
                    from ..agents.tools import NovelToolkit
                    toolkit = NovelToolkit(
                        self.project_dir, self.project_info, n, self._skill_registry
                    ).build(step.tools)

                    def _on_tool(kind: str, payload: dict):
                        self._emit_event(kind, step=step.id, n=n, **payload)

                    response = await agent.run_with_tools(
                        toolkit,
                        prompt,
                        context=context,
                        thinking=thinking,
                        keep_history=keep_history,
                        max_rounds=step.tool_rounds,
                        on_event=_on_tool,
                    )
                else:
                    run_kwargs = dict(
                        context=context, thinking=thinking, keep_history=keep_history,
                    )
                    if step.max_tokens:
                        run_kwargs["max_tokens"] = step.max_tokens
                    response = await agent.run(prompt, **run_kwargs)
                # usage 由 BaseAgent.usage_sink 汇总（含内置步骤与工具循环）
                logger.info("步骤完成: %s (第%d章)", step.id, n)
        except Exception as e:
            logger.error("步骤执行失败: %s (第%d章) - %s", step.id, n, e, exc_info=True)
            if self.on_error:
                self.on_error(step.id, str(e))
            raise

        original = response.content or ""
        cleaned = clean_model_text(original, step.id)
        if cleaned != original:
            logger.warning("步骤 %s 剥离模型思考前缀 %d 字符", step.id, len(original) - len(cleaned))
            response.content = cleaned
        if not cleaned.strip():
            logger.error("步骤 %s 返回空内容（可能只有思考过程）", step.id)
            if self.on_error:
                self.on_error(step.id, "模型只返回了思考过程，没有正文")
            raise RuntimeError("模型只返回了思考过程，没有可写正文")

        output = self._resolve_output(step, project, n, response.content)

        if output:
            written = self._write_output(step, n, output, response.content, agent)
            if not written:
                # 章节正文被拒：抛给循环层，记录后不推进 current。
                # 反模式回灌已移入 builtin 的 _run_review（review 走内置分发，
                # 本函数后半段对 review 不可达）。
                if step.id == "chapter":
                    raise ChapterWriteRejected(f"第{n}章正文未通过校验，未写入")
                return

        if self.on_step_end:
            self.on_step_end(step.id, n, agent.title, response.content)

    def _resolve_output(self, step: WorkflowStep, project: dict, n: int, content: str) -> str:
        """确定步骤的输出路径。"""
        if step.id in ("chapter", "polish"):
            # 正文写作和润色都定位到实际章节文件（{n}_标题.txt）
            existing = self._find_chapter_file(n)
            if existing:
                return f"chapters/{existing}"
            if step.id == "chapter":
                title = extract_chapter_title(content, n)
                return f"chapters/{project_io.chapter_filename(n, title)}"
            return ""  # polish 没有已有章节时跳过
        if step.id == "inspiration":
            return f"inspiration/{project_io.inspiration_filename(n, '灵感')}"
        return step.output.format(n=n, **project)

    def _write_output(self, step: WorkflowStep, n: int, output: str, content: str, agent: BaseAgent) -> bool:
        if step.id in CHAPTER_CONTENT_STEP_IDS:
            from ..canon.seal import is_sealed
            if is_sealed(self.project_dir, n):
                logger.error("第%d章已封印，拒绝覆写（%s）", n, step.id)
                if self.on_error:
                    self.on_error(step.id, f"第{n}章已封印，拒绝覆写")
                return False
        """写出步骤结果，带规划文档保护、内容校验、备份与写后验证。

        Returns:
            True 表示已写出（或跳过写出的情形已回调）；False 表示调用方应立即结束本步骤。
        """
        out_path = self.project_dir / output

        # 规划文档保护：已有内容的 planning/ 文件不覆盖（update_planning=True 时强制覆盖）
        if output.startswith("planning/") and out_path.exists() and out_path.stat().st_size > 0:
            if not step.update_planning:
                if self.on_step_end:
                    self.on_step_end(step.id, n, agent.title, f"[跳过] {output} 已有内容，不覆写")
                return False
            logger.info("反哺更新规划文档: %s", output)

        # ── 关键保护：LLM 返回为空或过短时绝不写入，防止清空已有文件 ──
        if step.id in CHAPTER_CONTENT_STEP_IDS:
            is_valid, reason = project_io.validate_chapter_content(content)
            if not is_valid:
                logger.error("章节内容校验失败，跳过写入! step=%s n=%d output=%s reason=%s",
                             step.id, n, output, reason)
                if self.on_error:
                    self.on_error(step.id, f"第{n}章内容校验失败: {reason}，跳过写入")
                return False
        elif step.id in ("proofread", "review") and not content.strip():
            # 报告类步骤：空内容不写入，防止清空已有报告
            logger.warning("%s 返回空内容，跳过写入 %s", step.id, output)
            return False

        # 润色保护：输出必须比原文更长或持平（润色不应大幅缩短），且不得偏离主题
        if step.id == "polish" and out_path.exists():
            original = project_io.read_md(out_path)
            if original and len(content.strip()) < len(original.strip()) * 0.5:
                logger.warning("润色输出过短（%d < 原文 50%%），跳过覆写", len(content))
                if self.on_error:
                    self.on_error(step.id, f"第{n}章润色输出过短，已跳过覆写")
                return False

        # 章节文件写入前备份（保留原始备份，不覆盖已有 .bak）
        if step.id in CHAPTER_CONTENT_STEP_IDS and out_path.exists() and out_path.stat().st_size > 0:
            backup_path = out_path.with_suffix(".bak.txt")
            if not backup_path.exists():
                shutil.copy2(out_path, backup_path)
                logger.debug("章节备份: %s", backup_path)

        write_content = project_io.normalize_chapter_content(content) if step.id in CHAPTER_CONTENT_STEP_IDS else content
        project_io.write_md(out_path, write_content)

        # 写后验证：确认文件存在且内容不为空
        if step.id in CHAPTER_CONTENT_STEP_IDS:
            if not out_path.exists() or out_path.stat().st_size == 0:
                logger.error("写后验证失败! 文件为空或不存在: %s", out_path)
                # 尝试从备份恢复
                backup_path = out_path.with_suffix(".bak.txt")
                if backup_path.exists() and backup_path.stat().st_size > 0:
                    shutil.copy2(backup_path, out_path)
                    logger.info("已从备份恢复: %s", backup_path)
                elif self.on_error:
                    self.on_error(step.id, f"第{n}章写入失败且无法恢复")

        return True

    def _format_prompt(self, template: str, project: dict, n: int) -> str:
        """格式化提示词模板 — 只替换已知键，未知 {x} 原样保留。

        不用 str.format：AI 生成的工作流 prompt 里常带 JSON 示例等字面花括号，
        str.format 会直接抛 KeyError/ValueError 中断整个工作流。
        """
        values = {"n": n, **project}
        return re.sub(
            r"\{(\w+)\}",
            lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0),
            template,
        )

    def _save_progress(self, progress: dict):
        """保存工作流进度到文件（附带累计 token 用量）。"""
        progress["_usage"] = dict(self.total_usage)
        project_io.save_workflow(self.project_dir, {"progress": progress})
