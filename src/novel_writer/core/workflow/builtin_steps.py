"""内置特殊步骤 — 不走标准 LLM 步骤流程的工具/结构化步骤。

每个处理器签名统一为 (runner, step, n)，自行负责回调触发与异常处理，
runner 通过注册表分发，替代原先的 if/elif 长链。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Awaitable, Callable

from .. import project_io
from ..agents.base import AgentConfig, BaseAgent
from ..canon.events import CanonEvent, CanonLog
from ..canon.extract import extract_percepts
from ..canon.seal import ChapterSealedError, seal_chapter
from ..characters.offscreen import run_offscreen_tick
from ..characters.panel import run_character_panel
from ..characters.registry import CharacterRegistry
from ..character_sim import parse_characters
from ..characters.view import (
    CharacterViewBuilder,
    build_character_system_prompt,
    validate_knowledge_used,
)
from ..json_extract import extract_json_object
from ..text_clean import clean_model_text
from ..logger import get_logger
from ..quality_checker import QualityChecker
from ..world_state import WorldState
from .constants import SKILL_PROOFREAD, SKILL_REVIEW, SKILL_TYPO_CHECK
from .context import estimate_tokens, extract_chapter_section
from .prompts import (
    CHAPTER_OUTLINE_BATCH,
    OUTLINE_REVISE_WINDOW,
    REVIEW_PANEL_LENSES,
    build_chapter_outline_batch_prompt,
    build_chapter_outline_revise_prompt,
    build_chapter_summary_prompt,
    build_fix_titles_prompt,
    build_proofread_batch_prompt,
    build_review_aggregate_prompt,
    build_review_batch_prompt,
    build_review_panel_aggregate_prompt,
    build_review_panel_prompt,
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
            compact = gs.compact_update(update, chapter=n)
            if not compact:
                logger.info("第%d章世界状态无实际变化，跳过正典写入", n)
                if runner.on_step_end:
                    runner.on_step_end(step.id, n, agent.title, "世界状态无变化")
                return
            update = compact
            # 先入正典（可重放），再更新快照
            try:
                CanonLog(runner.project_dir).append(CanonEvent(
                    chapter=n, type="world_state_update", actor="system",
                    visibility="world", payload={"update": update, "source": "world_state_update"},
                ))
            except Exception as e:
                logger.warning("世界状态事件入正典失败（快照仍更新）: %s", e)
            logs = gs.apply_llm_update(update, chapter=n)
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
        project_io.write_md(summary_path, clean_model_text(resp.content, "chapter_summary"))
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
        project_io.write_md(out_path, clean_model_text(resp.content, "proofread"))
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
        resp = await agent.run(prompt, thinking="off", keep_history=False)
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
        resp = await agent.run(aggregate_prompt, thinking="off", keep_history=False)
        report = resp.content.strip() or "\n\n".join(findings)
    except Exception as e:
        logger.warning("审核汇总调用失败，直接落盘各批发现: %s", e)
        report = "\n\n".join(findings)
    project_io.write_md(out_path, clean_model_text(report, "review"))
    # 审稿 → 反模式禁忌库（原先在 runner 标准路径里，review 改内置后成了死代码）
    try:
        from ..anti_patterns import AntiPatternTracker
        last_n = max(c["number"] for c in chapters)
        AntiPatternTracker(runner.project_dir).add_from_review(report, last_n)
    except Exception as e:
        logger.warning("审核报告反模式入库失败（忽略）: %s", e)
    msg = f"全书审核完成（{len(batches)} 批 / {len(chapters)} 章）"
    logger.info(msg)
    if runner.on_step_end:
        runner.on_step_end(step.id, n, agent.title, msg)


# ── 多审稿人并行会审（fan-out / fan-in） ──

_REVIEW_PANEL_CHAPTERS = 3
_REVIEW_PANEL_CHAPTER_CAP = 6000


@handler("review_panel")
async def _run_review_panel(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    """三个视角并行审最近几章，再由主编聚合为一份修改清单。

    对标 DSH workflow 的 `parallel()` + `pipeline()`：子任务互不依赖，
    并行扇出；聚合步骤依赖全部子结果，天然是 fan-in。
    """
    chapters = _scan_nonempty_chapters(runner)
    if not chapters:
        return
    focus = chapters[-_REVIEW_PANEL_CHAPTERS:]
    start_ch, end_ch = focus[0]["number"], focus[-1]["number"]
    chapter_text = "\n\n".join(
        f"### 第{c['number']}章 {c['title']}\n"
        f"{project_io.read_md(c['content_path'])[:_REVIEW_PANEL_CHAPTER_CAP]}"
        for c in focus
    )

    base_agent = runner.find_agent(step.needs) if step.needs else None
    base_agent = base_agent or _fallback_agent(runner, (SKILL_REVIEW, SKILL_TYPO_CHECK))
    if not base_agent:
        logger.info("多视角会审跳过：没有可用智能体")
        return
    if runner.on_step_start:
        runner.on_step_start(step.id, n, base_agent.title)

    # 清一次历史，避免把上一轮审稿内容带进本次三个视角
    base_agent.clear_history()
    tasks = []
    for lens, focus_text in REVIEW_PANEL_LENSES:
        prompt = build_review_panel_prompt(lens, focus_text, chapter_text, end_ch)
        tasks.append(base_agent.run(prompt, thinking="off", keep_history=False))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    findings: list[str] = []
    for (lens, _), result in zip(REVIEW_PANEL_LENSES, results):
        if isinstance(result, Exception):
            logger.warning("会审视角 %s 失败（跳过）: %s", lens, result)
            continue
        text = (result.content or "").strip()
        if text:
            findings.append(f"## 【{lens}】\n{text}")
    if not findings:
        logger.warning("多视角会审：所有视角均无有效输出")
        return

    # fan-in：主编聚合
    editor = runner.find_agent("立意规划") or base_agent
    editor.clear_history()
    aggregate_prompt = build_review_panel_aggregate_prompt(findings, start_ch, end_ch)
    try:
        resp = await editor.run(aggregate_prompt, thinking="off", keep_history=False)
        report = (resp.content or "").strip() or "\n\n".join(findings)
    except Exception as e:
        logger.warning("会审聚合失败，直接落盘分视角发现: %s", e)
        report = "\n\n".join(findings)

    out_path = runner.project_dir / "review" / f"会审报告_第{start_ch}-{end_ch}章.md"
    header = f"# 多视角会审 · 第{start_ch}–{end_ch}章\n\n"
    project_io.write_md(out_path, header + report)
    try:
        from ..anti_patterns import AntiPatternTracker
        AntiPatternTracker(runner.project_dir).add_from_review(report, end_ch)
    except Exception as e:
        logger.warning("会审反模式入库失败（忽略）: %s", e)

    msg = f"多视角会审完成：第{start_ch}–{end_ch}章（{len(findings)} 个视角）"
    logger.info(msg)
    if runner.on_step_end:
        runner.on_step_end(step.id, n, base_agent.title, msg)


# ── 章节感知抽取（正文 → 角色可感知的正典事件） ──

_PERCEPT_CHAPTER_CAP = 12000


@handler("percept_extract")
async def _run_percept_extract(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    """把本章最终正文抽成 CanonEvent，供角色 POV 视图使用。

    幂等：同一章已有 source=percept 的事件时跳过；失败不阻断整跑。
    """
    chapter_file = project_io.find_chapter_file(runner.project_dir, n)
    if not chapter_file:
        return
    chapter_path = runner.project_dir / "chapters" / chapter_file
    if not chapter_path.exists() or chapter_path.stat().st_size == 0:
        return
    if CanonLog(runner.project_dir).has_percepts(n):
        logger.debug("第%d章已有感知事件，跳过", n)
        return

    registry = CharacterRegistry(runner.project_dir)
    if not registry.all():
        # 老项目没有角色注册表：先按人物设定建 extra 卡（不会自动 POV 推演）
        char_doc = project_io.read_md(runner.project_dir / "planning" / "人物设定.md")
        if char_doc.strip():
            try:
                registry.seed_from_profiles(parse_characters(char_doc), chapter=n)
            except Exception as e:
                logger.debug("人物设定建卡失败（忽略）: %s", e)
    if not registry.all():
        logger.debug("感知抽取跳过：角色注册表为空")
        return

    agent = runner.find_agent(step.needs) if step.needs else None
    agent = agent or _fallback_agent(runner, (SKILL_REVIEW,))
    if not agent:
        return
    if runner.on_step_start:
        runner.on_step_start(step.id, n, agent.title)
    try:
        events = await extract_percepts(
            runner.project_dir, n, project_io.read_md(chapter_path), agent,
            registry=registry,
        )
        if events:
            touched = False
            for event in events:
                for name in {event.actor, *event.known_by}:
                    if name and registry.get(name):
                        registry.touch(name, n)
                        touched = True
            if touched:
                registry.save()
        msg = f"第{n}章感知事件 {len(events)} 条"
        logger.info(msg)
        if runner.on_step_end:
            runner.on_step_end(step.id, n, agent.title, msg)
    except Exception as e:
        logger.error("第%d章感知抽取失败（不影响正文）: %s", n, e)


@handler("character_panel")
async def _run_character_panel(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    """多角色 POV 提案 + 导演裁决（只对 auto_sim 角色发起调用）。"""
    registry = CharacterRegistry(runner.project_dir)
    if not registry.simulatable():
        return
    agent = runner.find_agent(step.needs) if step.needs else None
    agent = agent or _fallback_agent(runner, (SKILL_REVIEW,))
    if not agent or not getattr(agent, "llm", None):
        return
    if runner.on_step_start:
        runner.on_step_start(step.id, n, agent.title)
    scene_brief = ""
    for name in ("章节细纲.md", "大纲.md"):
        text = project_io.read_md(runner.project_dir / "planning" / name)
        section = extract_chapter_section(text, n) if text else ""
        if section:
            scene_brief = section[:800]
            break
    try:
        panel = await run_character_panel(
            runner.project_dir, n, agent, registry=registry, scene_brief=scene_brief,
        )
    except Exception as e:
        logger.error("角色 panel 失败（不影响正文）: %s", e, exc_info=True)
        return
    if not panel:
        return
    choice = (panel.get("director") or {}).get("choice", "")
    msg = f"角色会审完成：{len(panel.get('proposals', []))} 份提案，导演选择 {choice}"
    logger.info(msg)
    if runner.on_step_end:
        runner.on_step_end(step.id, n, agent.title, msg)


@handler("offscreen_tick")
async def _run_offscreen_tick(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    """每 5 章批量推进离场角色；项目级开关 enable_offscreen_tick。"""
    if not runner.project_info.get("enable_offscreen_tick", True):
        return
    registry = CharacterRegistry(runner.project_dir)
    agent = runner.find_agent(step.needs) if step.needs else None
    agent = agent or _fallback_agent(runner, (SKILL_REVIEW,))
    if not agent or not getattr(agent, "llm", None):
        return
    try:
        data = await run_offscreen_tick(runner.project_dir, n, agent, registry=registry)
    except Exception as e:
        logger.error("离场 tick 失败（不影响正文）: %s", e, exc_info=True)
        return
    if data and runner.on_step_end:
        runner.on_step_end(step.id, n, agent.title, f"离场 tick：{len(data['ticks'])} 人")


@handler("seal_chapter")
async def _run_seal_chapter(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    """可选：章节定稿后写入不可变封印（project_info.seal_chapters）。"""
    if not runner.project_info.get("seal_chapters"):
        return
    try:
        event = seal_chapter(runner.project_dir, n)
        if event and runner.on_step_end:
            runner.on_step_end(step.id, n, "系统", f"第{n}章已封印")
    except ChapterSealedError as e:
        logger.warning("第%d章封印冲突（保留旧封印）: %s", n, e)


# ── 角色 POV 推演（隔离视图，不读大纲/全局状态） ──

_POV_SIM_MAX_CHARS = 4
_POV_SIM_MAX_TOKENS = 1200


@handler("pov_sim")
async def _run_pov_sim(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    """对已启用 auto_sim 的角色做隔离 POV 推演。

    与 `sim` 的区别：`sim` 是作者侧全知预演；本步骤只给角色自己的
    CharacterView，不读大纲/人物设定/world_state/memory，也不给工具。
    """
    registry = CharacterRegistry(runner.project_dir)
    cast = registry.simulatable(limit=_POV_SIM_MAX_CHARS)
    if not cast:
        logger.debug("POV 推演跳过：没有 auto_sim 角色")
        return

    base_agent = runner.find_agent(step.needs) if step.needs else None
    base_agent = base_agent or _fallback_agent(runner, (SKILL_REVIEW,))
    if not base_agent or not getattr(base_agent, "llm", None):
        logger.warning("POV 推演跳过：没有可用的 LLM")
        return
    if runner.on_step_start:
        runner.on_step_start(step.id, n, base_agent.title)

    builder = CharacterViewBuilder(runner.project_dir, registry=registry)
    written: list[str] = []
    for rec in cast:
        if runner._stop:
            break
        try:
            view = builder.build(rec, n)
            cfg = AgentConfig(
                name=rec.id,
                role="character",
                title=rec.name,
                system_prompt=build_character_system_prompt(rec),
                skills=[],
                temperature=0.8,
                max_tokens=_POV_SIM_MAX_TOKENS,
            )
            char_agent = BaseAgent(cfg, base_agent.llm)
            prompt = (
                f"现在是此刻。你在{rec.location or '当前场景'}。\n"
                "根据你已知的信息，决定你此刻想做什么。只输出 JSON：\n"
                "{\n"
                '  "intent": "你的真实目的（一句话）",\n'
                '  "action": "你准备做的具体动作",\n'
                '  "dialogue": "你要说的话（没有就空字符串）",\n'
                '  "expected_effects": ["你预期会造成的变化"],\n'
                '  "knowledge_used": ["你依据的【你记得的事】方括号里的 ID"],\n'
                '  "confidence": 0.0\n'
                "}"
            )
            resp = await char_agent.run(
                prompt, context=view.text, thinking="off", keep_history=False
            )
            proposal = extract_json_object(resp.content or "")
            if not proposal:
                logger.warning("POV 推演 %s 返回非 JSON，跳过", rec.name)
                continue
            ok, reason = validate_knowledge_used(proposal, view)
            if not ok:
                logger.warning("POV 推演 %s 未通过知识校验（%s），跳过", rec.name, reason)
                continue
            proposal["_character"] = rec.id
            proposal["_character_name"] = rec.name
            proposal["_chapter"] = n
            proposal["_view_event_ids"] = sorted(view.event_ids)
            out = runner.project_dir / "sim_cache" / "pov" / rec.id / f"sim_{n}.json"
            project_io.write_json(out, proposal)
            written.append(rec.name)
            logger.info("POV 推演完成: %s（依据 %d 条感知）", rec.name, len(view.event_ids))
        except Exception as e:
            logger.error("POV 推演失败（%s）: %s", rec.name, e, exc_info=True)

    msg = f"POV 推演完成：{', '.join(written) if written else '无有效提案'}"
    logger.info(msg)
    if runner.on_step_end:
        runner.on_step_end(step.id, n, base_agent.title, msg)


# ── 全书章节细纲 ──

_OUTLINE_DOC = "planning/章节细纲.md"


def _resolve_total_chapters(runner: "WorkflowRunner") -> int:
    """目标总章数。

    优先在运行时重读立意.md（新书立意流程里 ideation 刚写完，分卷章数最准），
    解析不到再退回工作流构建时的 target_chapters。
    """
    try:
        from .definition import _parse_expected_chapters
        parsed = _parse_expected_chapters(runner.project_dir)
        if parsed:
            return parsed
    except Exception:
        pass
    total = int(runner.project_info.get("target_chapters") or 0)
    return total if total > 0 else 100


def _planning_brief(runner: "WorkflowRunner", cap: int = 3500) -> str:
    """取规划文档摘要，供细纲生成对齐主线/伏笔。"""
    parts = []
    for name in ("立意.md", "大纲.md", "主线.md", "支线.md", "伏笔.md"):
        text = project_io.read_md(runner.project_dir / "planning" / name)
        if text:
            parts.append(f"【{name[:-3]}】\n{text[:800]}")
    return "\n\n".join(parts)[:cap]


def _prior_beats_block(text: str, before: int, cap: int = 1500) -> str:
    """截取已生成细纲中第 before 章之前的末尾片段，保证批间衔接。"""
    if not text or before <= 1:
        return ""
    lines = text.split("\n")
    cut = []
    for line in lines:
        m = re.match(r"^##\s*第\s*(\d+)\s*章", line)
        if m and int(m.group(1)) >= before:
            break
        cut.append(line)
    tail = "\n".join(cut).strip()
    return tail[-cap:] if tail else ""


_CHAPTER_HEADING_RE = re.compile(r"^##\s*第\s*(\d+)\s*章")


def parse_outline_sections(text: str) -> tuple[str, dict[int, str]]:
    """把细纲解析为 (前言, {章号: 完整条目})。

    只认 `## 第N章` 二级标题；其余内容归入前言。解析失败返回空映射，
    调用方据此拒绝覆盖，避免把整份细纲写成半截。
    """
    preamble: list[str] = []
    sections: dict[int, str] = {}
    current: int | None = None
    buf: list[str] = []

    def _flush():
        if current is not None:
            block = "\n".join(buf).strip()
            if block:
                sections[current] = block

    for line in text.splitlines():
        m = _CHAPTER_HEADING_RE.match(line)
        if m:
            _flush()
            current = int(m.group(1))
            buf = [line]
        elif current is None:
            preamble.append(line)
        else:
            buf.append(line)
    _flush()
    return "\n".join(preamble).strip(), sections


def clean_outline_text(text: str) -> str:
    """只保留 `## 第N章` 条目，丢弃模型夹带的英文思考/计划/前言。

    模型偶尔会在正文前后输出 "Now I need to plan..." 之类的推理；直接落盘
    会污染细纲，也会让按章切片的结果变得不可预测。
    """
    _, sections = parse_outline_sections(text)
    parts: list[str] = []
    for num in sorted(sections):
        lines = sections[num].splitlines()
        heading = lines[0] if lines else f"## 第{num}章"
        bullets = [
            line for line in lines[1:]
            if line.strip().startswith(("-", "*", "•", "·"))
        ]
        parts.append("\n".join([heading] + bullets))
    return "\n\n".join(parts).strip()


def outline_window(text: str, start: int, end: int) -> str:
    """取出 [start, end] 章区间的细纲文本。"""
    _, sections = parse_outline_sections(text)
    return "\n\n".join(
        sections[n] for n in sorted(sections) if start <= n <= end
    )


def splice_outline(existing: str, start: int, end: int, revision: str) -> str:
    """把修订后的 [start, end] 章拼回原文，保留区间外条目。

    Raises:
        ValueError: 修订结果覆盖不足（少于区间 80%）或原细纲无法解析。
    """
    preamble, old = parse_outline_sections(existing)
    _, new = parse_outline_sections(revision)
    if not old:
        raise ValueError("原细纲无法解析出任何 ## 第N章 条目")
    required = set(range(start, end + 1))
    got = required & set(new)
    need = max(1, int(len(required) * 0.8))
    if len(got) < need:
        raise ValueError(
            f"修订覆盖不足：{len(got)}/{len(required)} 章（至少 {need} 章）"
        )
    merged = dict(old)
    for num in got:
        merged[num] = new[num]
    body = "\n\n".join(merged[n] for n in sorted(merged))
    return (preamble + "\n\n" + body).strip() + "\n" if preamble else body.strip() + "\n"


@handler("chapter_outline")
async def _run_chapter_outline(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    """全书逐章剧情骨架：按批写入 planning/章节细纲.md。"""
    out_path = runner.project_dir / _OUTLINE_DOC
    if out_path.exists() and out_path.stat().st_size > 0 and not step.update_planning:
        logger.info("章节细纲已存在，跳过（删除 %s 后可重新生成）", _OUTLINE_DOC)
        return
    agent = runner.find_agent(step.needs) if step.needs else None
    agent = agent or _fallback_agent(runner, (SKILL_REVIEW,))
    if not agent:
        if step.optional:
            return
        raise RuntimeError(f"没有智能体承担步骤 {step.id}（{step.needs}）")
    # 文末补细纲时按“已写章节数”生成，避免只写了 3 章却硬生成 500 章细纲；
    # 规划模式（还没有正文）仍按目标总章数生成。
    written = len(_scan_nonempty_chapters(runner))
    total = written if written > 0 else _resolve_total_chapters(runner)
    if step.max_chapters:
        total = min(total, step.max_chapters)
    if total < 1:
        return
    if written:
        logger.info("章节细纲按已写 %d 章生成（文末补全）", written)
    else:
        logger.info("章节细纲预生成前 %d 章（后续每 5 章滚动补齐）", total)
    if runner.on_step_start:
        runner.on_step_start(step.id, n, agent.title)

    brief = _planning_brief(runner)
    chunks: list[str] = []
    start = 1
    # 断点续跑：上一轮被停止时已把完成批次写到 .partial.md
    partial_path = out_path.with_suffix(".partial.md")
    if partial_path.exists():
        _, done_sections = parse_outline_sections(
            project_io.read_md(partial_path)
        )
        if done_sections:
            chunks = [done_sections[n] for n in sorted(done_sections)]
            start = max(done_sections) + 1
            logger.info("章节细纲从断点续跑：已完成 %d 章", len(done_sections))

    batches = []
    while start <= total:
        end = min(start + CHAPTER_OUTLINE_BATCH - 1, total)
        batches.append((start, end))
        start = end + 1

    for i, (a, b) in enumerate(batches, 1):
        if runner._stop:
            break
        prior = _prior_beats_block("\n\n".join(chunks), a)
        prompt = build_chapter_outline_batch_prompt(brief, a, b, total, prior_beats=prior)
        agent.clear_history()
        # 15 章 × 4 行 ≈ 2–3k 字；显式放宽到 8192，防 provider 默认值过低截断。
        # 单批硬超时 300s：避免 provider 连接挂死把整个工作流拖 30+ 分钟。
        try:
            resp = await asyncio.wait_for(
                agent.run(prompt, thinking="off", keep_history=False, max_tokens=8192),
                timeout=300,
            )
        except asyncio.TimeoutError:
            logger.error("章节细纲批 %d（第%d–%d章）超时，保留已生成部分", i, a, b)
            break
        raw = (resp.content or "").strip()
        text = clean_outline_text(raw)
        if text:
            chunks.append(text)
            # 每批落一次中间产物：停止/崩溃后不必从第 1 章重跑
            project_io.write_md(partial_path, "\n\n".join(chunks) + "\n")
            _, got = parse_outline_sections(text)
            missing = [n for n in range(a, b + 1) if n not in got]
            if missing:
                logger.warning("章节细纲批 %d 缺章 %s（已保留其余）", i, missing[:10])
            if len(raw) - len(text) > 200:
                logger.info("章节细纲批 %d 清理了 %d 字非条目内容", i, len(raw) - len(text))
        else:
            logger.warning("章节细纲批 %d 无有效条目，已丢弃", i)
        logger.info("章节细纲进度: 第 %d–%d 章（批 %d/%d）", a, b, i, len(batches))

    if runner._stop:
        logger.info("章节细纲被停止，已完成批次保存在 %s", partial_path.name)
        return
    if not chunks:
        return
    project_io.write_md(out_path, "\n\n".join(chunks) + "\n")
    try:
        partial_path.unlink(missing_ok=True)
    except OSError:
        pass
    msg = f"章节细纲已生成（{total} 章 / {len(batches)} 批本轮）"
    logger.info(msg)
    if runner.on_step_end:
        runner.on_step_end(step.id, n, agent.title, msg)


@handler("update_chapter_outline")
async def _run_update_chapter_outline(runner: "WorkflowRunner", step: "WorkflowStep", n: int):
    """根据前 n 章实际走向，修订第 n+1 章起的章节细纲。"""
    out_path = runner.project_dir / _OUTLINE_DOC
    existing = project_io.read_md(out_path) if out_path.exists() else ""
    if not existing.strip():
        logger.info("无既有章节细纲，跳过修订")
        return
    total = _resolve_total_chapters(runner)
    revise_from = n + 1
    if revise_from > total:
        logger.info("已到末章，无需修订细纲")
        return

    # 只改未来一小段：单次输出可控，区间外原样保留（绝不整文件覆盖）
    revise_to = min(total, revise_from + OUTLINE_REVISE_WINDOW - 1)
    existing_window = outline_window(existing, revise_from, revise_to)

    agent = runner.find_agent(step.needs) if step.needs else None
    agent = agent or _fallback_agent(runner, (SKILL_REVIEW,))
    if not agent:
        return
    # 最近剧情：优先概要，回退正文摘要
    recent_parts = []
    for ch in _scan_nonempty_chapters(runner)[-5:]:
        summary = project_io.read_md(
            project_io.chapter_summary_path(
                runner.project_dir, ch["number"], ch["title"]
            )
        )
        if summary:
            recent_parts.append(f"第{ch['number']}章《{ch['title']}》\n{summary[:400]}")
        else:
            body = project_io.read_md(ch["content_path"])[:300]
            if body:
                recent_parts.append(f"第{ch['number']}章《{ch['title']}》\n{body}")
    recent = "\n\n".join(recent_parts)[:2500]
    if runner.on_step_start:
        runner.on_step_start(step.id, n, agent.title)
    prompt = build_chapter_outline_revise_prompt(
        existing_window, recent, n, total, revise_from, revise_to
    )
    try:
        agent.clear_history()
        resp = await agent.run(prompt, thinking="off", keep_history=False, max_tokens=8192)
        text = (resp.content or "").strip()
        if not text:
            logger.warning("章节细纲修订返回空内容，保留原文件")
            return
        merged = splice_outline(existing, revise_from, revise_to, text)
    except ValueError as e:
        logger.warning("章节细纲修订未通过校验，保留原文件: %s", e)
        return
    except Exception as e:
        logger.error("章节细纲修订失败（保留原文件）: %s", e)
        return

    project_io.write_md(out_path, merged)
    msg = f"细纲已修订（第{revise_from}–{revise_to}章，其余保留）"
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
