"""内置特殊步骤 — 不走标准 LLM 步骤流程的工具/结构化步骤。

每个处理器签名统一为 (runner, step, n)，自行负责回调触发与异常处理，
runner 通过注册表分发，替代原先的 if/elif 长链。
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Awaitable, Callable

from .. import project_io
from ..json_extract import extract_json_object
from ..logger import get_logger
from ..quality_checker import QualityChecker
from ..world_state import WorldState
from .constants import SKILL_PROOFREAD, SKILL_REVIEW, SKILL_TYPO_CHECK
from .context import estimate_tokens
from .prompts import (
    build_chapter_summary_prompt,
    build_fix_titles_prompt,
    build_proofread_batch_prompt,
    build_review_aggregate_prompt,
    build_review_batch_prompt,
    build_world_state_prompt,
)

if TYPE_CHECKING:
    from .runner import WorkflowRunner
    from .definition import WorkflowStep

logger = get_logger(__name__)

# 内置步骤处理器注册表：step_id -> handler(runner, step, n)
BUILTIN_STEP_HANDLERS: dict[str, Callable[..., Awaitable[None]]] = {}

# 分批审核/校对的每批 token 预算与单章截断上限
_BATCH_TOKEN_BUDGET = 60_000
_PROOFREAD_CHAPTER_CAP = 8_000   # 校对看文字层面，单章截断到 8K 字符
_REVIEW_CHAPTER_CAP = 10_000     # 审核看剧情逻辑，单章截断到 10K 字符


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


def _pack_batches(items: list[tuple[int, str]], budget: int = _BATCH_TOKEN_BUDGET) -> list[list[tuple[int, str]]]:
    """按 token 预算把 (章节号, 正文) 列表打包成批。"""
    batches: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    current_tokens = 0
    for num, text in items:
        t = estimate_tokens(text)
        if current and current_tokens + t > budget:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append((num, text))
        current_tokens += t
    if current:
        batches.append(current)
    return batches


def _scan_nonempty_chapters(runner: "WorkflowRunner") -> list[dict]:
    """扫描有内容的章节，按章节号排序。"""
    chapters = [c for c in project_io.scan_chapters(runner.project_dir)
                if c["content_path"].exists() and c["content_path"].stat().st_size > 0]
    chapters.sort(key=lambda c: c["number"])
    return chapters


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
        resp = await agent.run(prompt, thinking="off", keep_history=False)
        # 稳健提取 JSON（兼容 ```json``` 包裹 / 思考噪声 / 嵌套对象）
        text = resp.content.strip()
        title_map = extract_json_object(text)
        if title_map:
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
        resp = await agent.run(prompt, thinking="off", keep_history=False)
        text = resp.content.strip()
        update = extract_json_object(text)
        if update:
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
        resp = await agent.run(prompt, thinking="off", keep_history=False)
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


@handler("proofread")
async def _run_proofread(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    """校对：输出含 {n} 为逐章模式，否则为全书分批校对（VALIDATE 模式）。

    逐章模式复刻原标准路径行为；全书模式修复「校对全部章节实际只校对第 1 章」。
    """
    if "{n}" in step.output:
        await _proofread_single_chapter(runner, step, n)
    else:
        await _proofread_all_chapters(runner, step)


def _proofread_agent(runner: "WorkflowRunner", step: "WorkflowStep"):
    agent = runner.find_agent(step.needs) if step.needs else None
    return agent or _fallback_agent(runner, (SKILL_TYPO_CHECK, SKILL_PROOFREAD))


async def _proofread_single_chapter(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    existing = project_io.find_chapter_file(runner.project_dir, n)
    if not existing:
        return
    chapter_path = runner.project_dir / "chapters" / existing
    if not chapter_path.exists() or chapter_path.stat().st_size == 0:
        return
    agent = _proofread_agent(runner, step)
    if not agent:
        return
    output = runner._format_prompt(step.output, runner.project_info, n)
    out_path = runner.project_dir / output
    if out_path.exists() and out_path.stat().st_size > 0:
        logger.debug("第%d章校对报告已存在，跳过", n)
        return
    if runner.on_step_start:
        runner.on_step_start(step.id, n, agent.title)
    content = project_io.read_md(chapter_path)
    prompt = f"校对第{n}章的错别字、语法、标点。列出发现的问题和修改建议。\n\n正文：\n{content}"
    try:
        resp = await agent.run(prompt, thinking="off", keep_history=False)
        if not resp.content.strip():
            logger.warning("第%d章校对返回空内容，跳过写入", n)
            return
        project_io.write_md(out_path, resp.content)
        logger.info("第%d章校对完成", n)
        if runner.on_step_end:
            runner.on_step_end(step.id, n, agent.title, f"第{n}章校对完成")
    except Exception as e:
        logger.error("第%d章校对失败: %s", n, e, exc_info=True)
        raise


async def _proofread_all_chapters(runner: "WorkflowRunner", step: "WorkflowStep"):
    agent = _proofread_agent(runner, step)
    if not agent:
        if step.optional:
            return
        raise RuntimeError(f"没有智能体承担步骤 {step.id}（{step.needs}）")
    chapters = _scan_nonempty_chapters(runner)
    if not chapters:
        return
    out_path = runner.project_dir / step.output
    if out_path.exists() and out_path.stat().st_size > 0:
        logger.info("全书校对报告已存在，跳过（删除 %s 后可重新生成）", out_path.name)
        return
    last_n = max(c["number"] for c in chapters)
    if runner.on_step_start:
        runner.on_step_start(step.id, last_n, agent.title)
    items = [(c["number"], project_io.read_md(c["content_path"])[:_PROOFREAD_CHAPTER_CAP])
             for c in chapters]
    batches = _pack_batches(items)
    findings: list[str] = []
    for i, batch in enumerate(batches, 1):
        if runner._stop:
            break
        agent.clear_history()  # 各批独立，避免批间历史累积
        prompt = build_proofread_batch_prompt(batch, i, len(batches))
        resp = await agent.run(prompt, thinking="off", keep_history=False)
        if resp.content.strip():
            findings.append(
                f"### 第 {i}/{len(batches)} 批（第{batch[0][0]}-{batch[-1][0]}章）\n\n{resp.content.strip()}"
            )
        logger.info("全书校对进度: 批 %d/%d", i, len(batches))
    report = "\n\n".join(findings) if findings else "未发现错别字、语法或标点问题。"
    project_io.write_md(out_path, f"## 全书校对报告\n\n{report}")
    msg = f"全书校对完成（{len(batches)} 批 / {len(chapters)} 章）"
    logger.info(msg)
    if runner.on_step_end:
        runner.on_step_end(step.id, last_n, agent.title, msg)


@handler("review")
async def _run_review(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    """全书审核：分批审查全部章节，最后聚合为一份审核报告。

    修复原标准路径下 chapters/*.txt 走 RAG 只回填 top5×300 字片段、
    长书审核近乎失明的问题。
    """
    agent = runner.find_agent(step.needs) if step.needs else None
    agent = agent or _fallback_agent(runner, (SKILL_REVIEW,))
    if not agent:
        if step.optional:
            return
        raise RuntimeError(f"没有智能体承担步骤 {step.id}（{step.needs}）")
    chapters = _scan_nonempty_chapters(runner)
    if not chapters:
        return
    output = runner._format_prompt(step.output, runner.project_info, n)
    out_path = runner.project_dir / output
    if out_path.exists() and out_path.stat().st_size > 0:
        logger.info("审核报告已存在，跳过（删除 %s 后可重新生成）", out_path.name)
        return
    if runner.on_step_start:
        runner.on_step_start(step.id, n, agent.title)

    # 规划参考：各规划文档取开头摘要
    planning_dir = runner.project_dir / "planning"
    planning_parts: list[str] = []
    if planning_dir.exists():
        for p in sorted(planning_dir.glob("*.md")):
            text = project_io.read_md(p)
            if text:
                planning_parts.append(f"【{p.stem}】{text[:400]}")
    planning_summary = "\n".join(planning_parts)[:4000]

    items = [(c["number"], project_io.read_md(c["content_path"])[:_REVIEW_CHAPTER_CAP])
             for c in chapters]
    batches = _pack_batches(items)
    findings: list[str] = []
    for i, batch in enumerate(batches, 1):
        if runner._stop:
            break
        agent.clear_history()
        prompt = build_review_batch_prompt(batch, planning_summary, i, len(batches))
        resp = await agent.run(prompt, thinking="auto", keep_history=False)
        if resp.content.strip():
            findings.append(resp.content.strip())
        logger.info("全书审核进度: 批 %d/%d", i, len(batches))

    if not findings:
        logger.warning("全书审核：所有批次均无发现")
        return
    if runner._stop:
        return

    # 聚合各批发现为最终报告
    agent.clear_history()
    aggregate_prompt = build_review_aggregate_prompt(findings, len(chapters))
    try:
        resp = await agent.run(aggregate_prompt, thinking="auto", keep_history=False)
        report = resp.content.strip() or "\n\n".join(findings)
    except Exception as e:
        logger.warning("审核汇总调用失败，直接落盘各批发现: %s", e)
        report = "\n\n".join(findings)
    project_io.write_md(out_path, report)
    msg = f"全书审核完成（{len(batches)} 批 / {len(chapters)} 章）"
    logger.info(msg)
    if runner.on_step_end:
        runner.on_step_end(step.id, n, agent.title, msg)


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
