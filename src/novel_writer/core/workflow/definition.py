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
                }
                for s in self.steps
            ],
        }


# ── 模板步骤（单一来源，按需组合） ──

_PLANNING_STEPS = [
    {"id": "ideation", "needs": "立意规划", "prompt": "为一部{genre}题材的{style}风格小说确定核心立意、目标读者、卖点。书名：{title}", "output": "planning/立意.md"},
    {"id": "outline", "needs": "故事结构", "prompt": "根据立意设计完整的故事大纲。注意：必须严格按照【规划方向】中的分卷章数来规划总篇幅（如「30章→35章→35章」即100章），不要被其他数字误导。请将故事分为多个阶段（开篇铺垫、前期发展、中期推进、后期高潮、收束终章），每个阶段标注起止章节和核心剧情。大纲要足够详细，能支撑全部分卷的篇幅，避免剧情提前走完。", "input": ["planning/立意.md"], "output": "planning/大纲.md"},
    {"id": "characters", "needs": "人物设定", "prompt": "根据大纲设计主要人物档案（性格、背景、成长弧线、关系）。", "input": ["planning/大纲.md"], "output": "planning/人物设定.md"},
    {"id": "world", "needs": "世界观构建", "prompt": "根据大纲构建详细的世界观设定。", "input": ["planning/大纲.md", "planning/人物设定.md"], "output": "planning/世界观.md"},
    {"id": "timeline", "needs": "故事结构", "prompt": "根据大纲设计故事时间线。", "input": ["planning/大纲.md", "planning/人物设定.md", "planning/世界观.md"], "output": "planning/时间线.md"},
    {"id": "main_plot", "needs": "故事结构", "prompt": "梳理主线剧情脉络，标注关键转折点和对应章节范围。主线冲突的升级节奏必须匹配立意中规划的分卷篇幅，不要压缩剧情。", "input": ["planning/大纲.md", "planning/人物设定.md", "planning/世界观.md", "planning/立意.md"], "output": "planning/主线.md"},
    {"id": "sub_plot", "needs": "故事结构", "prompt": "梳理支线剧情，说明与主线的交汇点。支线数量和深度必须匹配立意中规划的分卷篇幅，不能提前把所有支线写完。", "input": ["planning/大纲.md", "planning/主线.md", "planning/立意.md"], "output": "planning/支线.md"},
    {"id": "foreshadow", "needs": "伏笔设计", "prompt": "设计伏笔清单：伏笔内容、埋设章节、回收章节。伏笔的埋设和回收要分布在整本书的各卷中，不要全部集中在前几十章。", "input": ["planning/大纲.md", "planning/主线.md", "planning/支线.md", "planning/立意.md"], "output": "planning/伏笔.md"},
]

_FIX_TITLES_STEP = {"id": "fix_titles", "needs": "", "prompt": "", "output": "", "optional": True}

# 逐章循环步骤（章前定时 + 正文写作 + 章后定时沉淀 + 全书审校）
_CYCLE_STEPS = [
    {"id": "inspiration", "needs": "灵感激发", "prompt": "基于当前剧情进展，提供3个意想不到的转折方向，为下一章提供创作灵感。", "input": ["prev_chapters"], "output": "inspiration/{n}_灵感.md", "every": 3, "optional": True},
    {"id": "sim", "needs": "角色推演", "prompt": "根据人物设定和大纲，推演第{n}章中各角色在当前冲突下的自然反应。输出JSON格式的推演结果。", "input": ["planning/大纲.md", "planning/人物设定.md", "prev_chapters"], "output": "sim_cache/sim_{n}.md", "every": 1, "optional": True},
    {"id": "chapter", "needs": "正文写作", "prompt": "根据所有规划文档和前文写第{n}章正文。严格遵守【写作约束·角色锚定】中的规则：主角不得更换，角色名不得擅改，新人物不得无铺垫登场。参考【灵感】和【角色推演】来推进剧情。保持与前文连贯。", "input": ["planning/大纲.md", "planning/人物设定.md", "planning/世界观.md", "planning/时间线.md", "planning/主线.md", "planning/支线.md", "planning/伏笔.md", "prev_chapters"], "output": "chapters/{n}_chapter.txt"},
    {"id": "chapter_summary", "needs": "", "prompt": "", "output": "", "every": 1, "optional": True},
    {"id": "world_state_update", "needs": "", "prompt": "", "output": "", "every": 1, "optional": True},
    {"id": "toc", "needs": "", "prompt": "", "output": "", "every": 1, "optional": True},
    {"id": "polish", "needs": "润色", "prompt": "润色第{n}章正文，提升文笔质量、场景描写、对话自然度和情感表达。保持原有风格，只做锦上添花。", "input": ["chapters/{n}_chapter.txt"], "output": "chapters/{n}_chapter.txt", "every": 2, "optional": True},
    {"id": "proofread", "needs": "错别字检查", "prompt": "校对第{n}章的错别字、语法、标点。列出发现的问题和修改建议。", "input": ["chapters/{n}_chapter.txt"], "output": "review/校对报告_第{n}章.md", "every": 1, "optional": True},
    {"id": "summary", "needs": "剧情摘要", "prompt": "将前{n}章的剧情浓缩为一份结构化摘要。格式要求：\n## 剧情摘要\n（150字内，只写关键转折）\n## 角色状态\n- 角色名: 当前状态/位置/实力\n## 伏笔\n- [埋设] 伏笔描述（第X章）\n- [回收] 伏笔描述（第X章）\n## 未解悬念\n- 悬念描述\n## 承接点\n（30字内，下一章应从哪里接）", "input": ["prev_chapters", "planning/人物设定.md"], "output": "summary/第1-{n}章摘要.md", "every": 5, "optional": True},
    {"id": "update_outline", "needs": "故事结构", "prompt": "根据前{n}章的实际内容，更新故事大纲。保留原有结构，补充实际发生的剧情走向、新增的支线、调整后的节奏。输出完整的新大纲。", "input": ["planning/大纲.md", "prev_chapters"], "output": "planning/大纲.md", "every": 5, "optional": True, "update_planning": True},
    {"id": "update_characters", "needs": "人物设定", "prompt": "根据前{n}章的实际内容，更新人物设定。补充角色的实际成长变化、新增的关系、性格变化。保留原有设定，在末尾追加【进展更新】章节。", "input": ["planning/人物设定.md", "prev_chapters"], "output": "planning/人物设定.md", "every": 5, "optional": True, "update_planning": True},
    {"id": "update_foreshadow", "needs": "伏笔设计", "prompt": "根据前{n}章的实际内容，更新伏笔清单。标注哪些伏笔已回收、哪些仍悬而未决、新增了哪些伏笔。保留原有清单，在末尾追加【进展更新】。", "input": ["planning/伏笔.md", "prev_chapters"], "output": "planning/伏笔.md", "every": 5, "optional": True, "update_planning": True},
    {"id": "quality_check", "needs": "", "prompt": "", "output": "review/质量检查报告.md", "every": 1, "optional": True},
    {"id": "review", "needs": "剧情审查", "prompt": "审查全部章节的剧情逻辑、人物一致性、节奏，给出修改建议。", "input": ["planning/*", "chapters/*.txt"], "output": "review/审核报告.md"},
]


# ── 内置工作流模板 ──

DEFAULT_WORKFLOW = {
    "name": "新书全流程",
    "description": "从立意到审校的全流程",
    "steps": _PLANNING_STEPS + [_FIX_TITLES_STEP] + _CYCLE_STEPS,
}

CONTINUE_WORKFLOW = {
    "name": "续写",
    "description": "从已有章节继续写作",
    "steps": [_FIX_TITLES_STEP] + _CYCLE_STEPS,
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
        # 匹配「(N章)」或（N章）模式，提取所有章数
        import re
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
    total = end_chapter - start_chapter + 1
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
