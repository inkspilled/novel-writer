"""四线程：主做 / 审核 / 监督 / 代理。

文章 L324-L357：

| 角色 | 负责什么 |
|---|---|
| 主做 | 负责所有制作 |
| 审核 | 每一步都检查，提交生成视频之前必须写「通过」 |
| 监督 | 把我说过的所有话列成清单，盯着大家有没有忘记 |
| 代理 | 按我的偏好提出修正建议 |

桌面程序没有「Claude 自己开子 agent」这层运行时，所以这里换一种等价落地：
**每个线程都是一次独立的模型调用**（上下文互不可见），由 `DramaAIWorker` 按
顺序编排；四个线程的提示词、判定口径、决定记录都在本模块固化。

- `FOUR_THREAD_PROMPT`：文章原文（可直接发给导演模型，声明规矩）；
- `review_step_prompt()` / `supervisor_*_prompt()` / `agent_*_prompt()`：三个线程的提示词；
- `parse_decision()`：只认「批准 / 不批准 / 等我」，并抽出依据与把握；
- `record_decision()`：所有决定进 `drama/review/ledger.jsonl`，我回来可以推翻。
"""
from __future__ import annotations

from typing import Any

from .drama_review import PASS, append_ledger, parse_verdict

# 文章 L330-L340 的分工表
THREAD_ROLES: tuple[tuple[str, str, str], ...] = (
    ("main", "主做", "负责所有制作"),
    ("review", "审核", "每一步都检查，提交生成视频之前必须写「通过」"),
    ("supervisor", "监督", "把我说过的所有话列成清单，盯着大家有没有忘记"),
    ("agent", "代理", "按我的偏好提出修正建议"),
)

ROLE_LABELS: dict[str, str] = {key: label for key, label, _duty in THREAD_ROLES}

# 协作提示词
FOUR_THREAD_PROMPT = """这支片子用四线程来做。你当主做，再开三个子 agent，四个一起配合，一直做到片子做完。
我说过的话在：[对话记录或者要求文件的路径；Claude Code 的记录在 ~/.claude/projects/ 下]。

分工：
- 主做：就是你，负责所有制作。
- 审核：开一个子 agent，只挑错，不改文件。每一步都要查，提交生成视频之前必须明确写"通过"。
- 监督：开一个子 agent，读完我说过的所有话，整理成要求清单。每一步都查有没有人忘了要求，包括你。
- 代理：开一个子 agent，读完我说过的所有话，整理成我的偏好。遇到分歧时，按我的偏好提出建议，写明依据。

配合的顺序：
1. 你每做完一步，先交给审核，再交给监督。
2. 有分歧时请代理提出建议。
3. 所有决定都记进审核记录，我回来可以推翻任何决定。"""


def four_thread_prompt(
    conversation_path: str = "", *, project_dir: str = ""
) -> str:
    """把文章原文的占位符换成实际值。"""
    path = conversation_path or (project_dir or "本项目对话记录")
    return (
        FOUR_THREAD_PROMPT
        .replace("[对话记录或者要求文件的路径；Claude Code 的记录在 ~/.claude/projects/ 下]", path)
    )


def review_step_prompt(step: str, materials: dict[str, Any] | None = None, *, criteria: str = "") -> str:
    """审核线程：每一步都查，只挑错、不改文件，提交视频前必须写「通过」。"""
    lines = [
        "你是审核线程。只挑错，不改文件，不替我拍板。",
        f"这一步是：{step}",
    ]
    if materials:
        lines.append("材料：")
        for key, value in materials.items():
            if value in (None, "", [], {}):
                continue
            lines.append(f"- {key}：{value}")
    if criteria:
        return "\n".join([*lines, "", criteria,
                          "只判断当前阶段。按位置列出实质问题及改法，最后单独一行写「通过」或「不通过」。"])
    lines += [
        "",
        "按顺序查：",
        "1. 这一步的产物和我说过的要求对得上吗？（对不上就指出是哪一句要求）",
        "2. 有没有违背以下七条规矩？（逐条过）",
    ]
    from .drama_preflight import RULES

    lines += [f"   {index}. {rule}" for index, rule in enumerate(RULES, start=1)]
    lines += [
        "3. 有没有漏掉可在当前阶段发现的问题？",
        "",
        "只写问题，每条给出「位置 + 问题 + 建议改法」。",
        "最后单独一行写「通过」或「不通过」。提交生成视频这一步，没有「通过」就不许往下走。",
    ]
    return "\n".join(lines)


def supervisor_extract_prompt(conversation_excerpt: str) -> str:
    """监督线程第一步：把我说过的话整理成要求清单。"""
    return "\n".join([
        "你是监督线程。下面是导演和我的全部对话/要求原文。",
        "把我说过的所有话整理成一份**可核对的要求清单**：",
        "1. 一条一行，用我原来的措辞，不要改写、不要合并；",
        "2. 每条后面标「硬要求 / 偏好 / 一次性交代」；",
        "3. 有前后矛盾的地方单独列出来，不要替我做决定；",
        "4. 只输出清单，不要建议、不要评价。",
        "",
        "【我说过的话】",
        (conversation_excerpt or "（空）").strip(),
    ])


def supervisor_check_prompt(requirements: list[str], step: str, artifact: str = "") -> str:
    """监督线程第二步：每一步查有没有人忘了要求（包括主做）。"""
    lines = [
        "你是监督线程。只核对要求有没有被忘掉，不改文件、不评价好坏。",
        f"这一步：{step}",
        "",
        "要求清单：",
    ]
    lines += [f"{index}. {item}" for index, item in enumerate(requirements, start=1)] or ["（清单为空）"]
    if artifact:
        lines += ["", "这一步的产物：", artifact]
    lines += [
        "",
        "逐条判断：满足 / 违反 / 还没到这一步 / 无法判断。",
        "违反的写清「哪条要求 + 产物哪里违背 + 怎么补」。",
        "最后单独一行写「无遗漏」或「有遗漏」。",
    ]
    return "\n".join(lines)


def agent_preference_prompt(conversation_excerpt: str) -> str:
    """代理线程第一步：整理我的偏好（我平时会怎么选）。"""
    return "\n".join([
        "你是代理线程。读完我说过的所有话，整理成我的偏好表。",
        "1. 我明确说过的取舍（要/不要、先做哪个、什么不能碰）；",
        "2. 我反复强调过的词（出现两次以上）；",
        "3. 我默认会怎么选（例如优先保留角色外观或叙事节奏）；",
        "4. 每条后面写「依据：我原话的哪一句」。",
        "只整理偏好，不要替我决定还没发生的事。",
        "",
        "【我说过的话】",
        (conversation_excerpt or "（空）").strip(),
    ])


def agent_decision_prompt(
    question: str,
    *,
    preferences: list[str] | None = None,
) -> str:
    """代理线程第二步：拿不准的事按我的偏好决定，写明依据和把握。"""
    lines = ["你是代理线程。按我的偏好提出建议，尊重审核与监督结论。", ""]
    if preferences:
        lines += ["我的偏好："] + [f"{index}. {text}" for index, text in enumerate(preferences, start=1)]
    lines += [
        "",
        f"要决定的事：{question}",
        "",
        "只写三行：",
        "决定：批准 / 不批准 / 等我",
        "依据：引用我的哪句话或哪条偏好（没有依据就写「无依据，等我」）",
        "把握：高 / 中 / 低",
    ]
    return "\n".join(lines)


def parse_decision(text: str) -> dict[str, str]:
    """解析代理线程输出：决定/依据/把握。"""
    result = {"decision": "", "basis": "", "confidence": ""}
    for line in (text or "").splitlines():
        stripped = line.strip().lstrip("-* ")
        for key, label in (("decision", "决定"), ("basis", "依据"), ("confidence", "把握")):
            if stripped.startswith(label):
                value = stripped[len(label):].lstrip("：: ").strip()
                if value:
                    result[key] = value
    if not result["decision"]:
        verdict = parse_verdict(text)
        if verdict == PASS:
            result["decision"] = "批准"
        elif verdict:
            result["decision"] = "不批准"
    return result


def record_decision(
    store,
    role: str,
    *,
    step: str = "",
    verdict: str = "",
    note: str = "",
    basis: str = "",
    episode: int = 0,
    shot_id: str = "",
) -> None:
    """四线程的每个决定都进审核记录（同一本账，我回来能翻）。"""
    append_ledger(store, {
        "kind": f"thread:{role}",
        "role": ROLE_LABELS.get(role, role),
        "step": step,
        "episode": int(episode or 0),
        "shot_id": shot_id,
        "verdict": verdict,
        "note": note or step,
        "basis": basis,
    })


def thread_summary(entries: list[dict[str, Any]]) -> str:
    """四线程一屏总结：谁查了几步、几个不通过。"""
    counts: dict[str, dict[str, Any]] = {}
    for entry in entries or []:
        role = str(entry.get("role") or entry.get("kind") or "")
        if not role:
            continue
        slot = counts.setdefault(role, {"checks": 0, "failed": 0})
        slot["checks"] += 1
        if str(entry.get("verdict") or "") == "不通过":
            slot["failed"] += 1
    lines = ["# 四线程记录", "", "| 线程 | 检查 | 不通过 |", "|---|---|---|"]
    for role, slot in counts.items():
        lines.append(f"| {role} | {slot['checks']} | {slot['failed']} |")
    if not counts:
        lines.append("| （还没有记录） | | |")
    return "\n".join(lines) + "\n"
