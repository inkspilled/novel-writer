"""工作流执行器 — 技能匹配 + 文件传递。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from .. import project_io
from ..agents.base import BaseAgent
from ..logger import get_logger
from .builtin_steps import BUILTIN_STEP_HANDLERS
from .constants import (
    CHAPTER_CONTENT_STEP_IDS,
    PLANNING_UPDATE_STEP_PREFIX,
    POST_CHAPTER_PERIODIC_IDS,
    PRE_CHAPTER_PERIODIC_IDS,
)
from .context import ContextBuilder
from .definition import WorkflowDef, WorkflowStep
from .prompts import build_pacing_context, build_title_constraint
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
    ):
        self.agents = agents
        self.project_dir = project_dir
        self.project_info = project_info
        self.max_context_tokens = max_context_tokens
        self._agent_by_skill: dict[str, list[BaseAgent]] = {}
        self._build_skill_index()
        self._stop = False
        self._context_builder = ContextBuilder(project_dir, project_info, max_context_tokens)
        # 回调
        self.on_step_start: Callable[[str, int, str], None] | None = None  # (step_id, n, agent_title)
        self.on_step_end: Callable[[str, int, str, str], None] | None = None  # (step_id, n, agent_title, output)
        self.on_error: Callable[[str, str], None] | None = None  # (step_id, error)

    def _build_skill_index(self):
        for agent in self.agents.values():
            for skill in agent.config.skills:
                self._agent_by_skill.setdefault(skill, []).append(agent)

    def find_agent(self, skill: str) -> BaseAgent | None:
        candidates = self._agent_by_skill.get(skill, [])
        return candidates[0] if candidates else None

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

        for step in normal_steps:
            if self._stop:
                break

            step_progress = progress.get(step.id, "pending")
            if step_progress == "done":
                continue

            if step.repeat > 0:
                # 循环步骤（如逐章写作）
                default_start = workflow.project.get("_start_chapter", 1)
                start = default_start
                if isinstance(step_progress, dict):
                    start = max(step_progress.get("current", default_start), default_start)
                # 查漏补缺模式：只执行缺失章节
                gaps = workflow.project.get("_gaps", [])
                chapters_to_write = gaps if gaps else list(range(start, step.repeat + 1))
                for n in chapters_to_write:
                    if self._stop:
                        break
                    # 章前定时步骤（灵感、角色推演）
                    for ps in periodic_steps:
                        if self._stop:
                            break
                        if ps.id in PRE_CHAPTER_PERIODIC_IDS and n % ps.every == 0:
                            ps_key = f"{ps.id}_{n}"
                            if progress.get(ps_key) != "done":
                                await self._run_single(ps, workflow.project, n, progress)
                                progress[ps_key] = "done"
                                self._save_progress(progress)
                    # 执行章节写作
                    await self._run_single(step, workflow.project, n, progress)
                    progress[step.id] = {"current": n, "total": step.repeat}
                    self._save_progress(progress)
                    # 章后定时步骤（润色、校验、摘要、规划反哺）
                    for ps in periodic_steps:
                        if self._stop:
                            break
                        if ps.id in POST_CHAPTER_PERIODIC_IDS and n % ps.every == 0:
                            ps_key = f"{ps.id}_{n}"
                            if progress.get(ps_key) != "done":
                                await self._run_single(ps, workflow.project, n, progress)
                                progress[ps_key] = "done"
                                self._save_progress(progress)
            else:
                # 单次步骤
                await self._run_single(step, workflow.project, 1, progress)
                progress[step.id] = "done"
                self._save_progress(progress)

        logger.info("工作流执行完成: %s", workflow.name)
        return progress

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

        # 写后沉淀：章节写完后提取记忆项
        if step.id == "chapter" and response:
            sediment_chapter(self.project_dir, n, response.content)

        # 审稿后：提取反模式
        if step.id == "review" and response:
            from ..anti_patterns import AntiPatternTracker
            tracker = AntiPatternTracker(self.project_dir)
            tracker.add_from_review(response.content, n)

        if self.on_step_end:
            self.on_step_end(step.id, n, agent.title, response.content)

    def _resolve_output(self, step: WorkflowStep, project: dict, n: int, content: str) -> str:
        """确定步骤的输出路径。"""
        if step.id == "chapter":
            existing = self._find_chapter_file(n)
            if existing:
                return f"chapters/{existing}"
            title = extract_chapter_title(content, n)
            return f"chapters/{project_io.chapter_filename(n, title)}"
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
        """格式化提示词模板。"""
        return template.format(n=n, **project)

    def _save_progress(self, progress: dict):
        """保存工作流进度到文件。"""
        project_io.save_workflow(self.project_dir, {"progress": progress})
