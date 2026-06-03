"""工作流引擎 — 技能驱动的多 Agent 协作。

支持:
- 线性步骤执行
- 技能匹配（needs → agent.skills）
- 循环步骤（repeat: 逐章写作）
- 文件输入/输出（读取 MD 文件作为上下文，输出写入文件）
- 模板变量插值（{n}, {title}, {genre} 等）
- 进度回调
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from . import project_io
from .agents.base import BaseAgent
from .llm.base import BaseLLM, LLMMessage
from .logger import get_logger

logger = get_logger(__name__)
from .logger import get_logger

logger = get_logger(__name__)


# ── 工作流模式 ──

class WorkflowMode(Enum):
    NEW_BOOK = "new_book"           # 新书：从立意到审校全流程
    CONTINUE = "continue"           # 续写：从已有章节继续
    FILL_GAPS = "fill_gaps"         # 查漏补缺：检查缺失章节并补写
    VALIDATE = "validate"           # 校验：审核+校对已有章节


@dataclass
class WorkflowStep:
    """工作流步骤定义。"""
    id: str = ""
    needs: str = ""  # 需要的技能名称
    prompt: str = ""  # 指令模板，支持 {变量} 插值
    input_files: list[str] = field(default_factory=list)  # 输入文件列表
    output: str = ""  # 输出文件名，支持 {n} 占位
    repeat: int = 0  # 循环次数（0 = 不循环）
    every: int = 0  # 每隔 N 步执行一次
    optional: bool = False  # 找不到匹配 Agent 时跳过
    update_planning: bool = False  # 强制覆盖 planning/ 文件（用于反哺步骤）


@dataclass
class WorkflowDef:
    """工作流定义。"""
    name: str = ""
    description: str = ""
    # 项目信息（用于变量插值）
    project: dict = field(default_factory=dict)
    steps: list[WorkflowStep] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> WorkflowDef:
        steps = []
        for s in data.get("steps", []):
            steps.append(WorkflowStep(
                id=s.get("id", ""),
                needs=s.get("needs", ""),
                prompt=s.get("prompt", ""),
                input_files=s.get("input", []),
                output=s.get("output", ""),
                repeat=s.get("repeat", 0),
                every=s.get("every", 0),
                optional=s.get("optional", False),
                update_planning=s.get("update_planning", False),
            ))
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            project=data.get("project", {}),
            steps=steps,
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "project": self.project,
            "steps": [
                {
                    "id": s.id,
                    "needs": s.needs,
                    "prompt": s.prompt,
                    "input": s.input_files,
                    "output": s.output,
                    "repeat": s.repeat,
                    "every": s.every,
                    "optional": s.optional,
                    "update_planning": s.update_planning,
                }
                for s in self.steps
            ],
        }


class WorkflowError(Exception):
    pass


class WorkflowRunner:
    """工作流执行器 — 技能匹配 + 文件传递。"""

    def __init__(
        self,
        agents: dict[str, BaseAgent],
        project_dir: Path,
        project_info: dict,
    ):
        self.agents = agents
        self.project_dir = project_dir
        self.project_info = project_info
        self._agent_by_skill: dict[str, list[BaseAgent]] = {}
        self._build_skill_index()
        self._stop = False
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
        chapters_dir = self.project_dir / project_io.CHAPTERS_DIR
        if not chapters_dir.exists():
            return None
        for f in chapters_dir.iterdir():
            if not f.is_file():
                continue
            m = project_io._CHAPTER_RE.match(f.name)
            if m and int(m.group(1)) == n:
                return f.name
        return None

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
                        if ps.id in ("inspiration", "sim") and n % ps.every == 0:
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
                        if ps.id in ("polish", "proofread", "summary", "toc", "chapter_summary", "update_outline", "update_characters", "update_foreshadow") and n % ps.every == 0:
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

    async def run_single_step(self, step_id: str, workflow: WorkflowDef, n: int = 1) -> str:
        """执行单个步骤，返回输出内容。"""
        step = None
        for s in workflow.steps:
            if s.id == step_id:
                step = s
                break
        if not step:
            raise WorkflowError(f"步骤 '{step_id}' 不存在")

        # ── 纯工具步骤（不需要 LLM） ──
        if step.id == "toc":
            project_io.generate_toc(self.project_dir)
            return "目录已更新"

        # ── fix_titles: LLM 根据正文内容生成正确标题 ──
        if step.id == "fix_titles":
            agent = self.find_agent("剧情审查") or self.find_agent("错别字检查") or list(self.agents.values())[0] if self.agents else None
            if not agent:
                return "标题校准跳过：没有可用的智能体"
            chapters = project_io.scan_chapters(self.project_dir)
            if not chapters:
                return "没有章节需要校准"
            excerpts = []
            for ch in chapters:
                content = project_io.read_md(ch["content_path"])
                excerpt = content[:500].replace("\n", " ")
                excerpts.append(f"第{ch['number']}章（当前标题：「{ch['title']}」）：\n{excerpt}")
            prompt = (
                "你是小说编辑。以下是各章的开头内容和当前标题。\n"
                "当前标题可能重复或不准确，请根据正文内容为每章重新拟定一个2-6字的精准标题。\n"
                "严格输出 JSON 格式，不要输出其他内容：\n"
                '{"1": "新标题", "2": "新标题", ...}\n\n'
                + "\n\n".join(excerpts)
            )
            resp = await agent.run(prompt)
            import json, re as _re
            text = resp.content.strip()
            m = _re.search(r'\{[^{}]+\}', text)
            if m:
                title_map = json.loads(m.group())
                logs = []
                for ch_num_str, new_title in title_map.items():
                    ch_num = int(ch_num_str)
                    old = project_io.rename_chapter(self.project_dir, ch_num, str(new_title).strip())
                    if old:
                        logs.append(f"第{ch_num}章: 「{old}」→「{new_title}」")
                project_io.generate_toc(self.project_dir)
                return f"标题校准完成，修正 {len(logs)} 处\n" + "\n".join(logs)
            else:
                return f"标题校准失败：LLM 返回格式异常\n{text[:200]}"

        agent = self.find_agent(step.needs)
        if not agent:
            raise WorkflowError(f"没有智能体能做「{step.needs}」")

        # 章节写作：已有内容直接跳过，不浪费 LLM 调用
        if step.id == "chapter":
            existing = self._find_chapter_file(n)
            if existing:
                existing_path = self.project_dir / "chapters" / existing
                if existing_path.exists() and existing_path.stat().st_size > 0:
                    return f"[跳过] 第{n}章已有内容，不覆写"

        context = self._build_context(step.input_files, n)
        prompt = self._format_prompt(step.prompt, workflow.project, n)

        # 章节写作：注入已有标题约束
        if step.id == "chapter":
            existing = self._find_chapter_file(n)
            if existing:
                m = project_io._CHAPTER_RE.match(existing)
                if m:
                    old_title = m.group(2)
                    prompt += f"\n\n【标题约束】本章标题必须是「{old_title}」，正文第一行用 # {old_title}，不得更改标题。"

        response = await agent.run(prompt, context=context)

        # 确定输出路径
        if step.id == "chapter":
            existing = self._find_chapter_file(n)
            if existing:
                output = f"chapters/{existing}"
            else:
                title = _extract_chapter_title(response.content, n)
                output = f"chapters/{project_io.chapter_filename(n, title)}"
        elif step.id == "inspiration":
            output = f"inspiration/{project_io.inspiration_filename(n, '灵感')}"
        else:
            output = step.output.format(n=n, **workflow.project)

        if output:
            out_path = self.project_dir / output
            # 规划文档保护：已有内容的 planning/ 文件不覆盖（update_planning=True 时强制覆盖）
            if output.startswith("planning/") and out_path.exists() and out_path.stat().st_size > 0:
                if not step.update_planning:
                    return f"[跳过] {output} 已有内容，不覆写"
                logger.info("反哺更新规划文档: %s", output)

            # ── 关键保护：LLM 返回为空时绝不写入 ──
            if not response.content or not response.content.strip():
                logger.error("LLM 返回为空，跳过写入! step=%s n=%d output=%s", step.id, n, output)
                return f"[错误] LLM 返回为空，跳过写入 {output}"

            # 章节文件写入前备份
            if step.id == "chapter" and out_path.exists() and out_path.stat().st_size > 0:
                backup_path = out_path.with_suffix(".bak.txt")
                import shutil
                shutil.copy2(out_path, backup_path)

            project_io.write_md(out_path, response.content)

        return response.content

    async def _run_single(self, step: WorkflowStep, project: dict, n: int, progress: dict):
        # ── 纯工具步骤（不需要 LLM） ──
        if step.id == "toc":
            toc = project_io.generate_toc(self.project_dir)
            logger.info("目录已更新")
            if self.on_step_end:
                self.on_step_end(step.id, n, "系统", "目录已更新")
            return

        # ── chapter_summary: LLM 生成每章概要 ──
        if step.id == "chapter_summary":
            # 找到第 n 章文件
            existing = self._find_chapter_file(n)
            if not existing:
                return
            existing_path = self.project_dir / "chapters" / existing
            if not existing_path.exists() or existing_path.stat().st_size == 0:
                return
            # 检查概要是否已存在
            m = project_io._CHAPTER_RE.match(existing)
            if not m:
                return
            ch_title = m.group(2)
            summary_path = project_io.chapter_summary_path(self.project_dir, n, ch_title)
            if summary_path.exists() and summary_path.stat().st_size > 0:
                logger.debug("第%d章概要已存在，跳过", n)
                return
            # 用审核 agent 生成概要
            agent = self.find_agent("剧情审查") or self.find_agent("校对") or list(self.agents.values())[0] if self.agents else None
            if not agent:
                return
            if self.on_step_start:
                self.on_step_start(step.id, n, agent.title)
            content = project_io.read_md(existing_path)
            prompt = (
                f"请为以下章节生成结构化概要，严格按此格式输出：\n\n"
                f"## 第{n}章 {ch_title} 概要\n"
                f"- **地点**：主要场景\n"
                f"- **出场人物**：人物列表\n"
                f"- **核心事件**：本章发生的关键事情（2-3句）\n"
                f"- **伏笔/悬念**：埋设或回收的伏笔\n"
                f"- **情绪走向**：如 惊疑→震怒→决心\n"
                f"- **承接点**：本章结尾留给下一章的悬念（1句）\n\n"
                f"正文：\n{content[:3000]}"
            )
            try:
                resp = await agent.run(prompt)
                project_io.write_md(summary_path, resp.content)
                logger.info("第%d章概要已生成", n)
                if self.on_step_end:
                    self.on_step_end(step.id, n, agent.title, f"第{n}章概要已生成")
            except Exception as e:
                logger.error("第%d章概要生成失败: %s", n, e)
            return

        # ── fix_titles: LLM 根据正文内容生成正确标题 ──
        if step.id == "fix_titles":
            agent = self.find_agent("剧情审查") or self.find_agent("错别字检查") or list(self.agents.values())[0] if self.agents else None
            if not agent:
                logger.warning("标题校准跳过：没有可用的智能体")
                return
            if self.on_step_start:
                self.on_step_start(step.id, n, agent.title)
            logger.info("执行标题校准（LLM）...")
            chapters = project_io.scan_chapters(self.project_dir)
            if not chapters:
                return
            # 构建上下文：每章前 500 字
            excerpts = []
            for ch in chapters:
                content = project_io.read_md(ch["content_path"])
                excerpt = content[:500].replace("\n", " ")
                excerpts.append(f"第{ch['number']}章（当前标题：「{ch['title']}」）：\n{excerpt}")
            prompt = (
                "你是小说编辑。以下是各章的开头内容和当前标题。\n"
                "当前标题可能重复或不准确，请根据正文内容为每章重新拟定一个2-6字的精准标题。\n"
                "严格输出 JSON 格式，不要输出其他内容：\n"
                '{"1": "新标题", "2": "新标题", ...}\n\n'
                + "\n\n".join(excerpts)
            )
            try:
                resp = await agent.run(prompt)
                import json, re as _re
                # 提取 JSON（兼容 ```json ``` 包裹）
                text = resp.content.strip()
                m = _re.search(r'\{[^{}]+\}', text)
                if m:
                    title_map = json.loads(m.group())
                    logs = []
                    for ch_num_str, new_title in title_map.items():
                        ch_num = int(ch_num_str)
                        old = project_io.rename_chapter(self.project_dir, ch_num, str(new_title).strip())
                        if old:
                            logs.append(f"第{ch_num}章: 「{old}」→「{new_title}」")
                    # 刷新目录
                    project_io.generate_toc(self.project_dir)
                    msg = f"标题校准完成，修正 {len(logs)} 处"
                    logger.info(msg)
                    if self.on_step_end:
                        self.on_step_end(step.id, n, agent.title, msg + ("\n" + "\n".join(logs)))
                else:
                    logger.warning("标题校准：LLM 返回格式异常: %s", text[:200])
                    if self.on_step_end:
                        self.on_step_end(step.id, n, agent.title, "标题校准失败：LLM 返回格式异常")
            except Exception as e:
                logger.error("标题校准失败: %s", e, exc_info=True)
                if self.on_error:
                    self.on_error(step.id, str(e))
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

        context = self._build_context(step.input_files, n)
        prompt = self._format_prompt(step.prompt, project, n)

        # 章节写作：注入已有标题约束，防止 LLM 每次生成不同标题
        if step.id == "chapter":
            existing = self._find_chapter_file(n)
            if existing:
                # 从文件名提取已有标题: "3_黎明前夜.txt" -> "黎明前夜"
                m = project_io._CHAPTER_RE.match(existing)
                if m:
                    old_title = m.group(2)
                    prompt += f"\n\n【标题约束】本章标题必须是「{old_title}」，正文第一行用 # {old_title}，不得更改标题。"

        try:
            response = await agent.run(prompt, context=context)
            logger.info("步骤完成: %s (第%d章)", step.id, n)
        except Exception as e:
            logger.error("步骤执行失败: %s (第%d章) - %s", step.id, n, e, exc_info=True)
            if self.on_error:
                self.on_error(step.id, str(e))
            raise

        # 确定输出路径
        output = step.output
        if step.id == "chapter":
            # 检查是否已有该章节文件
            existing = self._find_chapter_file(n)
            if existing:
                output = f"chapters/{existing}"
            else:
                title = _extract_chapter_title(response.content, n)
                output = f"chapters/{project_io.chapter_filename(n, title)}"
        elif step.id == "inspiration":
            output = f"inspiration/{project_io.inspiration_filename(n, '灵感')}"
        else:
            output = step.output.format(n=n, **project)

        if output:
            out_path = self.project_dir / output
            # 规划文档保护：已有内容的 planning/ 文件不覆盖（update_planning=True 时强制覆盖）
            if output.startswith("planning/") and out_path.exists() and out_path.stat().st_size > 0:
                if not step.update_planning:
                    if self.on_step_end:
                        self.on_step_end(step.id, n, agent.title, f"[跳过] {output} 已有内容，不覆写")
                    return
                logger.info("反哺更新规划文档: %s", output)

            # ── 关键保护：LLM 返回为空时绝不写入，防止清空已有文件 ──
            if not response.content or not response.content.strip():
                logger.error("LLM 返回为空，跳过写入! step=%s n=%d output=%s", step.id, n, output)
                if self.on_error:
                    self.on_error(step.id, f"LLM 返回为空，跳过写入 {output}")
                return

            # 章节文件写入前备份（防止意外丢失）
            if step.id == "chapter" and out_path.exists() and out_path.stat().st_size > 0:
                backup_path = out_path.with_suffix(".bak.txt")
                import shutil
                shutil.copy2(out_path, backup_path)
                logger.debug("章节备份: %s", backup_path)

            project_io.write_md(out_path, response.content)

        # 写后沉淀：章节写完后提取记忆项
        if step.id == "chapter" and response:
            _sediment_chapter(self.project_dir, n, response.content)

        # 审稿后：提取反模式
        if step.id == "review" and response:
            from .anti_patterns import AntiPatternTracker
            tracker = AntiPatternTracker(self.project_dir)
            tracker.add_from_review(response.content, n)

        if self.on_step_end:
            self.on_step_end(step.id, n, agent.title, response.content)

    async def _run_periodic(self, step: WorkflowStep, project: dict, progress: dict):
        """定时触发步骤（如每 N 章注入一次灵感）。"""
        total_chapters = project.get("target_chapters", 20)
        start_chapter = project.get("_start_chapter", 1)
        # 从起始章节的下一个 every 倍数开始
        first = max(step.every, ((start_chapter - 1) // step.every + 1) * step.every)
        for n in range(first, total_chapters + 1, step.every):
            if self._stop:
                break
            step_key = f"{step.id}_{n}"
            if progress.get(step_key) == "done":
                continue
            await self._run_single(step, project, n, progress)
            progress[step_key] = "done"
            self._save_progress(progress)

    def _build_context(self, input_files: list[str], n: int) -> str:
        """三层记忆组装：工作记忆（近章）+ 语义记忆（暂存器）+ 文件上下文。"""
        parts = []
        char_file_content = ""

        # ── 语义记忆：记忆暂存器 ──
        from .memory import MemoryScratchpad
        mem = MemoryScratchpad(self.project_dir)
        memory_text = mem.build_memory_text(limit_per_bucket=5)
        if memory_text:
            parts.append(f"=== 长期记忆 ===\n{memory_text}")

        # ── 反模式约束 ──
        from .anti_patterns import AntiPatternTracker
        tracker = AntiPatternTracker(self.project_dir)
        constraint_text = tracker.get_constraint_text()
        if constraint_text:
            parts.append(constraint_text)

        # ── 追读力指导 ──
        from .reading_power import ReadingPowerTracker
        rp_tracker = ReadingPowerTracker(self.project_dir)
        rp_guidance = rp_tracker.build_guidance(n)
        if rp_guidance:
            parts.append(rp_guidance)

        # ── 角色推演结果 ──
        from .character_sim import load_sim_cache
        sim_text = load_sim_cache(self.project_dir, n)
        if sim_text:
            parts.append(sim_text)

        # ── 章节概要（每章结构化小卡片） ──
        chapter_summaries = project_io.load_chapter_summaries(self.project_dir, up_to_chapter=n, window=10)
        if chapter_summaries:
            parts.append(chapter_summaries)

        for f in input_files:
            if f == "prev_chapters":
                # 工作记忆：最新摘要 + 最近 3 章全文
                summary = project_io.latest_summary(self.project_dir)
                if summary:
                    parts.append(f"=== 剧情摘要 ===\n{summary}")
                chapters_dir = self.project_dir / project_io.CHAPTERS_DIR
                if chapters_dir.exists():
                    # 只读最近 3 章全文（工作记忆窗口）
                    start = max(1, n - 3)
                    for i in range(start, n):
                        for ch_file in sorted(chapters_dir.glob(f"{i}_*.txt")):
                            if ch_file.name.endswith(".outline.md") or ch_file.name.endswith(".summary.md"):
                                continue
                            content = project_io.read_md(ch_file)
                            if content:
                                parts.append(f"=== 第{i}章 ===\n{content}")

                # RAG 检索：从更早章节中检索与当前大纲相关的段落
                try:
                    from .rag import RAGRetriever
                    rag = RAGRetriever(self.project_dir)
                    rag.load_chapters(up_to_chapter=start)
                    # 用大纲摘要作为查询
                    outline_path = self.project_dir / "planning" / "大纲.md"
                    if outline_path.exists():
                        outline = project_io.read_md(outline_path)
                        # 取大纲中关于第 n 章的描述作为查询
                        query = _extract_chapter_query(outline, n)
                        if query:
                            rag_text = rag.retrieve(query, top_k=3)
                            if rag_text:
                                parts.append(rag_text)
                except Exception:
                    pass  # RAG 失败不影响写作
            elif "*" in f:
                target = self.project_dir / f
                parent = target.parent
                pattern = target.name
                if parent.exists():
                    for p in sorted(parent.glob(pattern)):
                        if p.is_file():
                            content = project_io.read_md(p)
                            if content:
                                parts.append(f"=== {p.name} ===\n{content}")
            else:
                p = self.project_dir / f.format(n=n, **self.project_info)
                if p.exists():
                    content = project_io.read_md(p)
                    if content:
                        if f.endswith("人物设定.md"):
                            char_file_content = content
                        parts.append(content)

        # 角色约束
        if char_file_content:
            constraint = _build_character_constraint(char_file_content)
            if constraint:
                parts.append(constraint)

        return "\n\n".join(parts)

    def _format_prompt(self, template: str, project: dict, n: int) -> str:
        """格式化提示词模板。"""
        return template.format(n=n, **project)

    def _save_progress(self, progress: dict):
        """保存工作流进度到文件。"""
        project_io.save_workflow(self.project_dir, {"progress": progress})


def _extract_chapter_title(content: str, n: int) -> str:
    """从章节正文中提取标题（第一个 Markdown 标题）。"""
    for line in content.split("\n"):
        line = line.strip()
        m = re.match(r"^#{1,3}\s+(.+)$", line)
        if m:
            title = m.group(1).strip()
            # 清理标题中的特殊字符
            title = re.sub(r"[*_`#\[\]()]", "", title).strip()
            if title:
                return title[:50]  # 限制长度
    return f"第{n}章"


def _extract_chapter_query(outline: str, n: int) -> str:
    """从大纲中提取第 n 章的描述作为 RAG 查询。"""
    lines = outline.split("\n")
    capture = False
    query_lines = []
    for line in lines:
        if re.match(rf"^#.*第\s*{n}\s*章", line) or re.match(rf"^#.*{n}\.", line):
            capture = True
            continue
        if capture:
            if re.match(r"^#", line) and "第" not in line:
                break
            query_lines.append(line)
            if len(query_lines) >= 5:
                break
    return " ".join(query_lines).strip()[:200]


def _sediment_chapter(project_dir, chapter: int, content: str):
    """写后沉淀：从章节正文中提取记忆项 + 追读力分析。"""
    from .memory import MemoryScratchpad, MemoryItem

    mem = MemoryScratchpad(project_dir)

    # 提取出现的人物名（通过对话符号「」和引号）
    names_in_text = set()
    for m in re.finditer(r"[「""]([^「」""]{1,20})[」""]", content):
        names_in_text.add(m.group(1))

    # 提取可能的状态变化（X突破了/晋升为/受伤了/死了）
    state_patterns = [
        (r"(\w+)(突破|晋升|升级|进阶)", "实力变化"),
        (r"(\w+)(受伤|重伤|濒死|死亡|陨落)", "身体状态"),
        (r"(\w+)(到达|来到|离开|前往|回到)", "位置变化"),
    ]
    for pat, field_name in state_patterns:
        for m in re.finditer(pat, content):
            subj = m.group(1)
            if len(subj) >= 2 and len(subj) <= 6:
                mem.upsert(MemoryItem(
                    category="character_state",
                    subject=subj,
                    aspect=field_name,
                    value=m.group(0),
                    source_chapter=chapter,
                ))

    # 提取伏笔关键词（如果/竟然/原来/没想到）
    foreshadow_patterns = [
        r"(?:竟然|居然|原来|没想到|殊不知)([^。，！？]{5,40})",
        r"(?:如果|倘若|万一)([^。，！？]{5,40})",
    ]
    for pat in foreshadow_patterns:
        for m in re.finditer(pat, content):
            val = m.group(0)[:60]
            mem.upsert(MemoryItem(
                category="open_loops",
                subject=f"第{chapter}章伏笔",
                aspect="悬念",
                value=val,
                source_chapter=chapter,
                payload={"urgency": 0.6},
            ))

    # 记录章节事件
    title = _extract_chapter_title(content, chapter)
    first_para = content.split("\n\n")[0][:100] if content else ""
    mem.upsert(MemoryItem(
        category="story_facts",
        subject=f"第{chapter}章 {title}",
        aspect="剧情",
        value=first_para,
        source_chapter=chapter,
    ))

    # 四维追踪：资源/情感/信息边界/支线
    try:
        from .chapter_tracker import track_all
        track_all(mem, chapter, content)
    except Exception:
        pass

    mem.compact()
    mem.save()

    # 追读力分析
    try:
        from .reading_power import ReadingPowerTracker
        rp_tracker = ReadingPowerTracker(project_dir)
        rp = rp_tracker.analyze_chapter(content, chapter)
        rp_tracker.record(rp)
    except Exception:
        pass


def _build_character_constraint(char_content: str) -> str:
    """从人物设定内容中提取角色信息，生成写作约束指令。"""

    lines = char_content.split("\n")
    characters = []
    protagonist = ""

    # 解析人物设定：匹配 "### 角色名" 或 "## 角色名" 格式
    for i, line in enumerate(lines):
        m = re.match(r"^#{2,4}\s+(.+?)$", line.strip())
        if m:
            name = m.group(1).strip()
            # 跳过非角色标题（如 "一、核心人物设定" 之类的章节标题）
            if re.match(r"^[一二三四五六七八九十]", name):
                continue
            if name in ("核心同伴", "关键对立/引导角色", "关系网络", "动态演进"):
                continue
            # 检查是否标记为主角
            is_protagonist = False
            for j in range(i + 1, min(i + 10, len(lines))):
                if re.match(r"^#{2,4}\s+", lines[j].strip()):
                    break
                if "主角" in lines[j] or "protagonist" in lines[j].lower():
                    is_protagonist = True
                    break
            characters.append(name)
            if is_protagonist and not protagonist:
                protagonist = name

    # 如果没找到显式主角标记，用第一个角色
    if characters and not protagonist:
        protagonist = characters[0]

    if not characters:
        return ""

    # 生成约束指令
    parts = ["=== 【写作约束 · 角色锚定】 ==="]
    parts.append(f"主角：{protagonist}")
    parts.append(f"已登场角色：{', '.join(characters)}")
    parts.append("")
    parts.append("【强制规则】：")
    parts.append(f"1. 本小说主角是「{protagonist}」，全文必须以他/她为核心视角展开，不得中途更换主角。")
    parts.append("2. 严格使用人物设定中的角色名，不得擅自改名、拆分、合并角色。")
    parts.append("3. 未在人物设定中出现的新角色，不得突然作为重要角色登场。如需引入新角色，必须先铺垫，且不能喧宾夺主。")
    parts.append("4. 每个角色的性格、说话方式、行为必须与其人物设定一致，不得前后矛盾。")
    parts.append(f"5. 除「{protagonist}」外，其他角色不得替代主角推动主线剧情。")

    return "\n".join(parts)


# ── 工作流模板 ──

DEFAULT_WORKFLOW = {
    "name": "自动写作",
    "description": "从立意到审校的全流程",
    "steps": [
        {"id": "ideation", "needs": "立意规划", "prompt": "为一部{genre}题材的{style}风格小说确定核心立意、目标读者、卖点。书名：{title}", "output": "planning/立意.md"},
        {"id": "outline", "needs": "故事结构", "prompt": "根据立意设计完整的故事大纲，包括主要人物、世界观、主线冲突。", "input": ["planning/立意.md"], "output": "planning/大纲.md"},
        {"id": "characters", "needs": "人物设定", "prompt": "根据大纲设计主要人物档案（性格、背景、成长弧线、关系）。", "input": ["planning/大纲.md"], "output": "planning/人物设定.md"},
        {"id": "world", "needs": "世界观构建", "prompt": "根据大纲构建详细的世界观设定。", "input": ["planning/大纲.md"], "output": "planning/世界观.md"},
        {"id": "timeline", "needs": "故事结构", "prompt": "根据大纲设计故事时间线。", "input": ["planning/大纲.md"], "output": "planning/时间线.md"},
        {"id": "main_plot", "needs": "故事结构", "prompt": "梳理主线剧情脉络，标注关键转折点。", "input": ["planning/大纲.md"], "output": "planning/主线.md"},
        {"id": "sub_plot", "needs": "故事结构", "prompt": "梳理支线剧情，说明与主线的交汇点。", "input": ["planning/大纲.md"], "output": "planning/支线.md"},
        {"id": "foreshadow", "needs": "伏笔设计", "prompt": "设计伏笔清单：伏笔内容、埋设章节、回收章节。", "input": ["planning/大纲.md"], "output": "planning/伏笔.md"},
        {"id": "fix_titles", "needs": "", "prompt": "", "output": "", "optional": True},
        {"id": "inspiration", "needs": "灵感激发", "prompt": "基于当前剧情进展，提供3个意想不到的转折方向，为下一章提供创作灵感。", "input": ["prev_chapters"], "output": "inspiration/{n}_灵感.md", "every": 3, "optional": True},
        {"id": "sim", "needs": "角色推演", "prompt": "根据人物设定和大纲，推演第{n}章中各角色在当前冲突下的自然反应。输出JSON格式的推演结果。", "input": ["planning/大纲.md", "planning/人物设定.md", "prev_chapters"], "output": "sim_cache/sim_{n}.md", "every": 1, "optional": True},
        {"id": "chapter", "needs": "正文写作", "prompt": "根据大纲和前文写第{n}章正文。严格遵守【写作约束·角色锚定】中的规则：主角不得更换，角色名不得擅改，新人物不得无铺垫登场。参考【灵感】和【角色推演】来推进剧情。保持与前文连贯。", "input": ["planning/大纲.md", "planning/人物设定.md", "prev_chapters"], "output": "chapters/{n}_chapter.txt"},
        {"id": "chapter_summary", "needs": "", "prompt": "", "output": "", "every": 1, "optional": True},
        {"id": "toc", "needs": "", "prompt": "", "output": "", "every": 1, "optional": True},
        {"id": "polish", "needs": "润色", "prompt": "润色第{n}章正文，提升文笔质量、场景描写、对话自然度和情感表达。保持原有风格，只做锦上添花。", "input": ["chapters/{n}_chapter.txt"], "output": "chapters/{n}_chapter.txt", "every": 2, "optional": True},
        {"id": "proofread", "needs": "错别字检查", "prompt": "校对第{n}章的错别字、语法、标点。", "input": ["chapters/{n}_chapter.txt"], "output": "review/校对报告.md", "every": 1, "optional": True},
        {"id": "summary", "needs": "剧情摘要", "prompt": "将前{n}章的剧情浓缩为一份结构化摘要。格式要求：\n## 剧情摘要\n（150字内，只写关键转折）\n## 角色状态\n- 角色名: 当前状态/位置/实力\n## 伏笔\n- [埋设] 伏笔描述（第X章）\n- [回收] 伏笔描述（第X章）\n## 未解悬念\n- 悬念描述\n## 承接点\n（30字内，下一章应从哪里接）", "input": ["prev_chapters", "planning/人物设定.md"], "output": "summary/第1-{n}章摘要.md", "every": 5, "optional": True},
        {"id": "update_outline", "needs": "故事结构", "prompt": "根据前{n}章的实际内容，更新故事大纲。保留原有结构，补充实际发生的剧情走向、新增的支线、调整后的节奏。输出完整的新大纲。", "input": ["planning/大纲.md", "prev_chapters"], "output": "planning/大纲.md", "every": 10, "optional": True, "update_planning": True},
        {"id": "update_characters", "needs": "人物设定", "prompt": "根据前{n}章的实际内容，更新人物设定。补充角色的实际成长变化、新增的关系、性格变化。保留原有设定，在末尾追加【进展更新】章节。", "input": ["planning/人物设定.md", "prev_chapters"], "output": "planning/人物设定.md", "every": 10, "optional": True, "update_planning": True},
        {"id": "update_foreshadow", "needs": "伏笔设计", "prompt": "根据前{n}章的实际内容，更新伏笔清单。标注哪些伏笔已回收、哪些仍悬而未决、新增了哪些伏笔。保留原有清单，在末尾追加【进展更新】。", "input": ["planning/伏笔.md", "prev_chapters"], "output": "planning/伏笔.md", "every": 10, "optional": True, "update_planning": True},
        {"id": "review", "needs": "剧情审查", "prompt": "审查全部章节的剧情逻辑、人物一致性、节奏，给出修改建议。", "input": ["planning/*", "chapters/*.txt"], "output": "review/审核报告.md"},
    ],
}


# ── 续写工作流 ──

CONTINUE_WORKFLOW = {
    "name": "续写",
    "description": "从已有章节继续写作",
    "steps": [
        {"id": "fix_titles", "needs": "", "prompt": "", "output": "", "optional": True},
        {"id": "inspiration", "needs": "灵感激发", "prompt": "基于当前剧情进展，提供3个意想不到的转折方向，为下一章提供创作灵感。", "input": ["prev_chapters"], "output": "inspiration/{n}_灵感.md", "every": 3, "optional": True},
        {"id": "sim", "needs": "角色推演", "prompt": "根据人物设定和大纲，推演第{n}章中各角色在当前冲突下的自然反应。输出JSON格式的推演结果。", "input": ["planning/大纲.md", "planning/人物设定.md", "prev_chapters"], "output": "sim_cache/sim_{n}.md", "every": 1, "optional": True},
        {"id": "chapter", "needs": "正文写作", "prompt": "根据大纲和前文写第{n}章正文。严格遵守【写作约束·角色锚定】中的规则：主角不得更换，角色名不得擅改，新人物不得无铺垫登场。参考【灵感】和【角色推演】来推进剧情。保持与前文连贯。", "input": ["planning/大纲.md", "planning/人物设定.md", "prev_chapters"], "output": "chapters/{n}_chapter.txt"},
        {"id": "chapter_summary", "needs": "", "prompt": "", "output": "", "every": 1, "optional": True},
        {"id": "toc", "needs": "", "prompt": "", "output": "", "every": 1, "optional": True},
        {"id": "polish", "needs": "润色", "prompt": "润色第{n}章正文，提升文笔质量、场景描写、对话自然度和情感表达。保持原有风格，只做锦上添花。", "input": ["chapters/{n}_chapter.txt"], "output": "chapters/{n}_chapter.txt", "every": 2, "optional": True},
        {"id": "proofread", "needs": "错别字检查", "prompt": "校对第{n}章的错别字、语法、标点。", "input": ["chapters/{n}_chapter.txt"], "output": "review/校对报告.md", "every": 1, "optional": True},
        {"id": "summary", "needs": "剧情摘要", "prompt": "将前{n}章的剧情浓缩为一份结构化摘要。格式要求：\n## 剧情摘要\n（150字内，只写关键转折）\n## 角色状态\n- 角色名: 当前状态/位置/实力\n## 伏笔\n- [埋设] 伏笔描述（第X章）\n- [回收] 伏笔描述（第X章）\n## 未解悬念\n- 悬念描述\n## 承接点\n（30字内，下一章应从哪里接）", "input": ["prev_chapters", "planning/人物设定.md"], "output": "summary/第1-{n}章摘要.md", "every": 5, "optional": True},
        {"id": "update_outline", "needs": "故事结构", "prompt": "根据前{n}章的实际内容，更新故事大纲。保留原有结构，补充实际发生的剧情走向、新增的支线、调整后的节奏。输出完整的新大纲。", "input": ["planning/大纲.md", "prev_chapters"], "output": "planning/大纲.md", "every": 10, "optional": True, "update_planning": True},
        {"id": "update_characters", "needs": "人物设定", "prompt": "根据前{n}章的实际内容，更新人物设定。补充角色的实际成长变化、新增的关系、性格变化。保留原有设定，在末尾追加【进展更新】章节。", "input": ["planning/人物设定.md", "prev_chapters"], "output": "planning/人物设定.md", "every": 10, "optional": True, "update_planning": True},
        {"id": "update_foreshadow", "needs": "伏笔设计", "prompt": "根据前{n}章的实际内容，更新伏笔清单。标注哪些伏笔已回收、哪些仍悬而未决、新增了哪些伏笔。保留原有清单，在末尾追加【进展更新】。", "input": ["planning/伏笔.md", "prev_chapters"], "output": "planning/伏笔.md", "every": 10, "optional": True, "update_planning": True},
        {"id": "review", "needs": "剧情审查", "prompt": "审查全部章节的剧情逻辑、人物一致性、节奏，给出修改建议。", "input": ["planning/*", "chapters/*.txt"], "output": "review/审核报告.md"},
    ],
}


# ── 校验工作流 ──

VALIDATE_WORKFLOW = {
    "name": "校验",
    "description": "审核+校对已有章节",
    "steps": [
        {"id": "fix_titles", "needs": "", "prompt": "", "output": "", "optional": True},
        {"id": "toc", "needs": "", "prompt": "", "output": "", "optional": True},
        {"id": "review", "needs": "剧情审查", "prompt": "审查全部章节的剧情逻辑、人物一致性、节奏，给出修改建议。", "input": ["planning/*", "chapters/*.txt"], "output": "review/审核报告.md"},
        {"id": "proofread", "needs": "错别字检查", "prompt": "校对全部章节的错别字、语法、标点。", "input": ["chapters/*.txt"], "output": "review/校对报告.md"},
    ],
}


def build_workflow(
    mode: WorkflowMode,
    project_info: dict,
    start_chapter: int = 1,
    end_chapter: int = 20,
) -> WorkflowDef:
    """根据模式构建工作流定义。

    Args:
        mode: 工作流模式
        project_info: 项目信息
        start_chapter: 起始章节号（续写模式用）
        end_chapter: 结束章节号
    """
    if mode == WorkflowMode.NEW_BOOK:
        data = json.loads(json.dumps(DEFAULT_WORKFLOW))
    elif mode == WorkflowMode.CONTINUE:
        data = json.loads(json.dumps(CONTINUE_WORKFLOW))
    elif mode == WorkflowMode.VALIDATE:
        data = json.loads(json.dumps(VALIDATE_WORKFLOW))
    elif mode == WorkflowMode.FILL_GAPS:
        # 查漏补缺：扫描缺失或空章节，生成写作步骤
        from . import project_io as _pio
        chapters_dir = _pio.PLANNING_DIR  # just to import
        existing = {}
        if project_info.get("_project_dir"):
            from pathlib import Path
            ch_dir = Path(project_info["_project_dir"]) / _pio.CHAPTERS_DIR
            if ch_dir.exists():
                for f in ch_dir.iterdir():
                    m = _pio._CHAPTER_RE.match(f.name)
                    if m:
                        num = int(m.group(1))
                        # 空文件也算缺失
                        existing[num] = f.stat().st_size > 0
        # 找出缺失或空的章节
        gap_steps = []
        for n in range(start_chapter, end_chapter + 1):
            if n not in existing or not existing[n]:
                gap_steps.append(n)
        data = {"name": "查漏补缺", "description": f"补写 {len(gap_steps)} 个缺失章节", "steps": [
            {"id": "chapter", "needs": "正文写作",
             "prompt": "根据大纲和前文写第{n}章正文。严格遵守【写作约束·角色锚定】中的规则：主角不得更换，角色名不得擅改，新人物不得无铺垫登场。保持与前文连贯。",
             "input": ["planning/大纲.md", "planning/人物设定.md", "prev_chapters"],
             "output": "chapters/{n}_chapter.txt", "repeat": end_chapter},
        ]}
        # 标记缺失章节
        data["_gaps"] = gap_steps
    else:
        data = json.loads(json.dumps(DEFAULT_WORKFLOW))

    # 设置项目信息
    data["project"] = {
        "title": project_info.get("title", ""),
        "genre": project_info.get("genre", ""),
        "style": project_info.get("style", ""),
        "target_chapters": end_chapter,
    }
    # 查漏补缺模式：记录缺失章节
    if mode == WorkflowMode.FILL_GAPS and "_gaps" in data:
        data["project"]["_gaps"] = data.pop("_gaps")

    # 为需要循环的步骤设置 repeat
    total = end_chapter - start_chapter + 1
    for step in data.get("steps", []):
        if step.get("id") == "chapter":
            step["repeat"] = end_chapter
            # 续写模式：prompt 中提示从第几章开始
            if mode == WorkflowMode.CONTINUE:
                step["prompt"] = f"根据大纲和前文写第{{n}}章正文。这是第{start_chapter}章到第{end_chapter}章的续写部分。严格遵守【写作约束·角色锚定】中的规则：主角不得更换，角色名不得擅改，新人物不得无铺垫登场。保持与前文连贯。"

    wf = WorkflowDef.from_dict(data)
    # 续写模式：跳过已完成的章节
    if mode == WorkflowMode.CONTINUE:
        wf.project["_start_chapter"] = start_chapter
    logger.info("构建工作流: mode=%s, steps=%d, chapters=%d-%d", mode.value, len(wf.steps), start_chapter, end_chapter)
    return wf
