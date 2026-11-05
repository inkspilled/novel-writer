"""短剧 AI 工作流 Worker：真正调用模型做改编 / 分集 / 分镜。

任务一览（DramaModeDialog 的五个模式）：
- full       全流程：圣经 → 智能分集(剧本) → 分镜 → 资产，已有产物自动跳过
- segment    智能分集：滑动窗口按剧情冲突合并章节，先规划再写剧本
- adapt      重编圣经：小说规划 + 滚动摘要 → 改编圣经
- storyboard 一键分镜：剧本 → 镜头 JSON
- assets     提取资产：分镜 → 角色/地点占位（本地规则，不调模型）
"""
from __future__ import annotations

import asyncio
import re
import time

from PySide6.QtCore import QThread, Signal

from ...core.agents.base import AgentConfig, BaseAgent
from ...core.drama import DramaStore, parse_storyboard_output
from ...core.drama_context import (
    ADAPT_DOC_BUDGET, STORYBOARD_BUDGET, parse_character_names, slice_bible,
    truncate, compress_script,
)
from ...core.llm.base import BaseLLM
from ...core.drama_adaptation import AdaptiveDramaRunner
from ...core.logger import get_logger

logger = get_logger(__name__)

# 需要大模型的任务（assets 是本地规则提取，llm 可为 None）
NEEDS_LLM_TASKS = ("full", "adapt", "segment", "storyboard")


class DramaAIWorker(QThread):
    """顺序执行短剧任务；每章/每集一次模型调用，支持停止。

    各任务方法只返回 summary，由 _run() 统一 emit finished_ok；
    异常透传到 run() 统一 emit failed。
    """

    progress = Signal(str, int)          # 状态文字, 百分比
    log = Signal(str)
    usage_updated = Signal(dict)         # 累计 token（对齐小说侧 mini bar）
    agent_state = Signal(str, str, str)  # agent key, working/done/error/idle, task
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        llm: BaseLLM | None,
        store: DramaStore,
        project_info: dict,
        task: str,
        scope: str = "current",
        chapters: list[dict] | None = None,
        current_chapter: int = 0,
        current_episode: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self.llm = llm
        self.store = store
        self.project_info = project_info or {}
        self.task = task
        self.scope = scope
        self.chapters = chapters or []
        self.current_chapter = int(current_chapter or 0)
        self.current_episode = int(current_episode or 0)
        self._stop = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._context_note = ""
        self._total_usage: dict = {}

    def request_stop(self):
        self._stop = True
        loop, task = self._loop, self._task
        if loop is not None and task is not None and not loop.is_closed() and not task.done():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    # ── 线程入口 ──

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._task = self._loop.create_task(self._run())
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            if self._stop:
                self.finished_ok.emit("已停止，已完成的内容已保存")
            else:
                self.failed.emit("生成被取消")
        except Exception as e:
            logger.error("短剧 AI 任务失败: %s", e, exc_info=True)
            self.failed.emit(str(e)[:500])
        finally:
            self._loop.close()
            self._loop = None
            self._task = None

    # ── 任务分发 ──

    async def _run(self):
        try:
            if self.task == "full":
                summary = await self._run_full()
            elif self.task == "adapt":
                summary = await self._run_adapt()
            elif self.task == "segment":
                summary = await self._run_segment()
            elif self.task == "storyboard":
                summary = await self._run_storyboard()
            elif self.task == "assets":
                summary = self._run_assets()
            else:
                raise ValueError(f"未知短剧任务：{self.task}")
            self.finished_ok.emit(summary)
        except asyncio.CancelledError:
            raise

    # ── 全流程 ──

    async def _run_full(self) -> str:
        """圣经 → 智能分集(剧本) → 分镜 → 资产；已完成的跳过，只补没做完的。"""
        parts: list[str] = []
        skipped: list[str] = []

        episodes = self.store.load_episodes()
        used: set[int] = set()
        for ep in episodes:
            numbers = ep.get("source_chapters") or (
                [ep["source_chapter"]] if ep.get("source_chapter") else []
            )
            used.update(int(n) for n in numbers if n)
        pending = [
            c for c in self.chapters
            if int(c["number"]) not in used and (c.get("text") or "").strip()
        ]
        with_script = [e for e in episodes if self.store.has_script(int(e["number"]))]
        missing_shots = [
            e for e in with_script
            if not self.store.load_storyboard(int(e["number"]))["shots"]
        ]
        self.log.emit(
            f"全流程接续点：剧本 {len(with_script)} 集 · 待用正文 {len(pending)} 章 · "
            f"缺分镜 {len(missing_shots)} 集 · "
            f"圣经{'已有，将跳过' if self.store.bible_text().strip() else '待生成'}"
        )

        self.progress.emit("全流程 1/4：改编圣经", 5)
        if self.store.bible_text().strip():
            skipped.append("圣经")
            self.log.emit("① 改编圣经已存在，跳过（要重写请单独跑「重编圣经」）")
        else:
            try:
                parts.append(await self._run_adapt())
            except Exception as e:
                # 圣经是节选增强材料，失败不拦全流程；分集在无圣经下也能跑
                self.log.emit(f"改编圣经生成失败，继续分集：{str(e)[:200]}")
        if self._stop:
            return self._partial(parts)

        self.progress.emit("全流程 2/4：智能分集（剧本）", 25)
        before = len([e for e in self.store.load_episodes() if self.store.has_script(int(e["number"]))])
        parts.append(await self._run_segment())
        after = len([e for e in self.store.load_episodes() if self.store.has_script(int(e["number"]))])
        if after == before:
            skipped.append("分集")
            self.log.emit("② 没有待用正文，分集跳过（已生成的剧本不动）")
        if self._stop:
            return self._partial(parts)

        self.progress.emit("全流程 3/4：一键分镜", 60)
        parts.append(await self._run_storyboard(only_missing_shots=True))
        if self._stop:
            return self._partial(parts)

        self.progress.emit("全流程 4/4：提取资产", 90)
        try:
            parts.append(self._run_assets())
        except RuntimeError as e:
            if "还没有分镜" in str(e):
                skipped.append("资产")
                self.log.emit("④ 还没有分镜，资产跳过")
            else:
                raise
        self.progress.emit("全流程已完成", 100)
        tail = f"（跳过：{'、'.join(skipped)}）" if skipped else ""
        return "全流程完成 · " + "；".join(p for p in parts if p) + tail

    @staticmethod
    def _partial(parts: list[str]) -> str:
        return "已停止" + ("（" + "；".join(parts) + "）" if parts else "")

    # ── 智能分集 ──

    async def _run_segment(self) -> str:
        planner = self._make_agent("分集策划", "你是短剧策划，按戏剧冲突合并小说章节，严格返回指定 JSON。", 0.4, 4096)
        writer = self._make_agent("分集编剧", "你是短剧编剧，按分集规划与目标时长创作可拍摄的场次剧本。", 0.7, 8192)

        async def ask(stage, prompt):
            key = "showrunner" if stage == "plan" else "writer"
            self.agent_state.emit(key, "working", "分集规划" if stage == "plan" else "编写剧本")
            try:
                text, usage, elapsed = await self._ask(planner if stage == "plan" else writer, prompt,
                                                       thinking="off",
                                                       max_tokens=4096 if stage == "plan" else 8192)
            except BaseException:
                self.agent_state.emit(key, "error", "本次未完成，素材保留")
                raise
            self.agent_state.emit(key, "done", "分集规划" if stage == "plan" else "编写剧本")
            self.log.emit(f"{'规划' if stage == 'plan' else '剧本'} · {elapsed:.1f}s · {self._usage(usage)}")
            return text

        runner = AdaptiveDramaRunner(self.store, ask, log=self.log.emit,
                                     progress=self.progress.emit, stopped=lambda: self._stop)
        return await runner.run(self.chapters, one_episode=self.scope == "current")

    # ── 重编圣经 ──

    async def _run_adapt(self) -> str:
        agent = self._make_agent(
            "短剧策划",
            "你是竖屏短剧总策划。把小说规划重新组织成可执行的短剧改编圣经，"
            "只输出 Markdown 文档，不要解释过程。",
            temperature=0.5,
            max_tokens=8192,
        )
        self.agent_state.emit("showrunner", "working", "汇总小说规划")
        self.progress.emit("正在生成改编圣经…", 10)
        prompt = self._adapt_prompt()
        if self._context_note:
            self.log.emit(self._context_note)
        text, usage, elapsed = await self._ask(agent, prompt, thinking="off", max_tokens=8192)
        if self._stop:
            return "已停止"
        if not (text or "").strip():
            self.agent_state.emit("showrunner", "error", "")
            raise RuntimeError("模型没有返回内容，请检查模型配置或稍后重试")
        text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
        self.store.save_bible(text + "\n")
        self.agent_state.emit("showrunner", "done", "改编圣经")
        self.progress.emit("改编圣经已生成", 100)
        self.log.emit(
            f"一键改编完成：{len(text)} 字 · {elapsed:.1f}s · {self._usage(usage)}"
        )
        return "改编圣经已生成"

    # ── 一键分镜 ──

    async def _run_storyboard(self, only_missing_shots: bool = False) -> str:
        items = self._episode_items(only_missing_shots)
        if not items:
            if only_missing_shots:
                self.log.emit("③ 所有剧本都已有最新分镜，跳过")
                return "分镜已齐全"
            raise RuntimeError("没有可用的剧本。先「智能分集」，再一键分镜。")
        agent = self._make_agent(
            "分镜师",
            "你是短剧分镜师。把剧本拆成可拍摄的镜头表，严格只输出 JSON，不要 Markdown 代码块和解释。",
            temperature=0.4,
            max_tokens=8192,
        )
        success = 0
        failed = 0
        duration = int(self.store.load_series().get("episode_seconds") or 90)
        for i, ep in enumerate(items, start=1):
            if self._stop:
                break
            number = int(ep["number"])
            self.progress.emit(
                f"正在拆第{number}集分镜（{i}/{len(items)}）",
                int((i - 1) / len(items) * 100),
            )
            self.agent_state.emit("storyboard", "working", f"第{number}集")
            script = self.store.load_script(number)
            prompt = self._storyboard_prompt(ep, script, duration)
            if self._context_note:
                self.log.emit(self._context_note)
            try:
                text, usage, elapsed = await self._ask(
                    agent, prompt, thinking="off", max_tokens=8192
                )
                if self._stop:
                    break
                shots = parse_storyboard_output(text, number)
                if not shots:
                    # 一次修复重试：只针对 JSON 格式，不改剧本
                    self.log.emit(f"第{number}集 JSON 解析失败，重试一次…")
                    repair = (
                        prompt
                        + "\n\n上一次输出不是合法 JSON。请只输出一个 JSON 对象，"
                        "格式为 {\"shots\": [...]}，不要代码块、不要解释。"
                    )
                    text, usage, elapsed = await self._ask(
                        agent, repair, thinking="off", max_tokens=8192
                    )
                    if self._stop:
                        break
                    shots = parse_storyboard_output(text, number)
                if not shots:
                    raise RuntimeError("模型输出的分镜不是合法 JSON")
                self.store.save_storyboard(number, {"shots": shots}, update_source=True)
                success += 1
                self.agent_state.emit("storyboard", "done", f"第{number}集")
                self.log.emit(
                    f"第{number}集分镜 · {len(shots)} 镜 · {elapsed:.1f}s · {self._usage(usage)}"
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                failed += 1
                self.agent_state.emit("storyboard", "error", f"第{number}集")
                logger.error("第%s集分镜失败: %s", number, e)
                self.log.emit(f"第{number}集分镜失败：{str(e)[:200]}")
            self.progress.emit(f"已完成 {i}/{len(items)}", int(i / len(items) * 100))
        if self._stop:
            return f"已停止（成功 {success} 集）"
        if success == 0:
            raise RuntimeError("所有集分镜都生成失败，请检查模型配置或日志")
        return f"已生成 {success} 集分镜" + (f"，失败 {failed}" if failed else "")

    # ── 提取资产（本地，不调模型）──

    def _run_assets(self) -> str:
        episodes = [
            e for e in self.store.load_episodes()
            if self.store.load_storyboard(int(e["number"]))["shots"]
        ]
        if not episodes:
            raise RuntimeError("还没有分镜，先「一键分镜」或跑全流程。")
        self.agent_state.emit("prompt", "working", "提取资产")
        added = 0
        for ep in episodes:
            added += self.store.extract_assets_from_storyboard(int(ep["number"]))
        self.agent_state.emit("prompt", "done", f"新增 {added}")
        return f"资产清单 +{added}"

    # ── 选择/提示词 ──

    def _episode_items(self, only_missing_shots: bool = False) -> list[dict]:
        items = [
            e for e in self.store.load_episodes()
            if self.store.has_script(int(e["number"]))
        ]
        if only_missing_shots:
            # 全流程增量模式：缺分镜的补，剧本改过的重拆，已有最新分镜的不动
            need: list[dict] = []
            for ep in items:
                number = int(ep["number"])
                data = self.store.load_storyboard(number)
                if not data["shots"]:
                    need.append(ep)
                elif data.get("script_changed"):
                    self.log.emit(f"第{number}集剧本改过，分镜需要重拆")
                    need.append(ep)
            return need
        if self.scope == "current":
            current = self.current_episode or (items[-1]["number"] if items else 0)
            return [e for e in items if int(e["number"]) == int(current)][:1]
        return items

    def _make_agent(self, title: str, system_prompt: str, temperature: float, max_tokens: int) -> BaseAgent:
        cfg = AgentConfig(
            name=f"drama_{self.task}",
            role=title,
            title=title,
            system_prompt=system_prompt,
            skills=["短剧改编"],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return BaseAgent(cfg, self.llm)

    @staticmethod
    def _merge_usage(*usages: dict) -> dict:
        total: dict[str, int] = {}
        for usage in usages:
            for key, value in (usage or {}).items():
                if isinstance(value, (int, float)):
                    total[key] = total.get(key, 0) + int(value)
        return total

    async def _ask(self, agent: BaseAgent, prompt: str, thinking: str, max_tokens: int):
        t0 = time.monotonic()
        resp = await agent.run(
            prompt, thinking=thinking, keep_history=False, max_tokens=max_tokens
        )
        usage = dict(resp.usage or {})
        text = (resp.content or "").strip()
        if not text:
            # 思考型模型把输出预算全用在 reasoning 上时 content 为空；
            # 关掉思考再来一次（而不是直接把「没有内容」丢给用户）。
            self.log.emit(
                f"模型未返回正文（finish={resp.finish_reason or 'n/a'} · "
                f"{self._usage(usage)}），关闭思考重试一次…"
            )
            retry = await agent.run(
                prompt, thinking="off", keep_history=False, max_tokens=max_tokens
            )
            usage = self._merge_usage(usage, retry.usage or {})
            resp = retry
            text = (resp.content or "").strip()
        if not text:
            raise RuntimeError(
                f"模型没有返回正文（finish={resp.finish_reason or 'n/a'}）。"
                "多半是思考把输出预算吃完了：可以在「模型与智能体」里换模型，"
                "或把该步骤的 max_tokens 调大后重试。"
            )
        self._total_usage = self._merge_usage(self._total_usage, usage)
        self.usage_updated.emit(dict(self._total_usage))
        return text, usage, time.monotonic() - t0

    def _adapt_prompt(self) -> str:
        known = parse_character_names(self._read_planning("人物设定.md"))
        before = after = 0
        docs = []
        for label, filename, keys in (
            ("立意", "立意.md", ("卖点", "核心", "立意", "读者")),
            ("大纲", "大纲.md", ("主线", "卷", "转折", "高潮", "结局")),
            ("人物设定", "人物设定.md", ("主角", "关系", "动机", "成长")),
            ("世界观", "世界观.md", ("规则", "势力", "禁忌", "设定")),
        ):
            body = self._read_planning(filename).strip()
            before += len(body)
            if not body:
                docs.append(f"### {label}\n（空）")
                continue
            sliced = slice_bible(body, keys, ADAPT_DOC_BUDGET)
            after += len(sliced)
            docs.append(f"### {label}\n{sliced}")

        # 已完成章节的滚动摘要：比规划更贴近「实际写了什么」，做全书圣经要用
        rolling = ""
        try:
            from ...core import project_io
            rolling = (project_io.latest_summary(self.store.project_dir) or "").strip()
        except Exception:
            rolling = ""
        if rolling:
            before += len(rolling)
            picked = truncate(rolling, 1800)
            after += len(picked)
            docs.append(f"### 已完成章节滚动摘要\n{picked}")

        self._context_note = (
            f"改编材料切片：规划文档/滚动摘要 {before} 字 → 送入 {after} 字"
            f"（单份上限 {ADAPT_DOC_BUDGET}，出场人物 {len(known)} 个）"
        )
        info = self.project_info
        series = self.store.load_series()
        return (
            "请为下面的小说做一份短剧改编圣经。只输出 Markdown。\n\n"
            f"书名：{info.get('title', '')}\n"
            f"题材：{info.get('genre', '')}　风格：{info.get('style', '')}\n"
            f"核心立意：{info.get('theme', '')}\n"
            f"一句话简介：{info.get('synopsis', '')}\n\n"
            f"【已定企划，必须遵守】{series.get('platform')} / {series.get('aspect')}，"
            f"{series.get('episode_count')} 集，每集 {series.get('episode_seconds')} 秒\n"
            "【小说规划文档（已按预算切片，省略部分不要自行补写）】\n" + "\n\n".join(docs) + "\n\n"
            "【输出要求】\n"
            "1. 一句话卖点（谁想要什么、谁挡着、局面怎么失控）\n"
            "2. 目标观众、平台、画幅、集数、单集时长\n"
            "3. 主线一句话 + 前 3 集钩子\n"
            "4. 保留/删除/合并的支线与角色\n"
            "5. 主要角色视觉锚点（外形/服装/标志物）\n"
            "6. 每集节奏规则：前 3 秒、15 秒、结尾 5 秒分别要什么\n"
            "7. 短剧化红线：不能直接照搬小说章节，必须重新分集\n"
        )

    def _read_planning(self, filename: str) -> str:
        path = self.store.project_dir / "planning" / filename
        try:
            return path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError:
            return ""

    def _storyboard_prompt(self, episode: dict, script: str, duration: int) -> str:
        compact = compress_script(script, STORYBOARD_BUDGET)
        self._context_note = f"第{episode['number']}集分镜：剧本 {len(script)} 字 → 送入 {len(compact)} 字"
        return (
            f"把下面第{episode['number']}集剧本拆成 20-40 个镜头。"
            "只输出 JSON 对象，不要 Markdown 代码块、不要解释。\n\n"
            "JSON 结构：\n"
            '{"shots":[{"scene_id":"SC01","shot_size":"中景","camera":"固定",'
            '"duration_sec":3.0,"location":"地点","time":"日/夜",'
            '"characters":"人物","action":"画面/动作","dialogue":"台词",'
            '"sfx":"音效","transition":"硬切","status":"draft","notes":""}]}\n\n'
            "要求：\n"
            "- 每镜 1-5 秒，总时长接近单集时长；\n"
            "- 景别用远景/全景/中景/近景/特写，运镜用固定/推近/拉远/摇/跟/手持；\n"
            "- action 写可拍画面，不写心理活动；\n"
            "- 关键台词保留在 dialogue，用「角色：台词」；\n"
            "- 不新增剧本没有的剧情，不改变人物。\n\n"
            f"【单集目标时长】{duration} 秒\n\n"
            f"【剧本】\n{compact}\n"
        )

    @staticmethod
    def _usage(usage: dict) -> str:
        if not isinstance(usage, dict):
            return "usage n/a"
        total = usage.get("total_tokens")
        if not total:
            # DeepSeek 等只回 prompt/completion，没有 total_tokens
            total = int(usage.get("prompt_tokens", 0) or 0) + int(
                usage.get("completion_tokens", 0) or 0
            )
        return f"{int(total)} tok" if total else "usage n/a"
