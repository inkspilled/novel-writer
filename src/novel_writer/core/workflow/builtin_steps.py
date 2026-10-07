"""内置特殊步骤 — 不走标准 LLM 步骤流程的工具/结构化步骤。

每个处理器签名统一为 (runner, step, n)，自行负责回调触发与异常处理，
runner 通过注册表分发，替代原先的 if/elif 长链。
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Awaitable, Callable

from .. import project_io
from ..logger import get_logger
from ..quality_checker import QualityChecker
from ..world_state import WorldState
from .constants import SKILL_PROOFREAD, SKILL_REVIEW, SKILL_TYPO_CHECK
from .prompts import (
    build_chapter_summary_prompt,
    build_fix_titles_prompt,
    build_world_state_prompt,
)

if TYPE_CHECKING:
    from .runner import WorkflowRunner
    from .definition import WorkflowStep

logger = get_logger(__name__)

# 内置步骤处理器注册表：step_id -> handler(runner, step, n)
BUILTIN_STEP_HANDLERS: dict[str, Callable[..., Awaitable[None]]] = {}


def handler(step_id: str):
    """注册内置步骤处理器。"""
    def decorator(fn):
        BUILTIN_STEP_HANDLERS[step_id] = fn
        return fn
    return decorator


def _fallback_agent(runner: "WorkflowRunner", skills: tuple[str, ...]):
    """按技能优先级找执行 Agent，都找不到时退回第一个 Agent。"""
    for skill in skills:
        agent = runner.find_agent(skill)
        if agent:
            return agent
    return list(runner.agents.values())[0] if runner.agents else None


@handler("toc")
async def _run_toc(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    project_io.generate_toc(runner.project_dir)
    logger.info("目录已更新")
    if runner.on_step_end:
        runner.on_step_end(step.id, n, "系统", "目录已更新")


@handler("fix_titles")
async def _run_fix_titles(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    agent = _fallback_agent(runner, (SKILL_REVIEW, SKILL_TYPO_CHECK))
    if not agent:
        logger.warning("标题校准跳过：没有可用的智能体")
        return
    if runner.on_step_start:
        runner.on_step_start(step.id, n, agent.title)
    logger.info("执行标题校准（LLM）...")
    chapters = project_io.scan_chapters(runner.project_dir)
    if not chapters:
        return
    # 构建上下文：每章前 500 字
    excerpts = []
    for ch in chapters:
        content = project_io.read_md(ch["content_path"])
        excerpt = content[:500].replace("\n", " ")
        excerpts.append(f"第{ch['number']}章（当前标题：「{ch['title']}」）：\n{excerpt}")
    prompt = build_fix_titles_prompt(excerpts)
    try:
        resp = await agent.run(prompt)
        # 提取 JSON（兼容 ```json ``` 包裹）
        text = resp.content.strip()
        m = re.search(r'\{[^{}]+\}', text)
        if m:
            title_map = json.loads(m.group())
            logs = []
            for ch_num_str, new_title in title_map.items():
                ch_num = int(ch_num_str)
                new_title_str = str(new_title).strip()
                if not new_title_str:
                    logs.append(f"第{ch_num}章: 标题为空，跳过")
                    continue
                old, err = project_io.safe_rename_chapter(runner.project_dir, ch_num, new_title_str)
                if err:
                    logs.append(f"第{ch_num}章: {err}")
                elif old:
                    logs.append(f"第{ch_num}章: 「{old}」→「{new_title_str}」")
            # 刷新目录
            project_io.generate_toc(runner.project_dir)
            # 同步更新 meta.json 中的章节标题
            _update_meta_chapter_titles(runner.project_dir, title_map)
            msg = f"标题校准完成，修正 {len(logs)} 处"
            logger.info(msg)
            if runner.on_step_end:
                runner.on_step_end(step.id, n, agent.title, msg + ("\n" + "\n".join(logs)))
        else:
            logger.warning("标题校准：LLM 返回格式异常: %s", text[:200])
            if runner.on_step_end:
                runner.on_step_end(step.id, n, agent.title, "标题校准失败：LLM 返回格式异常")
    except Exception as e:
        logger.error("标题校准失败: %s", e, exc_info=True)
        if runner.on_error:
            runner.on_error(step.id, str(e))


@handler("world_state_update")
async def _run_world_state_update(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    existing = project_io.find_chapter_file(runner.project_dir, n)
    if not existing:
        return
    existing_path = runner.project_dir / "chapters" / existing
    if not existing_path.exists() or existing_path.stat().st_size == 0:
        return
    agent = _fallback_agent(runner, (SKILL_REVIEW, SKILL_PROOFREAD))
    if not agent:
        return
    if runner.on_step_start:
        runner.on_step_start(step.id, n, agent.title)
    gs = WorldState(runner.project_dir)
    gs.load()
    content = project_io.read_md(existing_path)
    gs_summary = gs.build_context_text()
    prompt = build_world_state_prompt(n, gs_summary, content)
    try:
        resp = await agent.run(prompt)
        text = resp.content.strip()
        m = re.search(r'\{[\s\S]+\}', text)
        if m:
            update = json.loads(m.group())
            # 注入章节号到 timeline
            for ev in update.get("timeline", []):
                ev["chapter"] = n
            logs = gs.apply_llm_update(update)
            gs.save()
            msg = f"大世界状态已更新: {', '.join(logs)}"
            logger.info(msg)
            if runner.on_step_end:
                runner.on_step_end(step.id, n, agent.title, msg)
        else:
            logger.warning("大世界状态更新：LLM 返回格式异常")
    except Exception as e:
        logger.error("大世界状态更新失败: %s", e)


@handler("chapter_summary")
async def _run_chapter_summary(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    # 找到第 n 章文件
    existing = project_io.find_chapter_file(runner.project_dir, n)
    if not existing:
        return
    existing_path = runner.project_dir / "chapters" / existing
    if not existing_path.exists() or existing_path.stat().st_size == 0:
        return
    # 检查概要是否已存在
    m = project_io._CHAPTER_RE.match(existing)
    if not m:
        return
    ch_title = m.group(2)
    summary_path = project_io.chapter_summary_path(runner.project_dir, n, ch_title)
    if summary_path.exists() and summary_path.stat().st_size > 0:
        logger.debug("第%d章概要已存在，跳过", n)
        return
    agent = _fallback_agent(runner, (SKILL_REVIEW, SKILL_PROOFREAD))
    if not agent:
        return
    if runner.on_step_start:
        runner.on_step_start(step.id, n, agent.title)
    content = project_io.read_md(existing_path)
    prompt = build_chapter_summary_prompt(n, ch_title, content)
    try:
        resp = await agent.run(prompt)
        project_io.write_md(summary_path, resp.content)
        logger.info("第%d章概要已生成", n)
        if runner.on_step_end:
            runner.on_step_end(step.id, n, agent.title, f"第{n}章概要已生成")
    except Exception as e:
        logger.error("第%d章概要生成失败: %s", n, e)


@handler("quality_check")
async def _run_quality_check(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    existing = project_io.find_chapter_file(runner.project_dir, n)
    if not existing:
        return
    existing_path = runner.project_dir / "chapters" / existing
    if not existing_path.exists() or existing_path.stat().st_size == 0:
        return
    if runner.on_step_start:
        runner.on_step_start(step.id, n, "系统")
    content = project_io.read_md(existing_path)
    try:
        checker = QualityChecker(runner.project_dir)
        report = checker.check_chapter(content, n)
        report_text = checker.generate_report_text(report)
        # 保存报告
        report_path = runner.project_dir / "review" / f"质量检查_第{n}章.md"
        project_io.write_md(report_path, report_text)
        msg = f"质量检查完成: {report.total_score:.1f}分"
        logger.info(msg)
        if runner.on_step_end:
            runner.on_step_end(step.id, n, "系统", msg)
    except Exception as e:
        logger.error("质量检查失败: %s", e)
        if runner.on_error:
            runner.on_error(step.id, str(e))


def _update_meta_chapter_titles(project_dir, title_map: dict):
    """同步更新 meta.json 中的章节标题。"""
    meta_path = project_dir / "meta.json"
    if not meta_path.exists():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        chapter_meta = meta.get("chapter_meta", [])
        for ch_num_str, new_title in title_map.items():
            ch_num = int(ch_num_str)
            new_title_str = str(new_title).strip()
            if not new_title_str:
                continue
            # 查找对应章节并更新标题
            for item in chapter_meta:
                if item.get("number") == ch_num:
                    item["title"] = new_title_str
                    break
        meta["chapter_meta"] = chapter_meta
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("meta.json 章节标题已同步更新")
    except Exception as e:
        logger.error("更新 meta.json 失败: %s", e)
