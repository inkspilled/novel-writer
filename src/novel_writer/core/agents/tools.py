"""Agent 只读工具集 — 让模型按需拉取小说上下文。

借鉴 deepseek-harness 的工具流水线：模型通过函数调用请求事实，而不是把
整本设定一次性塞进提示词。这里只暴露只读工具（规划/章节/记忆/世界状态/
伏笔/技能），写操作仍由工作流步骤负责，避免模型越权改稿。
"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import project_io
from ..logger import get_logger
from ..memory import MemoryScratchpad
from ..skills import SkillRegistry
from ..world_state import WorldState

logger = get_logger(__name__)

_MAX_RESULT_CHARS = 8000


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表 + 安全执行（参数校验、异常转文本、结果截断）。"""

    def __init__(self, max_result_chars: int = _MAX_RESULT_CHARS):
        self._tools: dict[str, Tool] = {}
        self.max_result_chars = max_result_chars

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"ERROR: 未知工具 {name}；可用工具：{', '.join(self._tools)}"
        try:
            result = tool.handler(args or {})
            if inspect.isawaitable(result):
                result = await result
        except Exception as e:  # 工具错误回给模型，不终止 Agent
            logger.warning("工具 %s 执行失败: %s", name, e)
            return f"ERROR: {type(e).__name__}: {e}"
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        if len(text) > self.max_result_chars:
            text = text[: self.max_result_chars] + "\n…（已截断）"
        return text


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class NovelToolkit:
    """把项目数据包装成只读工具，按步骤白名单暴露。"""

    def __init__(
        self,
        project_dir: Path,
        project_info: dict | None = None,
        chapter_n: int = 0,
        skills: SkillRegistry | None = None,
    ):
        self.project_dir = Path(project_dir)
        self.project_info = project_info or {}
        self.chapter_n = chapter_n
        self.skills = skills or SkillRegistry(self.project_dir)

    # ── 工具实现 ──

    def _read_planning(self, args: dict) -> str:
        raw = str(args.get("doc") or args.get("name") or "").strip()
        name = Path(raw).name
        if name and not name.endswith(".md"):
            name += ".md"
        allowed = set(project_io.PLANNING_FILES) | {"目录.md"}
        if name not in allowed:
            return f"ERROR: 未知规划文档 {raw!r}；可用：{', '.join(sorted(allowed))}"
        text = project_io.read_md(self.project_dir / "planning" / name)
        if not text.strip():
            return f"（{name} 尚未生成或为空）"
        return f"=== {name} ===\n{text[:_MAX_RESULT_CHARS]}"

    def _read_chapter(self, args: dict) -> str:
        n = _as_int(args.get("number") or args.get("n"), 0)
        if n <= 0:
            return "ERROR: number 必须是正整数"
        ch_file = project_io.find_chapter_file(self.project_dir, n)
        if not ch_file:
            return f"（第{n}章尚未写作）"
        text = project_io.read_md(self.project_dir / "chapters" / ch_file)
        title = ch_file.split("_", 1)[-1].rsplit(".txt", 1)[0]
        return f"=== 第{n}章 {title} ===\n{text[:_MAX_RESULT_CHARS]}"

    def _search_chapters(self, args: dict) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return "ERROR: query 不能为空"
        top_k = max(1, min(8, _as_int(args.get("top_k"), 5)))
        try:
            from ..rag import get_retriever
            retriever = get_retriever(self.project_dir)
            retriever.load_chapters(10 ** 9)
            text = retriever.retrieve(query, top_k=top_k)
        except Exception as e:
            return f"ERROR: 检索失败: {e}"
        return text or "（没有检索到相关段落）"

    def _get_world_state(self, args: dict) -> str:
        ws = WorldState(self.project_dir)
        ws.load()
        entity = str(args.get("entity") or "").strip()
        if entity:
            hits: list[str] = []
            for name, info in ws.characters.items():
                if entity in name:
                    hits.append(f"【角色·{name}】{json.dumps(info, ensure_ascii=False)[:1200]}")
            for name, info in ws.items_catalog.items():
                if entity in name:
                    hits.append(f"【物品·{name}】{json.dumps(info, ensure_ascii=False)[:600]}")
            for name, info in ws.world.get("locations", {}).items():
                if entity in name:
                    hits.append(f"【地点·{name}】{json.dumps(info, ensure_ascii=False)[:600]}")
            return "\n".join(hits) if hits else f"（世界状态中没有匹配 {entity!r} 的条目）"
        text = ws.build_context_text()
        return text[:6000] if text else "（世界状态为空）"

    def _get_memory(self, args: dict) -> str:
        bucket = str(args.get("bucket") or "").strip()
        subject = str(args.get("subject") or "").strip()
        mem = MemoryScratchpad(self.project_dir)
        if bucket:
            items = mem.get_active(bucket)
        else:
            items = []
            for b in ("character_state", "story_facts", "open_loops", "reader_promises",
                      "relationships", "resources", "world_rules", "info_boundary"):
                items.extend(mem.get_active(b))
        if subject:
            items = [i for i in items if subject in str(i.get("subject", ""))]
        if not items:
            return "（没有匹配的记忆项）"
        lines = []
        for item in items[:20]:
            lines.append(
                f"[{item.get('category', '')}] {item.get('subject', '')}"
                f"（{item.get('aspect', '')}）: {item.get('value', '')}"
                f" @第{item.get('source_chapter', 0)}章"
            )
        return "\n".join(lines)

    def _get_open_loops(self, args: dict) -> str:
        total = _as_int(self.project_info.get("target_chapters"), 0)
        mem = MemoryScratchpad(self.project_dir)
        loops = mem.get_open_loops(limit=10, current_chapter=self.chapter_n, total_chapters=total)
        if not loops:
            return "（没有未解伏笔）"
        from ..memory import loop_urgency
        lines = []
        for item in loops:
            urgency = loop_urgency(item, self.chapter_n, total)
            mark = " ⚠优先回收" if urgency >= 0.75 else ""
            lines.append(
                f"- {item.get('subject', '')}: {item.get('value', '')}"
                f"（第{item.get('source_chapter', 0)}章埋，紧急度{urgency:.2f}）{mark}"
            )
        return "\n".join(lines)

    def _list_chapters(self, args: dict) -> str:
        chapters = project_io.scan_chapters(self.project_dir)
        if not chapters:
            return "（还没有章节）"
        lines = []
        for ch in chapters[:200]:
            words = len(project_io.read_md(ch["content_path"]).replace(" ", "").replace("\n", ""))
            summary = "有概要" if project_io.chapter_summary_path(
                self.project_dir, ch["number"], ch["title"]
            ).exists() else ""
            lines.append(f"- 第{ch['number']}章 {ch['title']}（{words}字）{summary}")
        return "\n".join(lines)

    def _list_skills(self, args: dict) -> str:
        catalog = self.skills.catalog_text()
        return catalog or "（没有可用技能）"

    def _use_skill(self, args: dict) -> str:
        name = str(args.get("name") or "").strip()
        skill = self.skills.get(name)
        if skill is None:
            return f"ERROR: 未知技能 {name!r}；可用：{', '.join(s.name for s in self.skills.list())}"
        return f"=== 技能：{skill.name} ===\n{skill.body[:6000]}"

    # ── 组装 ──

    def build(self, tool_names: list[str] | None = None) -> ToolRegistry:
        registry = ToolRegistry()
        available = {
            "read_planning": Tool(
                "read_planning",
                "读取项目规划文档（立意/大纲/章节细纲/人物设定/世界观/时间线/主线/支线/伏笔/目录）",
                {
                    "type": "object",
                    "properties": {"doc": {"type": "string", "description": "文档名，如 人物设定 或 大纲.md"}},
                    "required": ["doc"],
                },
                self._read_planning,
            ),
            "read_chapter": Tool(
                "read_chapter",
                "读取指定章节的正文（用于核对前文细节、原句、伏笔）",
                {
                    "type": "object",
                    "properties": {"number": {"type": "integer", "description": "章节号"}},
                    "required": ["number"],
                },
                self._read_chapter,
            ),
            "search_chapters": Tool(
                "search_chapters",
                "按关键词/语义检索历史章节段落，返回最相关的片段与出处",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索词或一句话描述"},
                        "top_k": {"type": "integer", "description": "返回条数，1-8，默认 5"},
                    },
                    "required": ["query"],
                },
                self._search_chapters,
            ),
            "get_world_state": Tool(
                "get_world_state",
                "查询世界状态快照：角色属性/背包、物品图鉴、地点、时间线；可按名字过滤",
                {
                    "type": "object",
                    "properties": {"entity": {"type": "string", "description": "角色/物品/地点名，可留空看全局"}},
                },
                self._get_world_state,
            ),
            "get_memory": Tool(
                "get_memory",
                "查询长期记忆桶（角色状态/剧情事实/关系/资源/信息边界等），可按主题过滤",
                {
                    "type": "object",
                    "properties": {
                        "bucket": {"type": "string", "description": "记忆桶名，留空则查核心桶"},
                        "subject": {"type": "string", "description": "按主题子串过滤"},
                    },
                },
                self._get_memory,
            ),
            "get_open_loops": Tool(
                "get_open_loops",
                "列出未回收伏笔及其紧急度（写作时要留意回收或延续）",
                {"type": "object", "properties": {}},
                self._get_open_loops,
            ),
            "list_chapters": Tool(
                "list_chapters",
                "列出已有章节的章号、标题、字数、是否有概要",
                {"type": "object", "properties": {}},
                self._list_chapters,
            ),
            "list_skills": Tool(
                "list_skills",
                "列出可用写作技能包（工艺指南）",
                {"type": "object", "properties": {}},
                self._list_skills,
            ),
            "use_skill": Tool(
                "use_skill",
                "加载一个写作技能包的完整指南（开篇/对话/战斗/情感/悬念/连续性）",
                {
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "技能名，如 chapter-craft"}},
                    "required": ["name"],
                },
                self._use_skill,
            ),
        }
        names = tool_names or list(available)
        for name in names:
            if name in available:
                registry.register(available[name])
        return registry
