"""项目目录 IO 操作。

目录结构:
    {project_dir}/
    ├── meta.json
    ├── planning/
    │   ├── 立意.md
    │   ├── 大纲.md
    │   ├── 人物设定.md
    │   ├── 世界观.md
    │   ├── 时间线.md
    │   ├── 主线.md
    │   ├── 支线.md
    │   └── 伏笔.md
    ├── chapters/
    │   ├── 001_章节名.md
    │   ├── 001_章节名.outline.md
    │   └── ...
    ├── inspiration/
    │   └── 001_主题.md
    ├── review/
    │   ├── 审核报告.md
    │   └── 校对报告.md
    └── workflow.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# ── 固定目录名 ──

PLANNING_DIR = "planning"
CHAPTERS_DIR = "chapters"
INSPIRATION_DIR = "inspiration"
REVIEW_DIR = "review"
META_FILE = "meta.json"
WORKFLOW_FILE = "workflow.json"

# planning/ 下的固定文件名
PLANNING_FILES = [
    "立意.md", "大纲.md", "人物设定.md", "世界观.md",
    "时间线.md", "主线.md", "支线.md", "伏笔.md",
]


# ── 目录创建 ──

def init_project_dir(project_dir: Path) -> None:
    """创建项目的目录骨架。"""
    for sub in (PLANNING_DIR, CHAPTERS_DIR, INSPIRATION_DIR, REVIEW_DIR):
        (project_dir / sub).mkdir(parents=True, exist_ok=True)


# ── meta.json 读写 ──

def save_meta(project_dir: Path, meta: dict) -> None:
    path = project_dir / META_FILE
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def load_meta(project_dir: Path) -> dict:
    path = project_dir / META_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# ── MD 文件读写 ──

def read_md(path: Path) -> str:
    """读取 MD 文件，不存在返回空字符串。"""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def write_md(path: Path, content: str) -> None:
    """写入 MD 文件，自动创建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── Planning 文件 ──

def planning_path(project_dir: Path, name: str) -> Path:
    """获取 planning/ 下的文件路径。name 如 '立意', '大纲'。"""
    return project_dir / PLANNING_DIR / f"{name}.md"


def read_planning(project_dir: Path, name: str) -> str:
    return read_md(planning_path(project_dir, name))


def write_planning(project_dir: Path, name: str, content: str) -> None:
    write_md(planning_path(project_dir, name), content)


# ── 章节文件（正文用 .txt，细纲用 .md） ──

_CHAPTER_RE = re.compile(r"^(\d+)_(.+)\.txt$")
_CHAPTER_OUTLINE_RE = re.compile(r"^(\d+)_(.+)\.outline\.md$")


def chapter_filename(number: int, title: str) -> str:
    safe_title = title or "未命名"
    return f"{number}_{safe_title}.txt"


def chapter_outline_filename(number: int, title: str) -> str:
    safe_title = title or "未命名"
    return f"{number}_{safe_title}.outline.md"


def chapter_path(project_dir: Path, number: int, title: str) -> Path:
    return project_dir / CHAPTERS_DIR / chapter_filename(number, title)


def chapter_outline_path(project_dir: Path, number: int, title: str) -> Path:
    return project_dir / CHAPTERS_DIR / chapter_outline_filename(number, title)


def scan_chapters(project_dir: Path) -> list[dict]:
    """扫描 chapters/ 目录，返回章节元数据列表（按序号排序）。

    每项: {"number": int, "title": str, "content_path": Path, "outline_path": Path|None}
    """
    chapters_dir = project_dir / CHAPTERS_DIR
    if not chapters_dir.exists():
        return []

    result = []
    for f in chapters_dir.iterdir():
        if not f.is_file():
            continue
        if f.name.endswith(".outline.md"):
            continue
        m = _CHAPTER_RE.match(f.name)
        if not m:
            continue
        number = int(m.group(1))
        title = m.group(2)
        outline = chapters_dir / chapter_outline_filename(number, title)
        result.append({
            "number": number,
            "title": title,
            "content_path": f,
            "outline_path": outline if outline.exists() else None,
        })
    result.sort(key=lambda x: x["number"])
    return result


# ── 灵感文件 ──

_INSPIRATION_RE = re.compile(r"^(\d+)_(.+)\.md$")


def inspiration_filename(number: int, title: str) -> str:
    safe_title = title or "灵感"
    return f"{number}_{safe_title}.md"


def scan_inspirations(project_dir: Path) -> list[dict]:
    insp_dir = project_dir / INSPIRATION_DIR
    if not insp_dir.exists():
        return []
    result = []
    for f in sorted(insp_dir.iterdir()):
        if not f.is_file():
            continue
        m = _INSPIRATION_RE.match(f.name)
        if not m:
            continue
        result.append({"number": int(m.group(1)), "title": m.group(2), "path": f})
    return result


# ── Review 文件 ──

def review_path(project_dir: Path, name: str) -> Path:
    return project_dir / REVIEW_DIR / f"{name}.md"


def read_review(project_dir: Path, name: str) -> str:
    return read_md(review_path(project_dir, name))


def write_review(project_dir: Path, name: str, content: str) -> None:
    write_md(review_path(project_dir, name), content)


# ── Workflow 进度 ──

def load_workflow(project_dir: Path) -> dict:
    path = project_dir / WORKFLOW_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_workflow(project_dir: Path, data: dict) -> None:
    path = project_dir / WORKFLOW_FILE
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 项目目录扫描 ──

def list_projects(projects_root: Path) -> list[dict]:
    """扫描项目根目录，返回所有项目的简要信息。"""
    if not projects_root.exists():
        return []
    result = []
    for d in sorted(projects_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        meta = load_meta(d)
        if not meta:
            continue
        chapters = scan_chapters(d)
        total_words = 0
        for ch in chapters:
            content = read_md(ch["content_path"])
            total_words += len(content.replace(" ", "").replace("\n", ""))
        result.append({
            "dir": d,
            "title": meta.get("title", d.name),
            "genre": meta.get("genre", ""),
            "chapter_count": len(chapters),
            "total_words": total_words,
        })
    return result


def delete_project(project_dir: Path) -> None:
    """删除整个项目目录。"""
    import shutil
    if project_dir.exists():
        shutil.rmtree(project_dir)
