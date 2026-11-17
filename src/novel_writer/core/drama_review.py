"""审核闸门：故事冷读 / 首尾帧自查 / 提交前审核 / 全片复核。

使用独立模型审核：

- L106-L117：故事定稿后新开 agent 冷读，只给故事、不给结论；
- L199-L211：首尾帧画完逐条自查（8 条），**审过再画**；
- L275-L287：每条视频提交前，新开 agent 核对提示词和图片的每个细节，
  通过才提交；
- L313-L323：成片再新开 agent，像观众一样从头看一遍。

本项目不自动 spawn 子 agent（桌面程序没有这层运行时），但把四套提示词、
材料清单、判定口径和**通过闸门**全部固化：

- `review_prompt(kind, ...)`：四类审核提示词；
- `parse_verdict()` / `parse_shot_verdicts()`：只认「通过 / 不通过」，以最后一次为准；
- `save_review()`：落 `drama/review/{kind}_epNNN.md`，头部记判定与逐镜结果；
- `submit_gate()`：**每一镜**都要有「通过」记录，否则不许提交视频；
- `ThreadLedger` / `append_ledger()`：所有审核与决定记进 `review/ledger.jsonl`，
  我回来可以推翻任何决定。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import project_io
from .drama_story import story_review_prompt

REVIEW_DIR = "review"
LEDGER_FILE = "ledger.jsonl"

REVIEW_KINDS: tuple[str, ...] = ("story", "keyframe", "submit", "final")
KIND_TITLES: dict[str, str] = {
    "story": "故事冷读",
    "keyframe": "首尾帧自查",
    "submit": "提交前审核",
    "final": "全片复核",
}

PASS = "通过"
FAIL = "不通过"
_VERDICT_RE = re.compile(r"不通过|通过")
_SHOT_ID_RE = re.compile(r"E\d{2,4}-S\d{2,4}")
_HEADER_VERDICT_RE = re.compile(r"^>\s*判定[:：]\s*(不通过|通过)", re.MULTILINE)
_HEADER_SHOTS_RE = re.compile(r"^>\s*逐镜[:：]\s*(.+)$", re.MULTILINE)

# 首尾帧 8 条自查（文章 L204-L211 原文）
KEYFRAME_SELF_CHECKS: tuple[str, ...] = (
    "动作要用到的东西，首帧里必须有。比如后面要拿杯子，首帧里就要看得到杯子，否则杯子会凭空冒出来。",
    "别人的反应要提前摆好。一段视频就是一个镜头，没法中途切过去拍别人，所以首帧里要安排好："
    "一个人在前面做事，另一个人在后面，脸朝着镜头。",
    "很多人一起出现时，不能摆同一个姿势，不然模型会让所有人同时动。",
    "给动作留出空间。东西要落到、飘到的地方要提前空出来，人的头顶也要留点空。",
    "风往哪边吹要统一，不能围巾往左飘，床单却往右飘。",
    "缩小到手机大小看一遍，远处的人太小、看不清就要改。",
    "放大看有没有多余的东西，比如多一个水壶，或者多一条奇怪的细线。",
    "把首帧和尾帧叠在一起看有没有错位。不该变的地方两张图必须一样。"
    "比如墙的颜色不一样，视频里墙就会慢慢变色；杯子大了一点，视频里杯子就会慢慢变大。",
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _materials_text(materials: dict[str, Any] | None) -> str:
    if not materials:
        return "材料：见本文件附件。"
    bits = []
    for key, value in materials.items():
        if value in (None, "", []):
            continue
        bits.append(f"- {key}：{value}")
    return "\n".join(bits) if bits else "材料：见本文件附件。"


def review_prompt(kind: str, materials: dict[str, Any] | None = None, **kwargs) -> str:
    """四类审核提示词；`kind` 取 `REVIEW_KINDS` 之一。"""
    kind = str(kind or "").strip()
    if kind == "story":
        story = kwargs.get("story") or (materials or {}).get("故事") or ""
        return story_review_prompt(str(story))
    if kind == "keyframe":
        shot = kwargs.get("shot") or "本镜"
        return "\n".join([
            "首尾帧画完了，帮我按下面这些逐条检查，每张图都要放大看。"
            "有问题就告诉我是哪张图、哪里不对，我同意了再改。",
            _materials_text(materials),
            f"本镜：{shot}",
            "",
            *[f"{i}. {text}" for i, text in enumerate(KEYFRAME_SELF_CHECKS, start=1)],
            "",
            "最后只写「通过」或「不通过」。不通过就逐条列出是哪张图、哪里不对、怎么改。",
        ])
    if kind == "submit":
        return "\n".join([
            "你是审核，只读不改。这个镜头马上要提交视频生成。",
            _materials_text(materials),
            "逐项核对，放大看图：",
            "1. 提示词的每句话和两张图对得上吗？（位置、左右、颜色、人数、道具数量、衣服、表情）",
            "2. 两张图的机位、大小、背景一致吗？",
            "3. 和设定图、配色规则、前后镜头对得上吗？",
            "4. 动作现实吗？每个动作的原因写了吗？手放在哪？",
            "5. 模型可能在哪里出错？（同时动作、凭空出现、长脸、走反方向、歧义词）",
            '最后只写"通过"或"不通过"。不通过就逐条列出要改什么。',
        ])
    if kind == "final":
        video = kwargs.get("video") or (materials or {}).get("成片") or "（待填）"
        return "\n".join([
            f"这是成片：{video}。你第一次看，我不告诉你任何结论。",
            "1. 全部解码，每镜取多张全分辨率帧，切点前后各取 3 帧。",
            "2. 先写你看懂的故事，再列看不懂的地方。",
            "3. 逐镜查：重复的动作、道具数量、穿帮、突跳、颜色跳变、脸变形、字的问题。",
            "4. 明确写出你查不了的东西（动态手感、声音）。",
            '每条写：时间、帧号、问题。分"硬伤"和"小问题"。',
        ])
    raise ValueError(f"未知审核类型：{kind}（可选 {'/'.join(REVIEW_KINDS)}）")


def parse_verdict(text: str) -> str:
    """只认最后一次「通过 / 不通过」；都没有返回空串。"""
    lines = [line.strip().strip('* ') for line in (text or '').splitlines() if line.strip()]
    return lines[-1] if lines and lines[-1] in (PASS, FAIL) else ""


def parse_shot_verdicts(text: str) -> dict[str, str]:
    """按镜号分段取判定：``## E01-S001`` 到下一镜之间的「通过/不通过」。"""
    result: dict[str, str] = {}
    current = ""
    buffer: list[str] = []
    for line in (text or "").splitlines():
        found = _SHOT_ID_RE.search(line)
        if found:
            if current:
                verdict = parse_verdict("\n".join(buffer))
                if verdict:
                    result[current] = verdict
            current = found.group(0)
            buffer = [line[found.end():]]
            continue
        if current:
            buffer.append(line)
    if current:
        verdict = parse_verdict("\n".join(buffer))
        if verdict:
            result[current] = verdict
    return result


def review_path(store, kind: str, episode: int) -> Path:
    return Path(store.root) / REVIEW_DIR / f"{kind}_ep{int(episode):03d}.md"


def ledger_path(store) -> Path:
    return Path(store.root) / REVIEW_DIR / LEDGER_FILE


def save_review(
    store,
    kind: str,
    episode: int,
    text: str,
    *,
    verdict: str = "",
    materials: dict[str, Any] | None = None,
    material_snapshot: str | None = None,
) -> dict[str, Any]:
    """落一份审核记录；判定以正文最后一次「通过/不通过」为准。"""
    if kind not in REVIEW_KINDS:
        raise ValueError(f"未知审核类型：{kind}")
    body = (text or "").strip()
    final_verdict = verdict or parse_verdict(body)
    shot_verdicts = parse_shot_verdicts(body)
    path = review_path(store, kind, episode)
    path.parent.mkdir(parents=True, exist_ok=True)
    shot_line = "；".join(f"{sid} {value}" for sid, value in shot_verdicts.items())
    header = [
        f"# {KIND_TITLES[kind]} · 第{int(episode)}集",
        "",
        f"> 时间：{_now()}",
        f"> 判定：{final_verdict or '未判定'}",
    ]
    if shot_line:
        header.append(f"> 逐镜：{shot_line}")
    if materials:
        pairs = "；".join(f"{key}={value}" for key, value in materials.items() if value)
        header.append(f"> 材料：{pairs}")
    header.append("")
    content = "\n".join(header) + "\n" + body + "\n"
    from .drama_production import preserve, snapshot
    preserve(path)
    preserve(path.with_suffix('.json'))
    project_io.write_md(path, content)
    project_io.write_json(path.with_suffix('.json'), {"snapshot": material_snapshot or snapshot(store, episode)})
    record = {
        "at": _now(),
        "kind": kind,
        "episode": int(episode),
        "verdict": final_verdict,
        "shot_verdicts": shot_verdicts,
        "path": str(path),
    }
    append_ledger(store, {
        "at": record["at"], "kind": kind, "episode": int(episode),
        "shot_id": "", "verdict": final_verdict, "note": f"审核记录 {path.name}",
    })
    return record


def load_review(store, kind: str, episode: int) -> dict[str, Any] | None:
    """读回审核记录：判定从头部优先，头部没有就从正文解析。"""
    path = review_path(store, kind, episode)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    header = _HEADER_VERDICT_RE.search(text)
    verdict = header.group(1) if header else parse_verdict(text)
    shots_line = _HEADER_SHOTS_RE.search(text)
    shot_verdicts: dict[str, str] = {}
    if shots_line:
        for part in re.split(r"[；;\n]+", shots_line.group(1)):
            found = _SHOT_ID_RE.search(part)
            if not found:
                continue
            value = parse_verdict(part)
            if value:
                shot_verdicts[found.group(0)] = value
    return {
        "path": path,
        "verdict": verdict,
        "shot_verdicts": shot_verdicts or parse_shot_verdicts(text),
        "text": text,
    }


def has_passed(store, kind: str, episode: int, shot_id: str = "") -> bool:
    review = load_review(store, kind, episode)
    if not review:
        return False
    if shot_id:
        shot_verdict = review.get("shot_verdicts", {}).get(shot_id)
        if shot_verdict:
            return shot_verdict == PASS
        return review.get("verdict") == PASS
    return review.get("verdict") == PASS


def submit_gate(store, episode: int, shots: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """提交视频前的硬闸：每一镜都要有「通过」记录。

    返回 `(能不能提交, 待办清单)`；清单里每行说清哪个镜号缺什么。
    """
    review = load_review(store, "submit", episode)
    if review is None:
        return False, [f"第{episode}集还没有提交前审核记录：先跑「提交前审核」"]
    if review.get("verdict") != PASS:
        return False, ["本集提交前审核尚未整体通过，请重新审核"]
    from .drama_production import read_state, snapshot
    try:
        evidence = read_state(review_path(store, "submit", episode).with_suffix('.json'))
    except (OSError, ValueError):
        evidence = {}
    if evidence.get('snapshot') != snapshot(store, episode):
        return False, ["审核后材料已改动或缺少版本证据，请重新运行提交前审核"]
    per_shot = review.get("shot_verdicts") or {}
    pending: list[str] = []
    for index, shot in enumerate(shots or [], start=1):
        sid = str(shot.get("shot_id") or f"E{int(episode):02d}-S{index:03d}")
        verdict = per_shot.get(sid) or ""
        if verdict != PASS:
            reason = "审核不通过" if verdict == FAIL else "没有审核记录"
            pending.append(f"{sid}：{reason}")
    if pending:
        return False, pending
    return True, []


# ── 审核/决定账本（审核、监督、代理都记这里）──


def append_ledger(store, entry: dict[str, Any]) -> None:
    path = ledger_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({**entry, "at": entry.get("at") or _now()}, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_ledger(store) -> list[dict[str, Any]]:
    path = ledger_path(store)
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                entries.append(data)
    except OSError:
        return []
    return entries


def ledger_markdown(store) -> str:
    entries = read_ledger(store)
    lines = [
        "# 审核与决定记录",
        "",
        f"> 共 {len(entries)} 条；我回来可以推翻任何决定。",
        "",
        "| 时间 | 类型 | 集 | 镜号 | 判定 | 依据/备注 |",
        "|---|---|---|---|---|---|",
    ]
    for item in entries:
        lines.append(
            "| {at} | {kind} | {ep} | {shot} | {verdict} | {note} |".format(
                at=str(item.get("at") or "")[:19],
                kind=str(item.get("kind") or item.get("role") or ""),
                ep=item.get("episode") or "",
                shot=str(item.get("shot_id") or ""),
                verdict=str(item.get("verdict") or ""),
                note=str(item.get("note") or item.get("basis") or "").replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"
