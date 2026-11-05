"""章后抽取（digest）— 一次 LLM 调用产出四份章后数据。

这四份数据原本各由一个步骤独立生成，每章要读四遍同一份正文、重发四遍
世界状态与伏笔清单：

- ``chapter_summary``    → 章节结构化概要
- ``world_state_update`` → 世界状态快照增量
- ``percept_extract``    → 角色可感知的正典事件
- ``sediment``           → 叙事记忆（状态/伏笔/承诺/关系/规则）

`chapter_digest` 步骤把四次调用合成一次，落 ``digest/{n}.json``；四个步骤
仍然各自负责校验与落盘，只是优先消费 digest，缺键或整个 digest 缺失时
回退到原来的 LLM 路径（老项目 / 自定义工作流不会被打断）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .. import project_io
from ..json_extract import extract_json_object
from ..logger import get_logger

logger = get_logger(__name__)

DIGEST_DIR = "digest"

# 四个消费方各自认领的键；顺序即日志里的展示顺序。
DIGEST_KEYS = ("summary", "world", "percept", "memory")
# 第五段：主线/支线自动推进（可选；老 digest 没有也能用）
THREADS_KEY = "threads"

# 记忆沉淀至少要命中一个桶才认为这份 memory 片段可用（否则交给正则兜底）。
_MEMORY_BUCKETS = (
    "character_state", "open_loops", "resolved",
    "reader_promises", "relationships", "world_rules",
)

# 单次输出预算：五段 JSON 合计通常 1.8–3.5k tokens，留足余量。
DIGEST_MAX_TOKENS = 7000


def digest_path(project_dir: Path | str, chapter: int) -> Path:
    """``{项目}/digest/{n}.json``。"""
    return Path(project_dir) / DIGEST_DIR / f"{int(chapter)}.json"


def load_digest(project_dir: Path | str, chapter: int) -> dict:
    """读取本章 digest；不存在/损坏时返回空 dict（调用方回退）。"""
    path = digest_path(project_dir, chapter)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("第%d章 digest 读取失败（将回退独立抽取）: %s", chapter, e)
        return {}
    if not isinstance(data, dict):
        return {}
    fingerprint = data.get("_content_hash")
    if fingerprint:
        filename = project_io.find_chapter_file(Path(project_dir), chapter)
        if not filename:
            return {}
        try:
            content = project_io.read_md(Path(project_dir) / "chapters" / filename)
        except OSError:
            return {}
        if fingerprint != hashlib.sha256(content.encode("utf-8")).hexdigest():
            return {}
    return data


def digest_section(project_dir: Path | str, chapter: int, key: str) -> Any:
    """取 digest 的某一段；没有就返回 None。"""
    return load_digest(project_dir, chapter).get(key)


def save_digest(project_dir: Path | str, chapter: int, data: dict) -> None:
    project_io.write_json(digest_path(project_dir, chapter), data)


def normalize_digest(payload: Any) -> dict:
    """只保留四个消费方真正能用的键；类型不对的直接丢，让对应步骤自己回退。"""
    if not isinstance(payload, dict):
        return {}
    out: dict[str, Any] = {}

    summary = payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        out["summary"] = summary.strip()

    world = payload.get("world")
    # 空 dict 也要保留：它表示「本章世界状态无变化」，消费方据此跳过独立调用，
    # 而不是把「没有变化」误判成「模型没给这一段」。
    if isinstance(world, dict):
        out["world"] = world

    percept = payload.get("percept")
    if isinstance(percept, dict) and isinstance(percept.get("events"), list):
        out["percept"] = percept

    memory = payload.get("memory")
    if isinstance(memory, dict) and any(k in memory for k in _MEMORY_BUCKETS):
        out["memory"] = memory

    threads = payload.get("threads")
    if isinstance(threads, dict) and any(
        isinstance(threads.get(k), list) for k in ("main", "subplot")
    ):
        out[THREADS_KEY] = threads

    return out


def needs_digest(project_dir: Path | str, chapter: int, content: str | None = None) -> bool:
    """是否还需要（重）抽取。

    四段齐全才算完成。正文指纹必须一致；旧缓存没有指纹时重新抽取一次。
    """
    data = load_digest(project_dir, chapter)
    if not all(key in data for key in DIGEST_KEYS):
        return True
    if content is not None:
        return data.get("_content_hash") != hashlib.sha256(content.encode("utf-8")).hexdigest()
    return False


async def run_chapter_digest(
    project_dir: Path | str,
    chapter: int,
    agent,
    *,
    content: str,
    world_text: str,
    roster: list[str],
    active_loops: list[dict],
    threads_context: str = "",
) -> dict:
    """调用一次 LLM 产出并落盘 digest；失败返回空 dict（调用方各自回退）。

    输出不合法时补一句「只输出 JSON」再试一次——与 ``extract_percepts`` 同策略。
    四段里拿到两段以上就落盘：缺的段由消费方单独补，不必整章重抽。
    ``threads`` 是可选第五段（主线/支线自动推进）。
    """
    from .prompts import build_chapter_digest_prompt

    prompt = build_chapter_digest_prompt(
        chapter, content, world_text=world_text, roster=roster,
        active_loops=active_loops, threads_context=threads_context,
    )
    for attempt in range(2):
        try:
            response = await agent.run(
                prompt, thinking="off", keep_history=False, max_tokens=DIGEST_MAX_TOKENS,
            )
        except Exception as e:
            logger.warning("[DIGEST] 第%d章章后抽取调用失败（回退独立抽取）: %s", chapter, e)
            return {}

        data = normalize_digest(extract_json_object(response.content or ""))
        if len(data) >= 2:
            data["_chapter"] = chapter
            data["_source"] = "chapter_digest"
            data["_chars"] = len(content)
            data["_content_hash"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
            try:
                save_digest(project_dir, chapter, data)
            except Exception as e:
                logger.error("第%d章 digest 落盘失败（本次仍可用，下次重抽）: %s", chapter, e)
            return data

        logger.warning(
            "[DIGEST] 第%d章章后抽取返回可用字段不足（%s），第%d次重试",
            chapter, list(data) or "无", attempt + 1,
        )
        prompt = (
            "上一次输出不是合法 JSON 或缺少字段。请严格只输出一个 JSON 对象，"
            "必须包含 summary / world / percept / memory 四个键，"
            "并尽量包含 threads（主线/支线推进），不要解释、不要 Markdown。\n\n"
            + prompt
        )

    logger.warning("[DIGEST] 第%d章章后抽取两次均失败，四个步骤将各自调用", chapter)
    return {}


def digest_summary(project_dir: Path | str, chapter: int) -> str:
    """便捷取用：章节概要文本（没有则空串）。"""
    value = digest_section(project_dir, chapter, "summary")
    return value.strip() if isinstance(value, str) else ""
