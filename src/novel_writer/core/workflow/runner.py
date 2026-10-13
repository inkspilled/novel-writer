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
from ..logger import get_logger
from ..memory import MemoryScratchpad
from .builtin_steps import BUILTIN_STEP_HANDLERS, _fallback_agent
from .constants import (
    CHAPTER_CONTENT_STEP_IDS,
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

logger = get_logger(__name__)


class WorkflowError(Exception):
    pass


class WorkflowRunner:
    """工作流执行器 — 技能匹配 + 文件传递。"""

    def __init__(
        self,
        agents: dict[str, BaseAgent],
        project_dir: Path,
        project_info: dict,
        max_context_tokens: int = 120_000,
        rotate_agents: bool = False,
    ):
        self.agents = agents
        self.project_dir = project_dir
        self.project_info = project_info
        self.max_context_tokens = max_context_tokens
        # 同技能多 Agent 轮换开关：默认关闭。规划链（大纲→人物→世界观）
        # 依赖同一 Agent 实例的对话历史传递，轮换会打断该机制，故需显式开启
        self.rotate_agents = rotate_agents
        self._agent_by_skill: dict[str, list[BaseAgent]] = {}
        self._skill_cycles: dict[str, Iterator[BaseAgent]] = {}
        self._build_skill_index()
        self._stop = False
        self.total_usage: dict[str, int] = {}  # 本次运行累计 token 用量
        self._context_builder = ContextBuilder(project_dir, project_info, max_context_tokens)
        # 周期步骤触发时机（章前/章后）；未声明的自定义周期步骤默认按章后执行
        self._pre_ids = set(PRE_CHAPTER_PERIODIC_IDS)
        self._post_ids = set(POST_CHAPTER_PERIODIC_IDS)
        # 回调
        self.on_step_start: Callable[[str, int, str], None] | None = None  # (step_id, n, agent_title)
        self.on_step_end: Callable[[str, int, str, str], None] | None = None  # (step_id, n, agent_title, output)
        self.on_error: Callable[[str, str], None] | None = None  # (step_id, error)

    def _build_skill_index(self):
        for agent in self.agents.values():
            for skill in agent.config.skills:
                self._agent_by_skill.setdefault(skill, []).append(agent)
        self._skill_cycles = {skill: itertools.cycle(items) for skill, items in self._agent_by_skill.items()}

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

        # 预处理：分离定时步骤和普通步骤
        periodic_steps = [s for s in workflow.steps if s.every > 0]
        normal_steps = [s for s in workflow.steps if s.every == 0]

        # 未声明触发时机的自定义周期步骤默认按章后执行（原先会被静默丢弃）
        for ps in periodic_steps:
            if ps.id not in self._pre_ids and ps.id not in self._post_ids:
                logger.warning("周期步骤 %s 未声明章前/章后触发时机，默认按章后执行", ps.id)
                self._post_ids.add(ps.id)

        # 将 normal_steps 按 parallel_ok 分组：连续的 parallel_ok 步骤归为一组并发执行
        step_groups: list[list[WorkflowStep]] = []
        for step in normal_steps:
            if step.parallel_ok and step_groups and step_groups[-1] and step_groups[-1][0].parallel_ok:
                step_groups[-1].append(step)
            else:
                step_groups.append([step])

        for group in step_groups:
            if self._stop:
                break
            if len(group) == 1:
                # 断点恢复：已完成（或循环已跑满）的步骤直接跳过，不重复烧 LLM 调用
                step = group[0]
                if self._step_completed(step, progress):
                    logger.info("[跳过] 步骤 %s 已完成（断点恢复）", step.id)
                    continue
                await self._run_step_or_repeat(group[0], workflow.project, progress, periodic_steps)
            else:
                # 并发执行独立步骤（如规划阶段的人物/世界观/时间线/主线/支线/伏笔）
                logger.info("并发执行 %d 个独立步骤: %s", len(group), [s.id for s in group])
                tasks = [
                    self._run_step_or_repeat(s, workflow.project, progress, periodic_steps)
                    for s in group
                    if progress.get(s.id, "pending") != "done"
                ]
                if tasks:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for s, r in zip([s for s in group if progress.get(s.id, "pending") != "done"], results):
                        if isinstance(r, Exception):
                            logger.error("并发步骤失败: %s - %s", s.id, r)
                            if self.on_error:
                                self.on_error(s.id, str(r))
                            raise r
                        progress[s.id] = "done"
                        self._save_progress(progress)

        logger.info(
            "工作流执行完成: %s (累计 token 用量: %s)",
            workflow.name,
            self.total_usage or "未上报",
        )
        return progress

    @staticmethod
    def _step_completed(step: WorkflowStep, progress: dict) -> bool:
        """步骤是否已完成（普通步骤看 done；循环步骤看 current 是否跑满）。"""
        prog = progress.get(step.id, "pending")
        if prog == "done":
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
            start = default_start
            step_progress = progress.get(step.id, "pending")
            if isinstance(step_progress, dict):
                start = max(step_progress.get("current", default_start), default_start)
            gaps = project.get("_gaps", [])
            chapters_to_write = gaps if gaps else list(range(start, step.repeat + 1))
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
                    await self._run_single(step, project, n, progress)
                except WorkflowError:
                    raise  # 配置类错误（如找不到智能体），继续重试无意义
                except Exception as e:
                    # 单章失败不终止整跑：记录后跳到下一章，结尾汇总
                    logger.error("章节步骤失败（跳过继续）: 第%d章 - %s", n, e, exc_info=True)
                    progress.setdefault("_errors", []).append(f"第{n}章: {e}")
                    if self.on_error:
                        self.on_error(step.id, f"第{n}章写作失败: {e}")
                    continue
                progress[step.id] = {"current": n, "total": step.repeat}
                self._save_progress(progress)
                for ps in periodic_steps:
                    if self._stop:
                        break
                    if ps.id in self._post_ids and n % ps.every == 0:
                        if progress.get(f"{ps.id}_{n}") != "done":
                            await self._run_periodic(ps, project, n, progress)
                # 写后沉淀：等全部章后步骤（含润色改写正文）跑完后，
                # 从最终落盘正文提取记忆——草稿不是正典，润色前的文本不入账
                await self._run_sediment(n, progress)
            errors = progress.get("_errors")
            if errors:
                logger.warning("工作流循环完成，共 %d 章失败: %s", len(errors), "；".join(errors))
                if self.on_error:
                    self.on_error(step.id, f"共 {len(errors)} 章失败: {'；'.join(errors[-5:])}")
        else:
            await self._run_single(step, project, 1, progress)
            progress[step.id] = "done"
            self._save_progress(progress)

    async def _run_periodic(self, ps: WorkflowStep, project: dict, n: int, progress: dict):
        """执行章前/章后定时步骤 — 失败降级为记录错误并继续，不终止整跑。"""
        ps_key = f"{ps.id}_{n}"
        try:
            await self._run_single(ps, project, n, progress)
        except Exception as e:
            logger.error("定时步骤失败（跳过继续）: %s 第%d章 - %s", ps.id, n, e, exc_info=True)
            progress[ps_key] = "error"  # 恢复时 != "done" 会自动重试
            if self.on_error:
                self.on_error(ps.id, f"第{n}章 {ps.id} 失败: {e}")
            return
        progress[ps_key] = "done"
        self._save_progress(progress)

    async def _run_sediment(self, n: int, progress: dict):
        """写后沉淀：从第 n 章最终正文提取记忆项。

        在该章全部章后步骤（含润色）完成后调用，读最终落盘文件而非 LLM 原始输出。
        LLM 语义提取为主通道，失败/无 Agent 时正则兜底。
        断点键 sediment_{n}：失败不标记，恢复时自动重试；失败不中断章节循环。
        """
        if progress.get(f"sediment_{n}") == "done":
            return
        chapter_file = self._find_chapter_file(n)
        if not chapter_file:
            return  # 章节写作失败或被跳过，无可沉淀
        content = project_io.read_md(self.project_dir / "chapters" / chapter_file)
        if not content:
            return
        llm_data = await self._llm_sediment(n, content)
        try:
            sediment_chapter(self.project_dir, n, content, llm_data=llm_data)
            progress[f"sediment_{n}"] = "done"
            self._save_progress(progress)
            logger.debug("第%d章写后沉淀完成（%s）", n, "LLM" if llm_data else "正则兜底")
        except Exception as e:
            logger.error("第%d章写后沉淀失败（恢复时重试）: %s", n, e, exc_info=True)
            if self.on_error:
                self.on_error("sediment", f"第{n}章沉淀失败: {e}")

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
            response = await agent.run(prompt)
        except Exception as e:
            logger.warning("第%d章 LLM 沉淀调用失败，回退正则提取: %s", n, e)
            return None
        m = re.search(r"\{[\s\S]+\}", response.content or "")
        if not m:
            logger.warning("第%d章 LLM 沉淀返回非 JSON，回退正则提取", n)
            return None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            logger.warning("第%d章 LLM 沉淀 JSON 解析失败，回退正则提取", n)
            return None
        return data if isinstance(data, dict) else None

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
            response = await agent.run(prompt, context=context)
            for k, v in (response.usage or {}).items():
                if isinstance(v, (int, float)):
                    self.total_usage[k] = int(self.total_usage.get(k, 0) + v)
            logger.info("步骤完成: %s (第%d章)", step.id, n)
        except Exception as e:
            logger.error("步骤执行失败: %s (第%d章) - %s", step.id, n, e, exc_info=True)
            if self.on_error:
                self.on_error(step.id, str(e))
            raise

        output = self._resolve_output(step, project, n, response.content)

        if output:
            written = self._write_output(step, n, output, response.content, agent)
            if not written:
                return

        # 审稿后：提取反模式
        if step.id == "review" and response:
            from ..anti_patterns import AntiPatternTracker
            tracker = AntiPatternTracker(self.project_dir)
            tracker.add_from_review(response.content, n)

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
