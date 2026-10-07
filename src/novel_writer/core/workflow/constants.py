"""工作流常量 — 技能名、步骤 id 分组。"""
from __future__ import annotations

# ── 技能名（与 config/agents.json 中 agents 的 skills 对应） ──

SKILL_REVIEW = "剧情审查"        # 剧情审查/审核
SKILL_PROOFREAD = "校对"         # 校对
SKILL_TYPO_CHECK = "错别字检查"  # 错别字检查

# ── 章节循环中的定时步骤分组 ──

# 章前触发（写作前注入）
PRE_CHAPTER_PERIODIC_IDS = ("inspiration", "sim")

# 章后触发（写作后沉淀）
POST_CHAPTER_PERIODIC_IDS = (
    "polish", "proofread", "summary", "toc", "chapter_summary",
    "world_state_update", "update_outline", "update_characters", "update_foreshadow",
)

# ── 步骤 id 分组 ──

# 规划类步骤（产出 planning/ 文档，新书流程前半段）
PLANNING_STEP_IDS = frozenset({
    "ideation", "outline", "characters", "world",
    "timeline", "main_plot", "sub_plot", "foreshadow",
})

# 直接写章节内容的步骤（需要内容校验 + 备份保护）
CHAPTER_CONTENT_STEP_IDS = ("chapter", "polish")

# 反哺类步骤（强制覆盖 planning/ 文件）
PLANNING_UPDATE_STEP_PREFIX = "update_"
