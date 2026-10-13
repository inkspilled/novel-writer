"""应用级配置 — 路径常量与用户配置读写。

开发环境数据写在项目根 `data/`；PyInstaller 打包后写到系统用户数据目录
（Windows %APPDATA%/NovelWriter、macOS ~/Library/Application Support/NovelWriter、
Linux ~/.local/share/novel-writer），避免安装到 Program Files 时无权限写入。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _project_root() -> Path:
    """源码树根目录（仅开发环境有效）。"""
    return Path(__file__).resolve().parents[3]


def _bundle_dir() -> Path:
    """打包后资源目录（onedir: exe 旁；onefile: _MEIPASS）。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(sys.executable).resolve().parent


def _user_data_dir() -> Path:
    if not _is_frozen():
        return _project_root() / "data"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "NovelWriter"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "NovelWriter"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "novel-writer"


PROJECT_ROOT = _project_root()
DATA_DIR = _user_data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
PROJECTS_DIR = DATA_DIR / "projects"

# 包内资源：开发环境在 src/novel_writer/assets；打包后在 bundle 根
if _is_frozen():
    ASSETS_DIR = _bundle_dir()
    LOGO_PATH = _bundle_dir() / "logo.png"
else:
    ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
    LOGO_PATH = PROJECT_ROOT / "logo.png"


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """先写临时文件再 rename，避免中途崩溃留下空/半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_config() -> dict:
    """读取用户配置（data/config.json），不存在或损坏时返回空 dict。"""
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("config.json 损坏或不可读，按空配置启动: %s", e)
        return {}


def save_config(config: dict) -> None:
    """原子写入用户配置到 data/config.json，并收紧文件权限（含 API Key）。"""
    _atomic_write_text(CONFIG_PATH, json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass  # Windows 无 POSIX 权限位


def safe_project_dirname(title: str) -> str:
    """把书名清洗成安全的目录名：防路径穿越、Windows 保留名/非法字符。"""
    name = (title or "").strip()
    # 路径分隔符与非法字符
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    name = name.strip(" .")  # Windows 盘尾空格/点不合法
    name = re.sub(r"\s+", "_", name)
    if not name or set(name) <= {"."}:
        name = "未命名项目"
    # Windows 保留设备名
    reserved = {"CON", "PRN", "AUX", "NUL",
                *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    if name.upper() in reserved:
        name = f"_{name}"
    return name[:80]
