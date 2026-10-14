"""UI 模块可导入冒烟测试 — 抓住拆分后漏 import 的 NameError。

需要 PySide6；未安装时跳过。
"""
from __future__ import annotations

import importlib

import pytest

pytest.importorskip("PySide6")

MODULES = [
    "novel_writer.ui.common.styles",
    "novel_writer.ui.common.workers",
    "novel_writer.ui.common.chat_rendering",
    "novel_writer.ui.common.chat_widgets",
    "novel_writer.ui.agent.agent_animation",
    "novel_writer.ui.agent.office_scene",
    "novel_writer.ui.agent.agent_dialog",
    "novel_writer.ui.agent.agent_panel",
    "novel_writer.ui.editor.sidebar",
    "novel_writer.ui.editor.editor_panel",
    "novel_writer.ui.workflow.workflow_bar",
    "novel_writer.ui.workflow.workflow_panel",
    "novel_writer.ui.project.project_dialogs",
    "novel_writer.ui.project.new_project_dialog",
    "novel_writer.ui.settings.model_dialog",
    "novel_writer.ui.settings.appearance_dialog",
    "novel_writer.ui.shell.splash",
    "novel_writer.ui.app_shell.theme_mixin",
    "novel_writer.ui.app_shell.llm_mixin",
    "novel_writer.ui.app_shell.project_mixin",
    "novel_writer.ui.app_shell.chat_mixin",
    "novel_writer.ui.app_shell.settings_mixin",
    "novel_writer.ui.app_shell.workflow_mixin",
    "novel_writer.ui.main_window",
]


@pytest.mark.parametrize("mod", MODULES)
def test_ui_module_importable(mod: str):
    m = importlib.import_module(mod)
    assert m is not None


def test_mainwindow_methods_wired():
    from novel_writer.ui.main_window import MainWindow
    required = [
        "_setup_ui", "_setup_menu", "_apply_theme", "_show_toast",
        "_init_llm", "_init_agents", "_new_project", "_save_project",
        "_on_agent_run", "_build_context", "_workflow_start", "_export",
        "_open_model_settings", "closeEvent",
    ]
    for name in required:
        assert callable(getattr(MainWindow, name, None)), f"MainWindow 缺方法: {name}"


def test_app_shell_no_undefined_names():
    """AST 扫描 Mixin：方法体里的自由名必须已定义（抓拆分漏 import）。"""
    import ast
    import builtins
    from pathlib import Path

    BUILTIN = set(dir(builtins)) | {
        "self", "cls", "annotations", "__class__", "__file__", "__name__",
    }

    def defs_in(node, out):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                out.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.add(node.name)
            args = node.args
            for a in args.args + args.kwonlyargs + args.posonlyargs:
                out.add(a.arg)
            if args.vararg:
                out.add(args.vararg.arg)
            if args.kwarg:
                out.add(args.kwarg.arg)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in node.generators:
                t = gen.target
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        out.add(n.id)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        out.add(n.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
        elif isinstance(node, ast.For):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    out.add(n.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars:
                    for n in ast.walk(item.optional_vars):
                        if isinstance(n, ast.Name):
                            out.add(n.id)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            out.add(node.target.id)

    root = Path(__file__).resolve().parents[1] / "src" / "novel_writer" / "ui"
    targets = list((root / "app_shell").glob("*.py")) + [root / "main_window.py"]
    problems = []
    for py in targets:
        tree = ast.parse(py.read_text(encoding="utf-8"))
        defined = set(BUILTIN)
        for node in ast.walk(tree):
            defs_in(node, defined)
        used = {
            n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        missing = sorted(used - defined)
        if missing:
            problems.append(f"{py.name}: {missing}")
    assert not problems, "发现未定义名（多半是拆分漏 import）:\n" + "\n".join(problems)
