"""架构守护测试 — 依赖方向与分包约定（对标 smt-intelligent）。

约定：
1. `core/` 与 `models/` 不得 import Qt（保持纯 Python，可独立测试）
2. `core/` 不得 import `ui/`
3. `ui/` 根目录只允许 `main_window.py` 与 `__init__.py`（功能页进子包）
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "novel_writer"
UI_ROOT = SRC / "ui"
FORBIDDEN_ROOTS = {"core", "models"}

QT_MARKERS = ("PySide6", "PyQt", "shiboken")


def _iter_py(package: Path):
    for p in package.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def _import_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            # 相对导入看 level+module
            if node.level and node.module:
                names.add("." * node.level + node.module)
            elif node.level:
                names.add("." * node.level)
    return names


class TestCoreStaysQtFree:
    def test_core_and_models_have_no_qt(self):
        offenders = []
        for pkg_name in ("core", "models", "storage"):
            pkg = SRC / pkg_name
            if not pkg.exists():
                continue
            for py in _iter_py(pkg):
                names = _import_names(py)
                for n in names:
                    if any(m in n for m in QT_MARKERS):
                        offenders.append(f"{py.relative_to(SRC)}: {n}")
        assert not offenders, "core/models 不得依赖 Qt:\n" + "\n".join(offenders)

    def test_core_does_not_import_ui(self):
        offenders = []
        for py in _iter_py(SRC / "core"):
            for n in _import_names(py):
                if "ui" in n.split(".") or n.startswith(".ui") or n.startswith("..ui"):
                    # 相对导入 .ui / ..ui
                    if n.lstrip(".").split(".")[0] == "ui":
                        offenders.append(f"{py.relative_to(SRC)}: {n}")
        assert not offenders, "core 不得 import ui:\n" + "\n".join(offenders)


class TestUiRootOnlyMainWindow:
    def test_ui_root_files(self):
        allowed = {"__init__.py", "main_window.py", "__pycache__"}
        extra = []
        for p in UI_ROOT.iterdir():
            if p.name in allowed or p.name.startswith("."):
                continue
            if p.is_dir() and p.name == "__pycache__":
                continue
            extra.append(p.name)
        # 子包目录允许；平铺 .py 文件不允许（除 main_window）
        flat_py = [n for n in extra if n.endswith(".py")]
        assert not flat_py, (
            f"ui/ 根只允许 main_window.py，发现平铺文件: {flat_py}；请移入功能子包"
        )
