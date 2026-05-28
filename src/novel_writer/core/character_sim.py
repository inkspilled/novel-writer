"""角色推演 — 让笔下的角色"活"起来，自主反应剧情冲突。

工作原理：
1. 从人物设定中提取角色档案（性格、动机、弱点）
2. 从大纲中提取当前章节的冲突/情境
3. 让 LLM 分别以每个角色的视角生成自主反应
4. 汇总成剧情走向建议，注入写作上下文

不需要额外基础设施，复用现有的 LLM 调用。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SIM_CACHE_DIR = "sim_cache"


@dataclass
class CharacterProfile:
    """角色档案。"""
    name: str = ""
    personality: str = ""
    motivation: str = ""
    weakness: str = ""
    background: str = ""


@dataclass
class SimResult:
    """单个角色的推演结果。"""
    character: str = ""
    reaction: str = ""      # 角色的自然反应
    decision: str = ""      # 角色可能做的决定
    emotion: str = ""       # 角色的情绪状态
    conflict_with: str = "" # 可能与谁产生冲突


@dataclass
class PlotSimulation:
    """完整的剧情推演结果。"""
    chapter: int = 0
    scenario: str = ""              # 当前情境描述
    character_reactions: list[SimResult] = field(default_factory=list)
    plot_suggestions: list[str] = field(default_factory=list)
    consistency_warnings: list[str] = field(default_factory=list)


def parse_characters(char_content: str) -> list[CharacterProfile]:
    """从人物设定文本中解析角色档案。"""
    characters = []
    lines = char_content.split("\n")
    current = None

    for line in lines:
        line = line.strip()

        # 匹配角色名（### 或 ## 开头，排除非角色标题）
        m = re.match(r"^#{2,4}\s+(.+?)$", line)
        if m:
            name = m.group(1).strip()
            if re.match(r"^[一二三四五六七八九十]", name):
                continue
            if name in ("核心同伴", "关键对立/引导角色", "关系网络", "动态演进", "主角"):
                continue
            if current and current.name:
                characters.append(current)
            current = CharacterProfile(name=name)
            continue

        if not current:
            continue

        # 提取属性
        if "性格" in line and ("：" in line or ":" in line):
            current.personality = line.split("：" if "：" in line else ":")[-1].strip()
        elif "动机" in line or "目标" in line or "驱动力" in line:
            current.motivation = line.split("：" if "：" in line else ":")[-1].strip()
        elif "弱点" in line or "缺陷" in line or "矛盾" in line:
            current.weakness = line.split("：" if "：" in line else ":")[-1].strip()
        elif "背景" in line and ("：" in line or ":" in line):
            current.background = line.split("：" if "：" in line else ":")[-1].strip()

    if current and current.name:
        characters.append(current)

    return characters


def build_sim_prompt(characters: list[CharacterProfile], scenario: str, chapter: int) -> str:
    """构建推演 prompt。"""
    char_blocks = []
    for c in characters[:5]:  # 最多 5 个角色
        block = f"【{c.name}】"
        if c.personality:
            block += f"\n性格：{c.personality}"
        if c.motivation:
            block += f"\n动机：{c.motivation}"
        if c.weakness:
            block += f"\n弱点：{c.weakness}"
        if c.background:
            block += f"\n背景：{c.background[:100]}"
        char_blocks.append(block)

    return f"""你是一个剧情推演引擎。以下是小说中的角色档案和当前情境。

## 角色档案
{chr(10).join(char_blocks)}

## 当前情境（第{chapter}章）
{scenario}

## 推演要求
请分别以每个角色的视角，根据其性格、动机和弱点，推演他们在此情境下的自然反应。

输出 JSON 格式：
```json
{{
  "reactions": [
    {{
      "character": "角色名",
      "reaction": "角色的第一反应（50字内）",
      "decision": "角色可能做的决定（50字内）",
      "emotion": "角色的情绪状态（一个词）",
      "conflict_with": "可能与谁产生冲突（无则空）"
    }}
  ],
  "plot_suggestions": [
    "基于角色反应推演出的剧情走向建议（3条，每条50字内）"
  ],
  "consistency_warnings": [
    "如果某个角色的反应与其设定不符，在此警告"
  ]
}}
```"""


def parse_sim_response(response: str) -> PlotSimulation:
    """解析 LLM 返回的推演结果。"""
    sim = PlotSimulation()

    # 提取 JSON
    json_match = re.search(r"```json\s*(.*?)\s*```", response, re.DOTALL)
    if not json_match:
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
    if not json_match:
        return sim

    try:
        data = json.loads(json_match.group(1) if json_match.group(1) else json_match.group(0))
    except json.JSONDecodeError:
        return sim

    for r in data.get("reactions", []):
        sim.character_reactions.append(SimResult(
            character=r.get("character", ""),
            reaction=r.get("reaction", ""),
            decision=r.get("decision", ""),
            emotion=r.get("emotion", ""),
            conflict_with=r.get("conflict_with", ""),
        ))

    sim.plot_suggestions = data.get("plot_suggestions", [])
    sim.consistency_warnings = data.get("consistency_warnings", [])

    return sim


def format_sim_text(sim: PlotSimulation) -> str:
    """将推演结果格式化为可注入上下文的文本。"""
    parts = ["=== 角色推演 ==="]

    for r in sim.character_reactions:
        parts.append(f"【{r.character}】情绪：{r.emotion}")
        parts.append(f"  反应：{r.reaction}")
        parts.append(f"  决定：{r.decision}")
        if r.conflict_with:
            parts.append(f"  冲突对象：{r.conflict_with}")

    if sim.plot_suggestions:
        parts.append("\n【剧情走向建议】")
        for i, s in enumerate(sim.plot_suggestions, 1):
            parts.append(f"  {i}. {s}")

    if sim.consistency_warnings:
        parts.append("\n【一致性警告】")
        for w in sim.consistency_warnings:
            parts.append(f"  ⚠ {w}")

    return "\n".join(parts)


def load_sim_cache(project_dir: Path, chapter: int) -> str:
    """加载缓存的推演结果。"""
    cache_dir = project_dir / SIM_CACHE_DIR
    cache_file = cache_dir / f"sim_{chapter}.md"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    return ""


def save_sim_cache(project_dir: Path, chapter: int, text: str):
    """缓存推演结果。"""
    cache_dir = project_dir / SIM_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"sim_{chapter}.md"
    cache_file.write_text(text, encoding="utf-8")
