"""应用级配置 — 路径常量与用户配置读写。

开发环境与打包后布局完全镜像：根目录下 `config/ data/ logs/` 三件套
（打包后根 = exe 所在安装目录；详见 runtime_paths.py）。安装目录不可写时
整体回退系统用户数据目录（Windows %APPDATA%/NovelWriter 等）。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .logger import get_logger
from .runtime_paths import (
    RuntimeDirs,
    bundle_dir,
    is_frozen,
    project_root,
    resolve_runtime_dirs,
)

logger = get_logger(__name__)

PROJECT_ROOT = project_root()

# 运行期目录三件套：开发=项目根，打包=安装目录（不可写时回退用户数据目录）
_DIRS: RuntimeDirs = resolve_runtime_dirs(frozen=is_frozen())
DATA_DIR = _DIRS.data
USER_CONFIG_DIR = _DIRS.user_config
CONFIG_PATH = DATA_DIR / "config.json"
PROJECTS_DIR = DATA_DIR / "projects"

# 随包只读配置：开发=项目根 config/，打包=_internal/config/
BUNDLED_CONFIG_DIR = bundle_dir() / "config" if is_frozen() else PROJECT_ROOT / "config"


def resolve_config_path(filename: str) -> Path:
    """随包配置的读/写落点：用户层存在则优先，否则回退内置。

    返回值同时是写目标——首次写入会落在用户层（安装目录 config/），
    内置 _internal/config/ 永不被改写，升级整体刷新不丢用户修改。
    开发环境两层同目录，行为退化为单文件。
    """
    user = USER_CONFIG_DIR / filename
    return user if user.exists() else BUNDLED_CONFIG_DIR / filename


# 包内资源：开发环境在 src/novel_writer/assets；打包后在 bundle 根
if is_frozen():
    ASSETS_DIR = bundle_dir()
    LOGO_PATH = bundle_dir() / "logo.png"
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
