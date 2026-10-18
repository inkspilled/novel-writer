"""项目目录 IO 操作。

目录结构:
    {project_dir}/
    ├── meta.json
    ├── cover.png
    ├── memory.json          # 记忆暂存器（11桶）
    ├── anti_patterns.json   # 反模式追踪
    ├── reading_power.json   # 追读力数据
    ├── rag_index.json       # BM25 检索索引
    ├── workflow.json
    ├── planning/
    │   ├── 立意.md
    │   ├── 大纲.md
    │   ├── 人物设定.md
    │   └── ...
    ├── chapters/
    ├── summary/             # 结构化剧情摘要
    ├── inspiration/
    ├── review/
    └── sim_cache/           # 角色推演缓存
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)


# ── 固定目录名 ──

PLANNING_DIR = "planning"
CHAPTERS_DIR = "chapters"
INSPIRATION_DIR = "inspiration"
REVIEW_DIR = "review"
SUMMARY_DIR = "summary"
META_FILE = "meta.json"
WORKFLOW_FILE = "workflow.json"

# planning/ 下的固定文件名
PLANNING_FILES = [
    "立意.md", "大纲.md", "章节细纲.md", "人物设定.md", "世界观.md",
    "时间线.md", "主线.md", "支线.md", "伏笔.md",
]


# ── 目录创建 ──

def init_project_dir(project_dir: Path) -> None:
    """创建项目的目录骨架。"""
    for sub in (PLANNING_DIR, CHAPTERS_DIR, INSPIRATION_DIR, REVIEW_DIR, SUMMARY_DIR):
        (project_dir / sub).mkdir(parents=True, exist_ok=True)


# ── 目录.md 生成 ──

def generate_toc(project_dir: Path) -> str:
    """根据 chapters/ 下的文件生成目录 markdown 并写入 planning/目录.md。"""
    chapters = scan_chapters(project_dir)
    if not chapters:
        return ""

    lines = [f"# 目录\n"]
    for ch in chapters:
        # 统计字数
        content = read_md(ch["content_path"])
        word_count = len(content.replace(" ", "").replace("\n", ""))
        # 去掉内容中的标题行，避免重复
        lines.append(f"- **第{ch['number']}章 {ch['title']}**（{word_count}字）")

    toc_text = "\n".join(lines) + "\n"
    write_md(project_dir / PLANNING_DIR / "目录.md", toc_text)
    return toc_text


def find_chapter_file(project_dir: Path, n: int) -> str | None:
    """查找第 n 章的已有文件名，找到则返回文件名（不含目录）。"""
    chapters_dir = project_dir / CHAPTERS_DIR
    if not chapters_dir.exists():
        return None
    for f in chapters_dir.iterdir():
        if not f.is_file():
            continue
        m = _CHAPTER_RE.match(f.name)
        if m and int(m.group(1)) == n:
            return f.name
    return None


def rename_chapter(project_dir: Path, chapter_number: int, new_title: str) -> str:
    """重命名单个章节：改文件名 + 改正文 heading。返回旧标题。"""
    import re as _re
    chapters = scan_chapters(project_dir)
    for ch in chapters:
        if ch["number"] == chapter_number:
            old_title = ch["title"]
            if old_title == new_title:
                return old_title

            old_path = ch["content_path"]

            # 1. 读取原始内容（在重命名之前）
            content = read_md(old_path)

            # 2. 改文件名
            new_filename = chapter_filename(chapter_number, new_title)
            new_path = old_path.parent / new_filename
            if old_path != new_path:
                old_path.rename(new_path)

            # 3. 改正文 heading（仅在内容非空时写回）
            if content.strip():
                lines = content.split("\n")
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        m = _re.match(r"^#{1,3}\s+(.+)$", stripped)
                        if m:
                            level = len(stripped) - len(stripped.lstrip("#"))
                            lines[i] = f"{'#' * level} {new_title}"
                        else:
                            lines[i] = f"# {new_title}"
                        break
                new_content = "\n".join(lines)
                if new_content.strip():
                    write_md(new_path, new_content)
                else:
                    logger.error("rename_chapter: 修改后内容为空，跳过写入! chapter=%d", chapter_number)

            # 3. 同步改细纲文件名（如果有）
            if ch["outline_path"]:
                old_outline = ch["outline_path"]
                new_outline_name = chapter_outline_filename(chapter_number, new_title)
                new_outline_path = old_outline.parent / new_outline_name
                if old_outline != new_outline_path:
                    old_outline.rename(new_outline_path)

            # 4. 同步改概要文件名（如果有）
            old_summary = old_path.parent / chapter_summary_filename(chapter_number, old_title)
            if old_summary.exists():
                new_summary_name = chapter_summary_filename(chapter_number, new_title)
                new_summary_path = old_summary.parent / new_summary_name
                if old_summary != new_summary_path:
                    old_summary.rename(new_summary_path)

            logger.info("重命名第%d章: 「%s」→「%s」", chapter_number, old_title, new_title)
            return old_title
    return ""


# ── 章节概要 ──

def chapter_summary_filename(number: int, title: str) -> str:
    safe_title = title or "未命名"
    return f"{number}_{safe_title}.summary.md"


def chapter_summary_path(project_dir: Path, number: int, title: str) -> Path:
    return project_dir / CHAPTERS_DIR / chapter_summary_filename(number, title)


def load_chapter_summaries(project_dir: Path, up_to_chapter: int = 9999, window: int = 0) -> str:
    """加载章节概要文本，用于上下文组装。

    Args:
        project_dir: 项目目录
        up_to_chapter: 最大章节号（不含）
        window: 只加载最近 N 章的概要（0 = 全部）
    """
    chapters_dir = project_dir / CHAPTERS_DIR
    if not chapters_dir.exists():
        return ""

    summaries = []
    for f in sorted(chapters_dir.glob("*.summary.md")):
        m = _CHAPTER_RE.match(f.name.replace(".summary.md", ".txt"))
        if not m:
            continue
        num = int(m.group(1))
        if num >= up_to_chapter:
            continue
        content = read_md(f)
        if content:
            summaries.append((num, content))

    if window and len(summaries) > window:
        summaries = summaries[-window:]

    if not summaries:
        return ""

    parts = ["=== 章节概要 ==="]
    for num, text in summaries:
        parts.append(text)
    return "\n\n".join(parts)


# ── meta.json 读写 ──

def save_meta(project_dir: Path, meta: dict) -> None:
    write_json(project_dir / META_FILE, meta)


def load_meta(project_dir: Path) -> dict:
    path = project_dir / META_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("meta.json 损坏或不可读 (%s): %s", path, e)
        return {}


# ── MD 文件读写 ──

def read_md(path: Path) -> str:
    """读取 MD 文件，不存在返回空字符串。"""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def write_json(path: Path, data, indent: int = 2) -> None:
    """原子写入 JSON：先写临时文件再 rename，防止崩溃留下半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def write_md(path: Path, content: str) -> None:
    """原子写入 MD 文件：先写临时文件，再重命名，防止写入中途崩溃导致文件清空。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)  # 原子操作（同文件系统下）
    except Exception:
        # 清理临时文件
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def normalize_chapter_content(content: str) -> str:
    """规范化章节内容：将 3 个以上连续换行压缩为 2 个（即段落间只保留 1 个空行）。"""
    return re.sub(r'\n{3,}', '\n\n', content)


def validate_chapter_content(content: str, min_length: int = 100) -> tuple[bool, str]:
    """校验章节内容是否有效。

    Returns:
        (is_valid, reason): is_valid=False 时应拒绝写入
    """
    if not content:
        return False, "内容为空"
    stripped = content.strip()
    if not stripped:
        return False, "内容为空白"
    # 去掉 Markdown 标题后检查正文长度
    body = re.sub(r'^#{1,3}\s+.*$', '', stripped, flags=re.MULTILINE).strip()
    if len(body) < min_length:
        return False, f"正文过短（{len(body)}字 < {min_length}字），可能只有标题"
    return True, "OK"


def safe_rename_chapter(project_dir: Path, chapter_number: int, new_title: str) -> tuple[str, str]:
    """安全重命名单个章节：改文件名 + 改正文 heading + 同步细纲和概要。

    与 rename_chapter 的区别：
    - 检测新文件名是否已存在（冲突），冲突时返回错误而非覆盖
    - 返回 (旧标题, 错误信息)，错误为空表示成功
    """
    # 格式化标题：确保包含「第X章：」前缀
    if not new_title.startswith(f"第{_cn_number(chapter_number)}章") and not new_title.startswith(f"第{chapter_number}章"):
        formatted_title = f"第{_cn_number(chapter_number)}章：{new_title}"
    else:
        formatted_title = new_title

    chapters = scan_chapters(project_dir)
    for ch in chapters:
        if ch["number"] == chapter_number:
            old_title = ch["title"]
            if old_title == formatted_title:
                return old_title, ""

            new_filename = chapter_filename(chapter_number, formatted_title)
            new_path = ch["content_path"].parent / new_filename

            # 冲突检测：新文件已存在且不是当前文件
            if new_path.exists() and new_path != ch["content_path"]:
                return old_title, f"目标文件已存在: {new_filename}"

            old_path = ch["content_path"]

            # 读取原始内容（在重命名之前，避免 rename 后读取异常）
            content = read_md(old_path)

            # 改文件名
            if old_path != new_path:
                old_path.rename(new_path)

            # 改正文 heading（仅在内容非空时写回）
            if content.strip():
                lines = content.split("\n")
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        m = re.match(r"^#{1,3}\s+(.+)$", stripped)
                        if m:
                            level = len(stripped) - len(stripped.lstrip("#"))
                            lines[i] = f"{'#' * level} {formatted_title}"
                        else:
                            lines[i] = f"# {formatted_title}"
                        break
                new_content = "\n".join(lines)
                if new_content.strip():
                    write_md(new_path, new_content)
                else:
                    logger.error("safe_rename_chapter: 修改后内容为空，跳过写入! chapter=%d", chapter_number)

            # 同步改细纲文件名（如果有）
            if ch["outline_path"]:
                old_outline = ch["outline_path"]
                new_outline_name = chapter_outline_filename(chapter_number, formatted_title)
                new_outline_path = old_outline.parent / new_outline_name
                if old_outline != new_outline_path:
                    old_outline.rename(new_outline_path)

            # 同步改概要文件名（如果有）
            old_summary = old_path.parent / chapter_summary_filename(chapter_number, old_title)
            if old_summary.exists():
                new_summary_name = chapter_summary_filename(chapter_number, formatted_title)
                new_summary_path = old_summary.parent / new_summary_name
                if old_summary != new_summary_path:
                    old_summary.rename(new_summary_path)

            return old_title, ""
    return "", f"第{chapter_number}章不存在"


def _cn_number(n: int) -> str:
    """将数字转换为中文数字。"""
    cn = "一二三四五六七八九十"
    if n <= 10:
        return cn[n-1]
    elif n < 20:
        return f"十{cn[n-11]}" if n > 10 else "十"
    elif n < 100:
        return f"{cn[n//10-1]}十{cn[n%10-1]}" if n % 10 != 0 else f"{cn[n//10-1]}十"
    else:
        return str(n)


# ── Planning 文件 ──

# ── 章节文件（正文用 .txt，细纲用 .md） ──

# 章节文件名："3_黎明前夜.txt"。负向断言排除写前备份 "3_黎明前夜.bak.txt"——
# 否则 iterdir/glob 的第一个命中可能是备份，润色/续写/上下文都会读错文件
_CHAPTER_RE = re.compile(r"^(?!.*\.bak\.txt$)(\d+)_(.+)\.txt$")


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

def inspiration_filename(number: int, title: str) -> str:
    safe_title = title or "灵感"
    return f"{number}_{safe_title}.md"


# ── Summary 文件 ──

def latest_summary(project_dir: Path) -> str:
    """读取 summary/ 目录下最新的摘要文件内容（按覆盖到的最大章号排序，而非文件名字典序）。"""
    sum_dir = project_dir / SUMMARY_DIR
    if not sum_dir.exists():
        return ""
    files = list(sum_dir.glob("第*章摘要.md"))
    if not files:
        return ""

    def _end_chapter(path: Path) -> int:
        m = re.search(r"第1-(\d+)章", path.name)
        return int(m.group(1)) if m else -1

    files.sort(key=_end_chapter, reverse=True)
    return read_md(files[0])


# ── Review 文件 ──

# ── Workflow 进度 ──

def load_workflow(project_dir: Path) -> dict:
    path = project_dir / WORKFLOW_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("workflow.json 损坏或不可读: %s", e)
        return {}


def save_workflow(project_dir: Path, data: dict) -> None:
    """合并写入 workflow.json：runner 存进度时不能抹掉已保存的 workflow 定义。"""
    path = project_dir / WORKFLOW_FILE
    merged: dict = {}
    if path.exists():
        try:
            merged = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(merged, dict):
                merged = {}
        except (json.JSONDecodeError, OSError):
            merged = {}
    merged.update(data)
    write_json(path, merged)


# ── 项目目录扫描 ──

def list_projects(projects_root: Path) -> list[dict]:
    """扫描项目根目录，返回所有项目的简要信息。"""
    if not projects_root.exists():
        return []
    result = []

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    for d in sorted(projects_root.iterdir(), key=_mtime, reverse=True):
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


def delete_project(project_dir: Path) -> bool:
    """删除整个项目目录。成功返回 True；权限冲突重试一次后仍失败返回 False。"""
    import shutil
    import time
    if not project_dir.exists():
        return True
    try:
        shutil.rmtree(project_dir)
        return True
    except PermissionError:
        # 文件被锁定，等待后重试
        time.sleep(0.5)
        try:
            shutil.rmtree(project_dir)
            return True
        except OSError as e:
            logger.error("删除项目失败 %s: %s", project_dir, e)
            return False
    except OSError as e:
        logger.error("删除项目失败 %s: %s", project_dir, e)
        return False
