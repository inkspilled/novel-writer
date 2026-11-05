"""运行期目录解析 — exe 装在哪，config/data/logs 就长在哪。

打包（onedir）后的首选布局与开发目录完全镜像::

    <安装目录>/
    ├── NovelWriter.exe
    ├── _internal/   随包只读（代码 + 内置 config/）
    ├── config/      用户层配置（agents.json 编辑、skills 覆盖），升级不覆盖
    ├── data/        用户数据（projects/、config.json）
    └── logs/        info.log / error.log

安装目录不可写（如被装进 Program Files）时整体回退到系统用户数据目录
（Windows %APPDATA%/NovelWriter、macOS ~/Library/Application Support/NovelWriter、
Linux ~/.local/share/novel-writer），写路径永不指向 _internal（学 smt resource_path.py）。

首次在可写安装目录启动时，把旧版用户数据目录整体搬入 <安装目录>/data/
（logs/ 不搬），原目录改名为 NovelWriter.bak-YYYYMMDD 防止重复搬运。

本模块是 leaf 模块：不得 import 包内其他模块（logger/app_config 都依赖它，
反向引用会造成循环导入）。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """源码树根目录（仅开发环境有效）。core/ 下上三级 = 项目根。"""
    return Path(__file__).resolve().parents[3]


def bundle_dir() -> Path:
    """打包后资源目录（onedir: _internal；onefile: _MEIPASS）。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(sys.executable).resolve().parent / "_internal"


def exe_dir() -> Path:
    """exe 所在目录（安装根）。"""
    return Path(sys.executable).resolve().parent


def can_write_dir(path: Path) -> bool:
    """实际写一个临时文件探测目录可写性（学 smt _can_write_dir）。"""
    try:
        with tempfile.TemporaryFile(dir=str(path)):
            return True
    except OSError:
        return False


def legacy_user_data_dir() -> Path:
    """0.1.0 的用户数据目录（%APPDATA%/NovelWriter 等），只作回退与迁移源。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "NovelWriter"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "NovelWriter"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "novel-writer"


@dataclass(frozen=True)
class RuntimeDirs:
    """一次解析出的运行期目录三件套（互相同根，见模块 docstring）。"""

    data: Path
    logs: Path
    user_config: Path


def resolve_runtime_dirs(
    *,
    frozen: bool | None = None,
    install_dir: Path | None = None,
    legacy_dir: Path | None = None,
    dev_root: Path | None = None,
    probe_writable=None,
) -> RuntimeDirs:
    """解析 data/logs/user_config 三件套。

    Args:
        frozen: 是否 PyInstaller 打包环境（None=按当前进程判断）。
        install_dir: 安装根（默认 exe 目录）。
        legacy_dir: 旧版用户数据目录（默认按平台推导）。
        dev_root: 开发环境项目根（默认源码树根）。
        probe_writable: 可写探测函数（测试注入用，默认 can_write_dir）。
    """
    if frozen is None:
        frozen = is_frozen()
    if not frozen:
        root = dev_root if dev_root is not None else project_root()
        return RuntimeDirs(data=root / "data", logs=root / "logs", user_config=root / "config")

    install = install_dir if install_dir is not None else exe_dir()
    legacy = legacy_dir if legacy_dir is not None else legacy_user_data_dir()
    if probe_writable is None:
        probe_writable = can_write_dir

    if probe_writable(install):
        data_dir = install / "data"
        _migrate_legacy(legacy, data_dir)
        return RuntimeDirs(
            data=data_dir,
            logs=install / "logs",
            user_config=install / "config",
        )
    # 安装目录只读（Program Files 等）：沿用 0.1.0 用户目录布局
    return RuntimeDirs(data=legacy, logs=legacy / "logs", user_config=legacy / "config")


def _migrate_legacy(legacy: Path, data_dir: Path) -> None:
    """旧版用户数据一次性搬入安装目录 data/；搬完改名 .bak 防重复。

    logs/ 不搬（诊断日志随旧目录归档）；任何失败静默放弃，回退等于重新开始。
    """
    if data_dir.exists() or not legacy.is_dir():
        return
    try:
        data_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(legacy, data_dir, ignore=shutil.ignore_patterns("logs"))
        legacy.rename(legacy.with_name(f"{legacy.name}.bak-{date.today():%Y%m%d}"))
    except OSError:
        return
