"""智能体配置加载 — 两层合并。

随包默认层（只读基准）：开发 = 项目根 config/agents.json，打包 = _internal/config/agents.json。
升级安装只刷新这一层（默认智能体随包走，不允许用户增删改）。
用户自定义层：<安装目录>/config/agents.json（不可写时回退 %APPDATA%/NovelWriter/config），
安装/升级/卸载都不碰，自定义不丢。

路径一律在调用时从 ``app_config`` 取（不缓存模块级常量）：测试会 reload
``app_config`` 切换安装目录，缓存常量会让后续用例读到上一个 tmp 目录。
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import AgentConfig, BaseAgent
from .. import app_config
from ..logger import get_logger

logger = get_logger(__name__)

AGENTS_FILE = "agents.json"
# 兜底：历史遗留的 default_agents.json（agents.json 缺失时退回它）
FALLBACK_FILE = "default_agents.json"


def bundled_agents_path() -> Path:
    """随包默认智能体配置（只读）。"""
    return app_config.BUNDLED_CONFIG_DIR / AGENTS_FILE


def user_agents_path() -> Path:
    """用户层智能体配置（写目标，永不动 _internal）。"""
    return app_config.USER_CONFIG_DIR / AGENTS_FILE


def load_builtin_agents() -> dict:
    """随包默认智能体（只读）。"""
    for name in (AGENTS_FILE, FALLBACK_FILE):
        path = app_config.BUNDLED_CONFIG_DIR / name
        if path.exists():
            cfg = json.loads(path.read_text(encoding="utf-8"))
            logger.debug("加载默认智能体: %s（%d 个）", path, len(cfg))
            return cfg
    logger.warning("默认智能体配置不存在: %s", app_config.BUNDLED_CONFIG_DIR / AGENTS_FILE)
    return {}


def load_user_agents() -> dict:
    """用户自定义智能体（不存在则空）。"""
    path = user_agents_path()
    if path.exists():
        cfg = json.loads(path.read_text(encoding="utf-8"))
        logger.debug("加载自定义智能体: %s（%d 个）", path, len(cfg))
        return cfg
    return {}


def load_agents() -> dict:
    """合并加载：默认层 + 用户层。键冲突时默认层赢（内置不可被覆盖）。"""
    return {**load_user_agents(), **load_builtin_agents()}


def is_builtin_agent(name: str) -> bool:
    """是否随包内置智能体（内置不允许增删改）。"""
    return name in load_builtin_agents()


def save_agents(agents_cfg: dict):
    """保存智能体配置 — 落用户层（原子写），内置 _internal/config 永不被改写。"""
    from ..project_io import write_json

    write_json(user_agents_path(), agents_cfg)
    logger.info("保存智能体配置: %s（%d 个）", user_agents_path(), len(agents_cfg))


def reset_agents() -> dict:
    """重置为随包默认：清空用户层，回落到内置。"""
    path = user_agents_path()
    if path.exists():
        path.unlink()
        logger.info("已清空自定义智能体: %s", path)
    return load_agents()


__all__ = [
    "AgentConfig", "BaseAgent",
    "load_agents", "save_agents", "reset_agents",
    "load_builtin_agents", "load_user_agents", "is_builtin_agent",
    "bundled_agents_path", "user_agents_path",
]
