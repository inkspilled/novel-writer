"""智能体配置加载 — 两层合并。

随包默认层（只读基准）：开发 = 项目根 config/agents.json，打包 = _internal/config/agents.json。
升级安装只刷新这一层（默认智能体随包走，不允许用户增删改）。
用户自定义层：DATA_DIR/agents.json（打包后 %APPDATA%/NovelWriter/），
只存用户新增的智能体；安装/升级/卸载都不碰，自定义不丢。
"""
from __future__ import annotations

import json

from .base import AgentConfig, BaseAgent
from ..app_config import BUNDLED_CONFIG_DIR, DATA_DIR
from ..logger import get_logger

logger = get_logger(__name__)

# 随包默认层（只读，升级时被安装器整体刷新）
BUILTIN_AGENTS_PATH = BUNDLED_CONFIG_DIR / "agents.json"
# 兜底：历史遗留的 default_agents.json（agents.json 缺失时退回它）
_FALLBACK_PATH = BUNDLED_CONFIG_DIR / "default_agents.json"
# 用户自定义层（仅存非内置智能体）
USER_AGENTS_PATH = DATA_DIR / "agents.json"


def load_builtin_agents() -> dict:
    """随包默认智能体（只读）。"""
    for path in (BUILTIN_AGENTS_PATH, _FALLBACK_PATH):
        if path.exists():
            cfg = json.loads(path.read_text(encoding="utf-8"))
            logger.debug("加载默认智能体: %s（%d 个）", path, len(cfg))
            return cfg
    logger.warning("默认智能体配置不存在: %s", BUILTIN_AGENTS_PATH)
    return {}


def load_user_agents() -> dict:
    """用户自定义智能体（不存在则空）。"""
    if USER_AGENTS_PATH.exists():
        cfg = json.loads(USER_AGENTS_PATH.read_text(encoding="utf-8"))
        logger.debug("加载自定义智能体: %s（%d 个）", USER_AGENTS_PATH, len(cfg))
        return cfg
    return {}


def load_agents() -> dict:
    """合并加载：默认层 + 用户层。键冲突时默认层赢（内置不可被覆盖）。"""
    return {**load_user_agents(), **load_builtin_agents()}


def is_builtin_agent(name: str) -> bool:
    """是否随包内置智能体（内置不允许增删改）。"""
    return name in load_builtin_agents()


def save_agents(agents_cfg: dict):
    """保存智能体配置 — 只写用户自定义层。

    内置智能体条目一律滤掉（即使调用方把合并后的完整配置传回来），
    保证随包默认永不被运行时改写。
    """
    from ..project_io import write_json

    builtin = load_builtin_agents()
    user_cfg = {k: v for k, v in agents_cfg.items() if k not in builtin}
    write_json(USER_AGENTS_PATH, user_cfg)
    logger.info("保存自定义智能体: %d 个（内置 %d 个不落盘）", len(user_cfg), len(builtin))


def reset_agents() -> dict:
    """重置为随包默认：清空用户自定义层。"""
    if USER_AGENTS_PATH.exists():
        USER_AGENTS_PATH.unlink()
        logger.info("已清空自定义智能体: %s", USER_AGENTS_PATH)
    return load_agents()


__all__ = [
    "AgentConfig", "BaseAgent",
    "load_agents", "save_agents", "reset_agents",
    "load_builtin_agents", "load_user_agents", "is_builtin_agent",
    "BUILTIN_AGENTS_PATH", "USER_AGENTS_PATH",
]
