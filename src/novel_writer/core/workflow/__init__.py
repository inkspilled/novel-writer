"""工作流引擎 — 技能驱动的多 Agent 协作。

支持:
- 线性步骤执行
- 技能匹配（needs → agent.skills）
- 循环步骤（repeat: 逐章写作）
- 文件输入/输出（读取 MD 文件作为上下文，输出写入文件）
- 模板变量插值（{n}, {title}, {genre} 等）
- 分层上下文组装与动态压缩
- 进度回调

模块布局:
- constants:    技能名与步骤 id 分组
- definition:   WorkflowStep/WorkflowDef、内置模板、build_workflow
- context:      分层上下文组装与压缩
- prompts:      节奏控制/角色锚定/内置步骤提示词
- builtin_steps: 特殊步骤处理器注册表
- sediment:     写后沉淀（记忆/伏笔/追读力提取）
- runner:       WorkflowRunner 执行器
"""
from .builtin_steps import BUILTIN_STEP_HANDLERS
from .constants import (
    CHAPTER_CONTENT_STEP_IDS,
    PLANNING_STEP_IDS,
    PLANNING_UPDATE_STEP_PREFIX,
    POST_CHAPTER_PERIODIC_IDS,
    PRE_CHAPTER_PERIODIC_IDS,
    SKILL_PROOFREAD,
    SKILL_REVIEW,
    SKILL_TYPO_CHECK,
)
from .context import (
    MAX_CONTEXT_TOKENS,
    STEP_CONTEXT_TIERS,
    ContextBuilder,
    ContextTier,
    compress_context,
    estimate_tokens,
)
from .definition import (
    CONTINUE_WORKFLOW,
    DEFAULT_WORKFLOW,
    PLANNING_WORKFLOW,
    VALIDATE_WORKFLOW,
    WorkflowDef,
    WorkflowMode,
    WorkflowStep,
    build_workflow,
)
from .prompts import (
    build_character_constraint,
    build_pacing_context,
)
from .runner import WorkflowError, WorkflowRunner
from .sediment import extract_chapter_title

# 兼容别名：旧代码以 _build_character_constraint 引用
_build_character_constraint = build_character_constraint
_estimate_tokens = estimate_tokens
_compress_context = compress_context
_extract_chapter_title = extract_chapter_title

__all__ = [
    "BUILTIN_STEP_HANDLERS",
    "CHAPTER_CONTENT_STEP_IDS",
    "CONTINUE_WORKFLOW",
    "DEFAULT_WORKFLOW",
    "MAX_CONTEXT_TOKENS",
    "PLANNING_STEP_IDS",
    "PLANNING_UPDATE_STEP_PREFIX",
    "PLANNING_WORKFLOW",
    "POST_CHAPTER_PERIODIC_IDS",
    "PRE_CHAPTER_PERIODIC_IDS",
    "SKILL_PROOFREAD",
    "SKILL_REVIEW",
    "SKILL_TYPO_CHECK",
    "STEP_CONTEXT_TIERS",
    "VALIDATE_WORKFLOW",
    "ContextBuilder",
    "ContextTier",
    "WorkflowDef",
    "WorkflowError",
    "WorkflowMode",
    "WorkflowRunner",
    "WorkflowStep",
    "build_character_constraint",
    "build_pacing_context",
    "build_workflow",
    "compress_context",
    "estimate_tokens",
    "extract_chapter_title",
]
