"""智能体配置加载。"""

from __future__ import annotations

import json
import shutil

from .base import AgentConfig, BaseAgent
from ..app_config import BUNDLED_CONFIG_DIR, USER_CONFIG_DIR, resolve_config_path
from ..logger import get_logger

logger = get_logger(__name__)

# 智能体配置：读 用户层(config/agents.json，存在则优先) → 内置(_internal/config/)；
# 保存/重置一律落用户层 USER_AGENTS_PATH，升级刷新 _internal 不丢用户修改
# （开发环境两层同目录，行为退化为单文件）。
AGENTS_PATH = resolve_config_path("agents.json")
USER_AGENTS_PATH = USER_CONFIG_DIR / "agents.json"
DEFAULT_AGENTS_PATH = USER_CONFIG_DIR / "default_agents.json"


def load_agents() -> dict:
    """从 agents.json 加载智能体配置。"""
    # 首次运行时，如果 default_agents.json 不存在，用内置 agents.json 初始化
    if not DEFAULT_AGENTS_PATH.exists() and BUNDLED_CONFIG_DIR.joinpath("agents.json").exists():
        DEFAULT_AGENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BUNDLED_CONFIG_DIR / "agents.json", DEFAULT_AGENTS_PATH)
        logger.info("初始化默认智能体配置: %s", DEFAULT_AGENTS_PATH)
    path = resolve_config_path("agents.json")
    if path.exists():
        cfg = json.loads(path.read_text(encoding="utf-8"))
        logger.debug("加载智能体配置: %s (%d 个智能体)", path, len(cfg))
        return cfg
    logger.warning("智能体配置文件不存在: %s", path)
    return {}


def save_agents(agents_cfg: dict):
    """保存智能体配置到用户层 agents.json（原子写），不改内置随包文件。"""
    from ..project_io import write_json
    write_json(USER_AGENTS_PATH, agents_cfg)
    logger.info("保存智能体配置: %s (%d 个智能体)", USER_AGENTS_PATH, len(agents_cfg))


def reset_agents() -> dict:
    """重置智能体配置为默认值，返回默认配置。"""
    if DEFAULT_AGENTS_PATH.exists():
        DEFAULT_AGENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DEFAULT_AGENTS_PATH, USER_AGENTS_PATH)
    return load_agents()


__all__ = ["AgentConfig", "BaseAgent", "load_agents", "save_agents", "reset_agents"]
