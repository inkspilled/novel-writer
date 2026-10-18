"""工作流定义 — 步骤数据类、内置模板与构建入口。

模板由共享的步骤列表组合而成，避免四份模板互相复制：
- DEFAULT   = 规划步骤 + fix_titles + 逐章循环步骤
- CONTINUE  = fix_titles + 逐章循环步骤
- PLANNING  = 规划步骤
- VALIDATE  = 校验专用小步骤集
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .. import project_io
from ..logger import get_logger

logger = get_logger(__name__)


class WorkflowMode(Enum):
    NEW_BOOK = "new_book"           # 新书全流程：从立意到审校
    NEW_BOOK_PLANNING = "new_book_planning"  # 新书立意：只生成规划文档
    CONTINUE = "continue"           # 续写：从已有章节继续
    FILL_GAPS = "fill_gaps"         # 查漏补缺：检查缺失章节并补写
    VALIDATE = "validate"           # 校验：审核+校对已有章节


@dataclass
class WorkflowStep:
    """工作流步骤定义。"""
    id: str = ""
    needs: str = ""  # 需要的技能名称
    prompt: str = ""  # 指令模板，支持 {变量} 插值
    input_files: list[str] = field(default_factory=list)  # 输入文件列表
    output: str = ""  # 输出文件名，支持 {n} 占位
    repeat: int = 0  # 循环次数（0 = 不循环）
    every: int = 0  # 每隔 N 步执行一次
    optional: bool = False  # 找不到匹配 Agent 时跳过
    update_planning: bool = False  # 强制覆盖 planning/ 文件（用于反哺步骤）
    parallel_ok: bool = False  # 可与相邻 parallel_ok 步骤并发执行
    depends_on: list[str] = field(default_factory=list)  # DAG 依赖（声明后启用拓扑执行）
    # off=关闭思考（工具类步骤省 token/提速）；on=强制开启；auto=模型默认
    thinking: str = "auto"
    retry: int = 0  # 失败重试次数（不含首次）
    retry_delay: float = 2.0  # 重试基础退避秒数（指数）
    timeout: float = 0.0  # 单步超时秒数，0=不限
    max_tokens: int = 0  # 本步输出上限，0=用 Agent 默认（4096）
    max_chapters: int = 0  # 细纲类步骤一次最多生成多少章，0=全部
    tools: list[str] = field(default_factory=list)  # 本步可用的只读工具白名单
    tool_rounds: int = 2  # 工具调用最大轮数（0=不给工具）
    skills: list[str] = field(default_factory=list)  # 直接注入上下文的技能包
    phase: str = ""  # 阶段名（日志/进度分组）


@dataclass
class WorkflowDef:
    """工作流定义。"""
    name: str = ""
    description: str = ""
    # 项目信息（用于变量插值）
    project: dict = field(default_factory=dict)
    steps: list[WorkflowStep] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> WorkflowDef:
        steps = []
        for s in data.get("steps", []):
            steps.append(WorkflowStep(
                id=s.get("id", ""),
                needs=s.get("needs", ""),
                prompt=s.get("prompt", ""),
                input_files=s.get("input", []),
                output=s.get("output", ""),
                repeat=s.get("repeat", 0),
                every=s.get("every", 0),
                optional=s.get("optional", False),
                update_planning=s.get("update_planning", False),
                parallel_ok=s.get("parallel_ok", False),
                depends_on=s.get("depends_on", []) or [],
                thinking=s.get("thinking", "auto"),
                retry=s.get("retry", 0),
                retry_delay=s.get("retry_delay", 2.0),
                timeout=s.get("timeout", 0.0),
                max_tokens=s.get("max_tokens", 0),
                max_chapters=s.get("max_chapters", 0),
                tools=s.get("tools", []) or [],
                tool_rounds=s.get("tool_rounds", 2),
                skills=s.get("skills", []) or [],
                phase=s.get("phase", ""),
            ))
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            project=data.get("project", {}),
            steps=steps,
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "project": self.project,
            "steps": [
                {
                    "id": s.id,
                    "needs": s.needs,
                    "prompt": s.prompt,
                    "input": s.input_files,
                    "output": s.output,
                    "repeat": s.repeat,
                    "every": s.every,
                    "optional": s.optional,
                    "update_planning": s.update_planning,
                    "parallel_ok": s.parallel_ok,
                    "depends_on": s.depends_on,
                    "thinking": s.thinking,
                    "retry": s.retry,
                    "retry_delay": s.retry_delay,
                    "timeout": s.timeout,
                    "max_tokens": s.max_tokens,
                    "max_chapters": s.max_chapters,
                    "tools": s.tools,
                    "tool_rounds": s.tool_rounds,
                    "skills": s.skills,
                    "phase": s.phase,
                }
                for s in self.steps
            ],
        }


# ── 模板步骤（单一来源，按需组合） ──

_CHAPTER_OUTLINE_STEP = {
    "id": "chapter_outline", "needs": "故事结构", "prompt": "",
    "output": "planning/章节细纲.md", "thinking": "off",
}

_PLANNING_STEPS = [
    {"id": "ideation", "needs": "立意规划", "prompt": "为一部{genre}题材的{style}风格小说确定核心立意、目标读者、卖点。书名：{title}。总字数1500字以内，只输出最终文档。", "output": "planning/立意.md", "max_tokens": 2400, "thinking": "off"},
    {"id": "outline", "needs": "故事结构", "prompt": "根据立意设计故事大纲。总篇幅{target_chapters}章，分5卷。只写5卷卷纲：每卷3-5个阶段/剧情块，写清核心冲突和卷末转折；不要写具体章节号，不要逐章展开。总字数1800字以内，只输出大纲正文，不要思考过程。", "input": ["planning/立意.md"], "output": "planning/大纲.md", "max_tokens": 3000, "thinking": "off", "retry": 1, "phase": "规划"},
    {"id": "characters", "needs": "人物设定", "prompt": "根据大纲设计主要人物档案（性格、背景、成长弧线、关系）。只列8-12个核心角色，每人120字内，总字数2500字以内。", "input": ["planning/大纲.md"], "output": "planning/人物设定.md", "max_tokens": 3200, "thinking": "off"},
    {"id": "world", "needs": "世界观构建", "prompt": "根据大纲构建世界观设定，分6-8个小节，总字数2500字以内。", "input": ["planning/大纲.md", "planning/人物设定.md"], "output": "planning/世界观.md", "max_tokens": 3200, "thinking": "off"},
    {"id": "timeline", "needs": "故事结构", "prompt": "根据大纲设计故事时间线，按5卷列关键节点，总字数2500字以内。", "input": ["planning/大纲.md", "planning/人物设定.md", "planning/世界观.md"], "output": "planning/时间线.md", "max_tokens": 3000, "thinking": "off"},
    {"id": "main_plot", "needs": "故事结构", "prompt": "梳理主线剧情脉络，每卷5-8个转折，标注对应章节范围，总字数2200字以内。", "input": ["planning/大纲.md", "planning/人物设定.md", "planning/世界观.md", "planning/立意.md"], "output": "planning/主线.md", "max_tokens": 2800, "thinking": "off"},
    {"id": "sub_plot", "needs": "故事结构", "prompt": "梳理5-8条支线，说明与主线的交汇点，总字数2200字以内。", "input": ["planning/大纲.md", "planning/主线.md", "planning/立意.md"], "output": "planning/支线.md", "max_tokens": 2600, "thinking": "off"},
    {"id": "foreshadow", "needs": "伏笔设计", "prompt": "设计20-30条伏笔，标注埋设/回收章节，总字数2200字以内。", "input": ["planning/大纲.md", "planning/主线.md", "planning/支线.md", "planning/立意.md"], "output": "planning/伏笔.md", "max_tokens": 2600, "thinking": "off"},
    _CHAPTER_OUTLINE_STEP,
]

_FIX_TITLES_STEP = {"id": "fix_titles", "needs": "", "prompt": "", "output": "", "optional": True}

# 逐章循环步骤（章前定时 + 正文写作 + 章后定时沉淀 + 全书审校）
# thinking: 工具/抽取类步骤 off（省 token、免长考）；创作/审稿类 auto/on
_CYCLE_STEPS = [
    {"id": "inspiration", "needs": "灵感激发", "prompt": "基于当前剧情进展，提供3个意想不到的转折方向，为下一章提供创作灵感。", "input": ["prev_chapters"], "output": "inspiration/{n}_灵感.md", "every": 3, "optional": True, "thinking": "off", "tools": ["search_chapters", "get_open_loops", "list_skills", "use_skill"], "tool_rounds": 1, "phase": "灵感"},
    {"id": "sim", "needs": "角色推演", "prompt": "根据人物设定和大纲，推演第{n}章中各角色在当前冲突下的自然反应。输出JSON格式的推演结果。", "input": ["planning/大纲.md", "planning/人物设定.md", "prev_chapters"], "output": "sim_cache/sim_{n}.md", "every": 1, "optional": True, "thinking": "on", "tools": ["read_chapter", "get_world_state", "get_memory", "get_open_loops"], "tool_rounds": 1, "phase": "推演"},
    {"id": "character_panel", "needs": "剧情审查", "prompt": "", "output": "", "every": 1, "optional": True, "thinking": "off", "phase": "角色会审"},
    {"id": "chapter", "needs": "正文写作", "prompt": "严格按【本章细纲】写第{n}章正文；细纲只是骨架，可丰富场景与对话，但不得改写事件走向。严格遵守【写作约束·角色锚定】中的规则：主角不得更换，角色名不得擅改，新人物不得无铺垫登场。参考【本章大纲】【灵感】和【角色推演】来推进剧情。保持与前文连贯。", "input": ["planning/章节细纲.md", "planning/大纲.md", "planning/人物设定.md", "planning/世界观.md", "planning/时间线.md", "planning/主线.md", "planning/支线.md", "planning/伏笔.md", "prev_chapters"], "output": "chapters/{n}_chapter.txt", "max_tokens": 8192, "thinking": "off", "skills": ["chapter-craft"], "tools": ["read_planning", "read_chapter", "search_chapters", "get_world_state", "get_memory", "get_open_loops", "list_skills", "use_skill"], "tool_rounds": 2, "retry": 1, "phase": "写作"},
    {"id": "chapter_summary", "needs": "", "prompt": "", "output": "", "every": 1, "optional": True, "thinking": "off"},
    {"id": "world_state_update", "needs": "", "prompt": "", "output": "", "every": 1, "optional": True, "thinking": "off"},
    {"id": "toc", "needs": "", "prompt": "", "output": "", "every": 1, "optional": True, "thinking": "off"},
    {"id": "polish", "needs": "润色", "prompt": "润色第{n}章正文，提升文笔质量、场景描写、对话自然度和情感表达。保持原有风格，只做锦上添花。", "input": ["chapters/{n}_chapter.txt"], "output": "chapters/{n}_chapter.txt", "every": 2, "optional": True, "max_tokens": 8192, "thinking": "off", "skills": ["continuity"], "tools": ["read_chapter", "read_planning", "search_chapters", "get_world_state"], "tool_rounds": 1, "retry": 1, "phase": "润色"},
    {"id": "percept_extract", "needs": "剧情审查", "prompt": "", "output": "", "every": 1, "optional": True, "thinking": "off", "phase": "感知抽取"},
    {"id": "seal_chapter", "needs": "", "prompt": "", "output": "", "every": 1, "optional": True, "thinking": "off", "phase": "封印"},
    {"id": "offscreen_tick", "needs": "剧情审查", "prompt": "", "output": "", "every": 5, "optional": True, "thinking": "off", "phase": "离场tick"},
    {"id": "proofread", "needs": "错别字检查", "prompt": "校对第{n}章的错别字、语法、标点。列出发现的问题和修改建议。", "input": ["chapters/{n}_chapter.txt"], "output": "review/校对报告_第{n}章.md", "every": 1, "optional": True, "thinking": "off"},
    {"id": "summary", "needs": "剧情摘要", "prompt": "将前{n}章的剧情浓缩为一份结构化摘要。格式要求：\n## 剧情摘要\n（150字内，只写关键转折）\n## 角色状态\n- 角色名: 当前状态/位置/实力\n## 伏笔\n- [埋设] 伏笔描述（第X章）\n- [回收] 伏笔描述（第X章）\n## 未解悬念\n- 悬念描述\n## 承接点\n（30字内，下一章应从哪里接）", "input": ["prev_chapters", "planning/人物设定.md"], "output": "summary/第1-{n}章摘要.md", "every": 5, "optional": True, "thinking": "off"},
    {"id": "update_outline", "needs": "故事结构", "prompt": "根据前{n}章的实际内容，更新故事大纲。保留原有结构，补充实际发生的剧情走向、新增的支线、调整后的节奏。输出完整的新大纲。", "input": ["planning/大纲.md", "prev_chapters"], "output": "planning/大纲.md", "every": 5, "optional": True, "update_planning": True, "thinking": "off"},
    {"id": "update_characters", "needs": "人物设定", "prompt": "根据前{n}章的实际内容，更新人物设定。补充角色的实际成长变化、新增的关系、性格变化。保留原有设定，在末尾追加【进展更新】章节。", "input": ["planning/人物设定.md", "prev_chapters"], "output": "planning/人物设定.md", "every": 5, "optional": True, "update_planning": True, "thinking": "off"},
    {"id": "update_foreshadow", "needs": "伏笔设计", "prompt": "根据前{n}章的实际内容，更新伏笔清单。标注哪些伏笔已回收、哪些仍悬而未决、新增了哪些伏笔。保留原有清单，在末尾追加【进展更新】。", "input": ["planning/伏笔.md", "prev_chapters"], "output": "planning/伏笔.md", "every": 5, "optional": True, "update_planning": True, "thinking": "off"},
    {"id": "update_chapter_outline", "needs": "故事结构", "prompt": "", "output": "planning/章节细纲.md", "every": 5, "optional": True, "update_planning": True, "thinking": "off"},
    {"id": "review_panel", "needs": "剧情审查", "prompt": "", "output": "review/会审报告.md", "every": 10, "optional": True, "thinking": "off", "phase": "会审"},
    {"id": "quality_check", "needs": "", "prompt": "", "output": "review/质量检查报告.md", "every": 1, "optional": True, "thinking": "off"},
    {"id": "review", "needs": "剧情审查", "prompt": "审查全部章节的剧情逻辑、人物一致性、节奏，给出修改建议。", "input": ["planning/*", "chapters/*.txt"], "output": "review/审核报告.md", "thinking": "off"},
]


# ── 内置工作流模板 ──

_PLANNING_CORE_STEPS = _PLANNING_STEPS[:-1]
_CHAPTER_OUTLINE_PRE = {
    **_CHAPTER_OUTLINE_STEP,
    "optional": True,
    "max_chapters": 10,  # 只预生成前 10 章，后续由 update_chapter_outline 每 5 章滚动补齐
    "phase": "细纲",
}

DEFAULT_WORKFLOW = {
    "name": "新书全流程",
    "description": "从立意到审校，正文前滚动生成章节细纲",
    # 细纲在正文之前，但只生成前 10 章，避免 500 章一次性阻塞；
    # 之后由循环里的 update_chapter_outline 每次向前滚 10 章。
    "steps": _PLANNING_CORE_STEPS + [_CHAPTER_OUTLINE_PRE, _FIX_TITLES_STEP] + _CYCLE_STEPS,
}

CONTINUE_WORKFLOW = {
    "name": "续写",
    "description": "从已有章节继续写作，正文前滚动生成细纲",
    "steps": [_FIX_TITLES_STEP, _CHAPTER_OUTLINE_PRE] + _CYCLE_STEPS,
}

VALIDATE_WORKFLOW = {
    "name": "校验",
    "description": "审核+校对已有章节",
    "steps": [
        {"id": "fix_titles", "needs": "", "prompt": "", "output": "", "optional": True},
        {"id": "toc", "needs": "", "prompt": "", "output": "", "optional": True},
        {"id": "review", "needs": "剧情审查", "prompt": "审查全部章节的剧情逻辑、人物一致性、节奏，给出修改建议。", "input": ["planning/*", "chapters/*.txt"], "output": "review/审核报告.md"},
        {"id": "proofread", "needs": "错别字检查", "prompt": "校对全部章节的错别字、语法、标点。", "input": ["chapters/*.txt"], "output": "review/校对报告.md"},
    ],
}

PLANNING_WORKFLOW = {
    "name": "新书立意",
    "description": "只生成规划文档，不写正文",
    "steps": _PLANNING_STEPS,
}


def _parse_expected_chapters(project_dir: str) -> int | None:
    """从规划方向解析预期总章数。

    扫描 planning/立意.md 中的分卷描述，如「速成上路(30章)→虚浮翻车(35章)→我自为道(35章)」，
    提取各卷章数求和。找不到或解析失败返回 None。
    """
    if not project_dir:
        return None
    try:
        ideation = Path(project_dir) / "planning" / "立意.md"
        if not ideation.exists():
            return None
        text = ideation.read_text(encoding="utf-8")
        import re
        # 优先解析「【分卷】…(30)→…(40)→…(30)」：括号里常省略「章」字
        volume = re.search(r"【分卷】([^\n]+)", text)
        if volume:
            nums = re.findall(r"[（(](\d{1,4})\s*章?[）)]", volume.group(1))
            if nums:
                total = sum(int(n) for n in nums)
                if 5 <= total <= 5000:
                    return total
        # 回退：全文中带「章」的括号（N章）
        matches = re.findall(r"[（(](\d+)\s*章[）)]", text)
        if matches:
            total = sum(int(m) for m in matches)
            if 5 <= total <= 5000:
                return total
        # 也匹配「共N章」「目标N章」
        m = re.search(r"(?:共|目标)\s*(\d+)\s*章", text)
        if m:
            total = int(m.group(1))
            if 5 <= total <= 5000:
                return total
    except Exception:
        pass
    return None


def build_workflow(
    mode: WorkflowMode,
    project_info: dict,
    start_chapter: int = 1,
    end_chapter: int = 100,
) -> WorkflowDef:
    """根据模式构建工作流定义。

    Args:
        mode: 工作流模式
        project_info: 项目信息
        start_chapter: 起始章节号（续写模式用）
        end_chapter: 结束章节号
    """
    if mode == WorkflowMode.NEW_BOOK:
        data = json.loads(json.dumps(DEFAULT_WORKFLOW))
    elif mode == WorkflowMode.NEW_BOOK_PLANNING:
        data = json.loads(json.dumps(PLANNING_WORKFLOW))
    elif mode == WorkflowMode.CONTINUE:
        data = json.loads(json.dumps(CONTINUE_WORKFLOW))
    elif mode == WorkflowMode.VALIDATE:
        data = json.loads(json.dumps(VALIDATE_WORKFLOW))
    elif mode == WorkflowMode.FILL_GAPS:
        # 查漏补缺：扫描缺失或空章节，生成写作步骤
        existing: dict[int, bool] = {}
        if project_info.get("_project_dir"):
            ch_dir = Path(project_info["_project_dir"]) / project_io.CHAPTERS_DIR
            if ch_dir.exists():
                for f in ch_dir.iterdir():
                    m = project_io._CHAPTER_RE.match(f.name)
                    if m:
                        num = int(m.group(1))
                        # 空文件也算缺失
                        existing[num] = f.stat().st_size > 0
        # 找出缺失或空的章节
        gap_steps = []
        for n in range(start_chapter, end_chapter + 1):
            if n not in existing or not existing[n]:
                gap_steps.append(n)
        data = {"name": "查漏补缺", "description": f"补写 {len(gap_steps)} 个缺失章节", "steps": [
            {"id": "chapter", "needs": "正文写作",
             "prompt": "根据大纲和前文写第{n}章正文。严格遵守【写作约束·角色锚定】中的规则：主角不得更换，角色名不得擅改，新人物不得无铺垫登场。保持与前文连贯。",
             "input": ["planning/大纲.md", "planning/人物设定.md", "prev_chapters"],
             "output": "chapters/{n}_chapter.txt", "repeat": end_chapter},
        ]}
        # 标记缺失章节
        data["_gaps"] = gap_steps
    else:
        data = json.loads(json.dumps(DEFAULT_WORKFLOW))

    # 设置项目信息
    data["project"] = {
        "title": project_info.get("title", ""),
        "genre": project_info.get("genre", ""),
        "style": project_info.get("style", ""),
        "target_chapters": end_chapter,
    }
    # 从规划方向解析预期总章数，与 end_chapter 对不上时以规划为准（并告警）
    expected = _parse_expected_chapters(project_info.get("_project_dir", ""))
    if expected and abs(expected - end_chapter) > max(5, expected * 0.15):
        logger.warning("目标章节数 %d 与规划方向预期 %d 章差异较大，以规划方向为准", end_chapter, expected)
        end_chapter = expected
        data["project"]["target_chapters"] = expected
    # 查漏补缺模式：记录缺失章节
    if mode == WorkflowMode.FILL_GAPS and "_gaps" in data:
        data["project"]["_gaps"] = data.pop("_gaps")

    # 为需要循环的步骤设置 repeat
    for step in data.get("steps", []):
        if step.get("id") == "chapter":
            step["repeat"] = end_chapter
            # 续写模式：prompt 中提示从第几章开始
            if mode == WorkflowMode.CONTINUE:
                step["prompt"] = f"根据大纲和前文写第{{n}}章正文。这是第{start_chapter}章到第{end_chapter}章的续写部分。严格遵守【写作约束·角色锚定】中的规则：主角不得更换，角色名不得擅改，新人物不得无铺垫登场。保持与前文连贯。"

    wf = WorkflowDef.from_dict(data)
    # 续写模式：跳过已完成的章节
    if mode == WorkflowMode.CONTINUE:
        wf.project["_start_chapter"] = start_chapter
    logger.info("构建工作流: mode=%s, steps=%d, chapters=%d-%d", mode.value, len(wf.steps), start_chapter, end_chapter)
    return wf
