"""智能体配置加载。"""

from __future__ import annotations

import json
from pathlib import Path

from .base import AgentConfig, BaseAgent

# 智能体配置文件路径
AGENTS_PATH = Path(__file__).resolve().parent.parent.parent.parent / "config" / "agents.json"


def load_agents() -> dict:
    """从 agents.json 加载智能体配置。"""
    if AGENTS_PATH.exists():
        return json.loads(AGENTS_PATH.read_text(encoding="utf-8"))
    return {}


def save_agents(agents_cfg: dict):
    """保存智能体配置到 agents.json。"""
    AGENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENTS_PATH.write_text(json.dumps(agents_cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# 兼容旧名
load_default_agents = load_agents

__all__ = ["AgentConfig", "BaseAgent", "load_agents", "save_agents", "load_default_agents"]
