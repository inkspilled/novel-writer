"""技能包（Skill）— 可复用的任务专项指令，按需加载。

借鉴 deepseek-harness 的 skill 设计：
- 技能是带 frontmatter 的 Markdown，目录可发现、正文按需读取（渐进披露）；
- 内置技能随包分发，用户可在 `config/skills/` 或项目 `skills/` 覆盖同名技能；
- 模型通过 `use_skill(name)` 工具拉取正文，避免把全部技能塞进系统提示词。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_NAME_RE = re.compile(r"[^a-z0-9_-]+")


def normalize_skill_name(name: str) -> str:
    """kebab-case 规范化：大小写/空格/下划线统一。"""
    return _NAME_RE.sub("-", (name or "").strip().lower().replace("_", "-")).strip("-")


@dataclass
class Skill:
    name: str
    description: str = ""
    when_to_use: str = ""
    body: str = ""
    source: str = ""
    path: str = ""

    def catalog_line(self) -> str:
        extra = f"（适用：{self.when_to_use}）" if self.when_to_use else ""
        return f"- {self.name}: {self.description}{extra}"


def parse_skill_file(path: Path, source: str = "") -> Skill | None:
    """解析一个技能 Markdown；格式错误返回 None（不阻断其它技能）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("技能读取失败 %s: %s", path, e)
        return None
    meta: dict[str, str] = {}
    body = text
    m = _FRONTMATTER_RE.match(text)
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip().strip('"').strip("'")

    name = normalize_skill_name(meta.get("name", "") or path.stem)
    if not name:
        return None
    body = body.strip()
    if not body:
        return None
    description = meta.get("description", "")
    if not description:
        for line in body.splitlines():
            if line.strip() and not line.startswith("#"):
                description = line.strip()[:120]
                break
    return Skill(
        name=name,
        description=description,
        when_to_use=meta.get("when_to_use", "") or meta.get("when-to-use", ""),
        body=body,
        source=source,
        path=str(path),
    )


class SkillRegistry:
    """按优先级合并：项目 skills/ > config/skills/ > 包内置 skills/。"""

    def __init__(self, project_dir: Path | None = None):
        self.project_dir = Path(project_dir) if project_dir else None
        self._skills: dict[str, Skill] = {}
        self.reload()

    @staticmethod
    def builtin_dir() -> Path:
        return Path(__file__).resolve().parent.parent / "skills"

    def _dirs(self) -> list[tuple[Path, str]]:
        dirs: list[tuple[Path, str]] = []
        if self.project_dir:
            dirs.append((self.project_dir / "skills", "project"))
        try:
            from .app_config import PROJECT_ROOT
            dirs.append((PROJECT_ROOT / "config" / "skills", "config"))
        except Exception:
            pass
        dirs.append((self.builtin_dir(), "builtin"))
        return dirs

    def reload(self) -> None:
        self._skills = {}
        # 反向遍历：低优先级先入，高优先级覆盖
        for directory, source in reversed(self._dirs()):
            if not directory.exists():
                continue
            paths = sorted(directory.glob("*.md"))
            paths += sorted(directory.glob("*/SKILL.md"))
            for path in paths:
                skill = parse_skill_file(path, source=source)
                if skill is None:
                    continue
                self._skills[skill.name] = skill

    def list(self) -> list[Skill]:
        return [self._skills[k] for k in sorted(self._skills)]

    def get(self, name: str) -> Skill | None:
        return self._skills.get(normalize_skill_name(name))

    def catalog_text(self, limit: int = 24) -> str:
        skills = self.list()[:limit]
        if not skills:
            return ""
        return "\n".join(s.catalog_line() for s in skills)

    def select(self, names: list[str]) -> list[Skill]:
        out = []
        for n in names:
            s = self.get(n)
            if s:
                out.append(s)
        return out
