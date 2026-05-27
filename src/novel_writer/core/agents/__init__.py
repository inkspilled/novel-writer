"""智能体配置加载。"""

from __future__ import annotations

import json
from pathlib import Path

from .base import AgentConfig, BaseAgent

# 默认智能体配置文件路径
DEFAULT_AGENTS_PATH = Path(__file__).resolve().parent.parent.parent.parent / "config" / "default_agents.json"


def load_default_agents() -> dict:
    """从 default_agents.json 加载默认智能体配置。"""
    if DEFAULT_AGENTS_PATH.exists():
        return json.loads(DEFAULT_AGENTS_PATH.read_text(encoding="utf-8"))
    return {}


__all__ = ["AgentConfig", "BaseAgent", "load_default_agents"]
