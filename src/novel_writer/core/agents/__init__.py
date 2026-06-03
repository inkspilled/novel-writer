"""智能体配置加载。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .base import AgentConfig, BaseAgent
from ..logger import get_logger

logger = get_logger(__name__)

# 智能体配置文件路径
_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "config"
AGENTS_PATH = _CONFIG_DIR / "agents.json"
DEFAULT_AGENTS_PATH = _CONFIG_DIR / "default_agents.json"


def load_agents() -> dict:
    """从 agents.json 加载智能体配置。"""
    # 首次运行时，如果 default_agents.json 不存在，用 agents.json 初始化
    if not DEFAULT_AGENTS_PATH.exists() and AGENTS_PATH.exists():
        shutil.copy2(AGENTS_PATH, DEFAULT_AGENTS_PATH)
        logger.info("初始化默认智能体配置: %s", DEFAULT_AGENTS_PATH)
    if AGENTS_PATH.exists():
        cfg = json.loads(AGENTS_PATH.read_text(encoding="utf-8"))
        logger.debug("加载智能体配置: %d 个智能体", len(cfg))
        return cfg
    logger.warning("智能体配置文件不存在: %s", AGENTS_PATH)
    return {}


def save_agents(agents_cfg: dict):
    """保存智能体配置到 agents.json。"""
    AGENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENTS_PATH.write_text(json.dumps(agents_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("保存智能体配置: %d 个智能体", len(agents_cfg))


def reset_agents() -> dict:
    """重置智能体配置为默认值，返回默认配置。"""
    if DEFAULT_AGENTS_PATH.exists():
        shutil.copy2(DEFAULT_AGENTS_PATH, AGENTS_PATH)
    return load_agents()


__all__ = ["AgentConfig", "BaseAgent", "load_agents", "save_agents", "reset_agents"]
