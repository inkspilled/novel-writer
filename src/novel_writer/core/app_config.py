"""应用级配置 — 路径常量与用户配置读写。"""
from __future__ import annotations

import json
from pathlib import Path

# src/novel_writer/core/app_config.py → 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = DATA_DIR / "config.json"
PROJECTS_DIR = DATA_DIR / "projects"

# 包内资源（src/novel_writer/assets）
ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
LOGO_PATH = PROJECT_ROOT / "logo.png"


def load_config() -> dict:
    """读取用户配置（data/config.json），不存在时返回空 dict。"""
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    return {}


def save_config(config: dict) -> None:
    """保存用户配置到 data/config.json。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8-sig")
