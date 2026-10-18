"""角色推演 — 角色档案解析与 sim 缓存读写。

工作流 `sim` 步骤用通用 LLM 把推演结果写入 `sim_cache/sim_{n}.md`，
写作上下文经 `load_sim_cache` 注入（带 `=== 角色推演 ===` 标记）。
`parse_characters` 是人物设定角色档案的唯一权威解析（角色锚定转引本表）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SIM_CACHE_DIR = "sim_cache"

# 非角色标题跳过名单（唯一权威，prompts.build_character_constraint 转引本表）
CHARACTER_SKIP_TITLES = frozenset({
    "核心同伴", "关键对立/引导角色", "关系网络", "动态演进", "主角",
})


@dataclass
class CharacterProfile:
    """角色档案。"""
    name: str = ""
    tier: str = "extra"  # core / recurring / extra，由人物设定标题推断
    personality: str = ""
    motivation: str = ""
    weakness: str = ""
    background: str = ""
    is_protagonist: bool = False


def parse_characters(char_content: str) -> list[CharacterProfile]:
    """从人物设定文本中解析角色档案（含主角标记）。"""
    characters = []
    lines = char_content.split("\n")
    current = None
    current_start = 0

    def _flush(end: int):
        nonlocal current
        if current and current.name:
            # 主角检测：标题后 10 行（或到下一标题前）含「主角」/「protagonist」
            for j in range(current_start + 1, min(current_start + 11, end)):
                if re.match(r"^#{2,4}\s+", lines[j].strip()):
                    break
                low = lines[j].lower()
                if "主角" in lines[j] or "protagonist" in low:
                    current.is_protagonist = True
                    break
            characters.append(current)
        current = None

    for i, line in enumerate(lines):
        line = line.strip()

        # 匹配角色名（### 或 ## 开头，排除非角色标题）
        m = re.match(r"^#{2,4}\s+(.+?)$", line)
        if m:
            name = m.group(1).strip()
            if re.match(r"^[一二三四五六七八九十]", name):
                _flush(i)  # 章节序号标题：结束当前角色，避免后续行串档
                continue
            if name in CHARACTER_SKIP_TITLES:
                _flush(i)  # 跳过名单标题：同上
                continue
            _flush(i)
            from .characters.naming import canonical_name, infer_tier
            current = CharacterProfile(
                name=canonical_name(name), tier=infer_tier(name),
            )
            current_start = i
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

    _flush(len(lines))
    return characters


def load_sim_cache(project_dir: Path, chapter: int) -> str:
    """加载缓存的推演结果；带 `=== 角色推演 ===` 标记供压缩器分类。"""
    cache_dir = project_dir / SIM_CACHE_DIR
    cache_file = cache_dir / f"sim_{chapter}.md"
    if not cache_file.exists():
        return ""
    body = cache_file.read_text(encoding="utf-8")
    if not body.strip():
        return ""
    if body.lstrip().startswith("=== 角色推演 ==="):
        return body
    return f"=== 角色推演 ===\n{body}"
