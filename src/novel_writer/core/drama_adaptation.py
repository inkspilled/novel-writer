"""跨章改编：滑动阅读窗口、可验证分集边界和未消费素材交接。不依赖 Qt。"""
from __future__ import annotations

import hashlib
from typing import Callable

from . import project_io
from .drama import DramaStore, clean_script_output
from .drama_context import (
    PLAN_CHAPTER_BUDGET, PLAN_WINDOW_BUDGET, PREV_TAIL_BUDGET, SCRIPT_BUDGET,
    parse_character_names, plan_digest, slice_bible, slice_chapter, truncate,
)
from .json_extract import extract_json_object

WINDOW_CHAPTERS = 5
MAX_SOURCE_CHARS = 50_000


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_plan(payload: dict, window: list[dict]) -> dict:
    """模型只能消费阅读窗口的连续前缀，不能跳章、重复消费或猜测不存在的章。"""
    if not isinstance(payload, dict):
        raise ValueError("分集规划必须是 JSON 对象")
    if payload.get("wait_for_more") is True:
        return {"wait_for_more": True, "carry_reason": str(payload.get("carry_reason") or "等待后续剧情形成完整冲突")}
    selected = payload.get("source_chapters")
    if (not isinstance(selected, list) or not selected
            or any(type(n) is not int for n in selected)
            or selected != [c["number"] for c in window[:len(selected)]]):
        raise ValueError("本集来源必须是阅读窗口中从第一章开始的连续章节，不能跳章或重复")
    fields = {}
    for key in ("title", "goal", "hook"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"分集规划缺少 {key}")
        fields[key] = value.strip()
    beats = payload.get("beats")
    if not isinstance(beats, list) or not beats or not all(isinstance(b, str) and b.strip() for b in beats):
        raise ValueError("分集规划缺少可执行的剧情节拍 beats")
    return {**fields, "source_chapters": selected, "beats": beats,
            "carry_reason": str(payload.get("carry_reason") or "留给下一集继续编排"), "wait_for_more": False}


class AdaptiveDramaRunner:
    """规划成功后写剧本，剧本成功保存后才标记素材已消费；重跑从剩余素材继续。"""

    def __init__(self, store: DramaStore, ask: Callable, *, log=None, progress=None, stopped=None):
        self.store = store
        self.ask = ask
        self.log = log or (lambda text: None)
        self.progress = progress or (lambda text, percent: None)
        self.stopped = stopped or (lambda: False)

    def _pending(self, chapters: list[dict]) -> list[dict]:
        used: dict[int, str] = {}
        for ep in self.store.load_episodes():
            sources = ep.get("source_chapters") or ([ep["source_chapter"]] if ep.get("source_chapter") else [])
            for number in sources:
                used[number] = ep.get("source_hashes", {}).get(str(number), "")
        for chapter in chapters:
            fingerprint = used.get(chapter["number"])
            if fingerprint and fingerprint != source_hash(chapter["text"]):
                raise ValueError(f"第{chapter['number']}章在改编后已修改，请先检查对应剧集，避免新旧剧情混用")
        pending = [c for c in chapters if c["number"] not in used]
        if used and pending and pending[0]["number"] < max(used):
            raise ValueError("已有分集来源中间出现缺口，请先整理被删除或逐章改编的剧集，避免把前文追加到剧尾")
        return pending

    def _save_pending(self, pending: list[dict], reason: str = "", window=None) -> None:
        project_io.write_json(self.store.root / "adaptation.json", {
            "schema_version": 1, "pending_chapters": [c["number"] for c in pending],
            "carry_reason": reason, "last_window": window or [],
        })

    def _window(self, pending: list[dict]) -> list[dict]:
        window, size = [], 0
        for chapter in pending[:WINDOW_CHAPTERS]:
            if size + len(chapter["text"]) > MAX_SOURCE_CHARS:
                if not window:
                    raise ValueError(f"第{chapter['number']}章超过改编阅读预算，请先拆分该章")
                break
            window.append(chapter)
            size += len(chapter["text"])
        return window

    def _known_names(self) -> list[str]:
        path = self.store.project_dir / "planning" / "人物设定.md"
        try:
            return parse_character_names(path.read_text(encoding="utf-8")) if path.exists() else []
        except OSError:
            return []

    def _window_digests(self, window: list[dict], budget: int = PLAN_WINDOW_BUDGET) -> str:
        """窗口内每章按冲突密度给摘录，总量不超预算（不是把 5 章原文全塞进去）。"""
        names = self._known_names()
        per_chapter = max(600, min(PLAN_CHAPTER_BUDGET, budget // max(1, len(window))))
        parts = []
        for chapter in window:
            body = plan_digest(chapter.get("text") or "", per_chapter, names)
            parts.append(f"### 小说第{chapter['number']}章 {chapter.get('title', '')}\n{body}")
        return "\n\n".join(parts)

    def _plan_prompt(self, window: list[dict], previous: str, final: bool) -> str:
        series = self.store.load_series()
        material = self._window_digests(window)
        bible = slice_bible(
            self.store.bible_text(),
            ("分集", "节奏", "钩子", "红线", "规则"),
        )
        prev = truncate(previous, PREV_TAIL_BUDGET)
        return (
            "你是短剧分集策划。一次阅读最多5章，按冲突、反转、兑现与集尾钩子决定下一集的边界。\n"
            "不要机械地一章一集，也不要强凑5章。可以把前4章合为一集，第5章留给下一集。\n"
            "只消费窗口的连续前缀；未选章节不许提前写进本集。不要跳过章节或虚构来源。\n"
            "本阶段按完整章节合并，不在章内切分。若这些素材仍不足以形成一集，设置 wait_for_more=true，说明原因。\n"
            "只输出 JSON：{\"source_chapters\":[1,2],\"title\":\"集名\",\"goal\":\"本集变化\","
            "\"hook\":\"集尾悬念\",\"beats\":[\"开场冲突\",\"升级\",\"反转\"],"
            "\"carry_reason\":\"剩余素材为何顺延\",\"wait_for_more\":false}\n"
            f"平台：{series['platform']}；画幅：{series['aspect']}；单集时长：{series['episode_seconds']} 秒。\n"
            f"当前窗口是否已到现有正文末尾：{'是' if final else '否'}。\n"
            f"【改编规则】\n{bible}\n"
            f"【上一集结尾】\n{prev or '这是第一集'}\n"
            "【阅读窗口：每章按冲突密度摘录，标「（略）」处不要自行补写】\n"
            f"{material}"
        )

    async def run(self, chapters: list[dict], *, one_episode=False) -> str:
        chapters = sorted([dict(c, number=int(c["number"])) for c in chapters if (c.get("text") or "").strip()],
                          key=lambda c: c["number"])
        if len({c["number"] for c in chapters}) != len(chapters):
            raise ValueError("源章节号重复，请先整理章节")
        if not chapters:
            raise ValueError("没有可用的小说正文")
        pending = self._pending(chapters)
        self._save_pending(pending)
        total, completed = len(pending), 0
        while pending and not self.stopped():
            window = self._window(pending)
            number = self.store.next_adaptive_episode_number()
            previous = self.store.previous_episode_tail(number)
            self.progress(f"阅读第{window[0]['number']}–{window[-1]['number']}章，规划第{number}集", int((total-len(pending))/total*100))
            prompt = self._plan_prompt(window, previous, len(window) == len(pending))
            plan = None
            for attempt in range(2):
                text = await self.ask("plan", prompt)
                if self.stopped():
                    break
                try:
                    plan = validate_plan(extract_json_object(text), window)
                    break
                except ValueError as exc:
                    if attempt:
                        raise
                    prompt += f"\n上次规划无效：{exc}。请修正并只返回 JSON。"
            if self.stopped():
                break
            if plan["wait_for_more"]:
                self._save_pending(pending, plan["carry_reason"], [c["number"] for c in window])
                return f"已生成 {completed} 集；待用 {len(pending)} 章：{plan['carry_reason']}"
            selected = window[:len(plan["source_chapters"])]
            series = self.store.load_series()
            per_chapter = max(4000, SCRIPT_BUDGET // max(1, len(selected)))
            names = self._known_names()
            material = "\n\n".join(
                f"### 小说第{c['number']}章\n{slice_chapter(c.get('text') or '', per_chapter, names)}"
                for c in selected
            )
            bible = slice_bible(
                self.store.bible_text(),
                ("节奏", "钩子", "红线", "视觉", "分集"),
            )
            prev = truncate(previous, PREV_TAIL_BUDGET)
            script_prompt = (
                f"把以下选定素材编排为短剧第{number}集，严格按已确认的分集规划写作。只输出 Markdown 剧本。\n"
                f"平台：{series['platform']}；画幅：{series['aspect']}；目标：{series['episode_seconds']} 秒。\n"
                f"标题：{plan['title']}\n本集目标：{plan['goal']}\n集尾钩子：{plan['hook']}\n"
                f"节拍：{'；'.join(plan['beats'])}\n"
                "用 # 第N集 标题与 ## 第N场 地点 · 日/夜 格式。动作必须可拍摄，台词口语化。\n"
                "保留核心因果，可压缩合并场景；不能引入未选章节的剧情，不能重复已完成的反转。\n"
                "素材按冲突密度切片，标了「（略）」的地方不要补写剧情。\n"
                f"【改编规则】\n{bible}\n【上一集结尾】\n{prev}\n"
                f"【本集选定素材（按场景切片）】\n{material}"
            )
            self.progress(f"编写第{number}集（来源第{selected[0]['number']}–{selected[-1]['number']}章）", int((total-len(pending))/total*100))
            text = await self.ask("script", script_prompt)
            if self.stopped():
                break
            if not text.strip() or not re_has_scene(text):
                raise ValueError(f"第{number}集未返回有效场次剧本，素材仍保留在待用队列")
            script = clean_script_output(text, number, plan["title"])
            self.store.commit_adaptive_episode(number, plan, selected, script)
            completed += 1
            pending = pending[len(selected):]
            self._save_pending(pending, plan["carry_reason"], [c["number"] for c in window])
            self.log(f"第{number}集已保存 · 使用第{selected[0]['number']}–{selected[-1]['number']}章 · 剩余 {len(pending)} 章待用")
            if one_episode:
                break
        self.progress("智能分集已保存", 100)
        return f"{'已停止，' if self.stopped() else ''}已生成 {completed} 集，剩余 {len(pending)} 章待用"


def re_has_scene(text: str) -> bool:
    import re
    return bool(re.search(r"^##\s+.+\n+\s*\S", text, re.MULTILINE))
