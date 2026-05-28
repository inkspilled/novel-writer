# 工作流编排器设计文档

## 目标

实现自动化写作流程：用户输入书名和题材，编排器自动调度多个智能体协作完成小说创作。

## 核心原则

- **智能体自描述**：每个 Agent 通过 `skills` 字段声明能力，编排器自动匹配
- **技能驱动**：工作流步骤绑定"需要什么技能"，不绑定具体智能体名称
- **新增即生效**：添加新智能体后无需改代码，编排器自动发现新能力
- **文件传递**：步骤之间通过 MD 文件传递上下文

## 架构

```
用户输入（书名 + 题材 + 风格）
         │
         ▼
   ┌─ LLM 编排器（可选）─┐
   │  根据可用智能体动态   │
   │  生成工作流步骤       │
   └──────────┬──────────┘
              ▼
      ┌─ WorkflowRunner ─┐
      │  按步骤顺序执行    │
      │  技能匹配 → 调用  │
      │  文件读写 → 传递  │
      └───────┬──────────┘
              ▼
    Agent A → Agent B → Agent C → ...
    (每个 Agent 独立执行，结果写入文件)
```

## 工作流步骤定义

### JSON 格式

```json
{
  "name": "自动写作",
  "description": "从立意到初稿的全自动流程",
  "project": {
    "title": "小说标题",
    "genre": "玄幻",
    "style": "轻松幽默",
    "target_chapters": 20
  },
  "steps": [
    {
      "id": "ideation",
      "needs": "立意规划",
      "prompt": "为一部{genre}题材的{style}风格小说确定核心立意、目标读者、卖点",
      "output": "00_立意.md"
    },
    {
      "id": "outline",
      "needs": "故事结构",
      "prompt": "根据立意设计完整的故事大纲，包括主要人物、世界观、主线冲突",
      "input": ["00_立意.md"],
      "output": "01_大纲.md"
    },
    {
      "id": "chapter",
      "needs": "正文写作",
      "prompt": "根据大纲写第{n}章正文，保持与前文连贯",
      "input": ["01_大纲.md", "prev_chapters"],
      "output": "第{n}章.md",
      "repeat": "target_chapters"
    },
    {
      "id": "inspiration",
      "needs": "灵感激发",
      "prompt": "基于当前剧情进展，提供3个意想不到的转折方向",
      "input": ["prev_chapters"],
      "output": "灵感_{n}.md",
      "every": 3,
      "optional": true
    },
    {
      "id": "review",
      "needs": "剧情审查",
      "prompt": "审查全部章节的剧情逻辑、人物一致性、节奏",
      "input": ["*.md"],
      "output": "审核报告.md"
    },
    {
      "id": "proofread",
      "needs": "错别字检查",
      "prompt": "校对全部章节的错别字、语法、标点",
      "input": ["*.md"],
      "output": "校对报告.md"
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 步骤唯一标识 |
| `needs` | string | 需要的技能名称，匹配 Agent 的 `skills` |
| `prompt` | string | 给 Agent 的指令模板，支持 `{变量}` 插值 |
| `input` | string[] | 读取的文件列表，`prev_chapters` 为特殊变量 |
| `output` | string | 输出文件名，`{n}` 为章节序号 |
| `repeat` | string/int | 重复执行，值为项目字段名或数字 |
| `every` | int | 每隔 N 步执行一次 |
| `optional` | bool | 找不到匹配 Agent 时跳过而非报错 |

## 编排器实现

### WorkflowRunner

```python
class WorkflowRunner:
    def __init__(self, agents: dict[str, BaseAgent], project_dir: Path, config: dict):
        self.agents = agents
        self.project_dir = project_dir
        self.config = config
        self._agent_by_skill: dict[str, list[BaseAgent]] = {}
        self._build_skill_index()

    def _build_skill_index(self):
        """建立技能 → 智能体的反向索引。"""
        for agent in self.agents.values():
            for skill in agent.config.skills:
                self._agent_by_skill.setdefault(skill, []).append(agent)

    def find_agent(self, skill: str) -> BaseAgent | None:
        """根据技能找到匹配的智能体。"""
        candidates = self._agent_by_skill.get(skill, [])
        return candidates[0] if candidates else None

    async def run(self, workflow: dict, progress_callback=None):
        """执行完整工作流。"""
        project = workflow.get("project", {})
        steps = workflow.get("steps", [])
        total = self._estimate_total(steps, project)

        for i, step in enumerate(steps):
            if step.get("every"):
                # 定时触发步骤
                await self._run_periodic(step, project, i, progress_callback)
            elif step.get("repeat"):
                # 循环步骤（如逐章写作）
                count = project.get(step["repeat"], 1)
                for n in range(1, count + 1):
                    await self._run_single(step, project, n, progress_callback)
            else:
                # 单次步骤
                await self._run_single(step, project, 1, progress_callback)

    async def _run_single(self, step: dict, project: dict, n: int, callback=None):
        """执行单个步骤。"""
        agent = self.find_agent(step["needs"])
        if not agent:
            if step.get("optional"):
                return
            raise WorkflowError(f"没有智能体能做「{step['needs']}」")

        # 构建输入上下文
        context = self._build_context(step.get("input", []), n)
        prompt = self._format_prompt(step["prompt"], project, n)

        # 调用智能体
        response = await agent.run(prompt, context=context)

        # 保存输出
        output = step.get("output", "").format(n=n, **project)
        if output:
            (self.project_dir / output).write_text(response.content, encoding="utf-8")

        if callback:
            callback(step["id"], n, response.content)

    def _build_context(self, input_files: list[str], n: int) -> str:
        """读取输入文件，拼接上下文。"""
        parts = []
        for f in input_files:
            if f == "prev_chapters":
                # 读取前面所有章节
                for i in range(1, n):
                    ch = self.project_dir / f"第{i}章.md"
                    if ch.exists():
                        parts.append(f"=== 第{i}章 ===\n{ch.read_text(encoding='utf-8')}")
            elif "*" in f:
                # 通配符匹配
                for p in sorted(self.project_dir.glob(f)):
                    parts.append(f"=== {p.name} ===\n{p.read_text(encoding='utf-8')}")
            else:
                p = self.project_dir / f.format(n=n)
                if p.exists():
                    parts.append(p.read_text(encoding="utf-8"))
        return "\n\n".join(parts)

    def _format_prompt(self, template: str, project: dict, n: int) -> str:
        """格式化提示词模板。"""
        return template.format(n=n, **project)
```

### LLM 动态编排（可选）

```python
async def generate_workflow(agents: dict, project_info: dict, llm: BaseLLM) -> dict:
    """让 LLM 根据可用智能体动态生成工作流。"""
    available = "\n".join(
        f"- {a.title}（技能：{', '.join(a.config.skills)}）"
        for a in agents.values()
    )

    prompt = f"""你是一个工作流编排器。请为以下小说项目设计自动化写作流程。

项目信息：
- 书名：{project_info['title']}
- 题材：{project_info['genre']}
- 风格：{project_info['style']}
- 目标章节：{project_info.get('target_chapters', 20)}

可用智能体：
{available}

请输出工作流步骤的 JSON 数组，每步包含：
- id: 步骤标识
- needs: 需要的技能（必须匹配某个智能体的 skills）
- prompt: 给该智能体的指令
- input: 需要读取的文件（数组）
- output: 输出文件名
- repeat: 如需循环，填目标章节字段名
- every: 如需定时触发，填间隔步数
- optional: 是否可选

只输出 JSON，不要其他内容。"""

    response = await llm.chat([LLMMessage(role="user", content=prompt)])
    return json.loads(response.content)
```

## 项目目录结构

```
data/projects/{project_name}/
├── 00_立意.md          # 主编输出
├── 01_大纲.md          # 策划输出
├── 02_人物设定.md       # 策划输出
├── 第1章.md            # 写手输出
├── 第2章.md
├── 第3章.md
├── ...
├── 灵感_3.md           # 灵感注入（每3章一次）
├── 灵感_6.md
├── 审核报告.md         # 审核输出
├── 校对报告.md         # 校对输出
└── workflow.json       # 工作流定义（保存当前进度）
```

## 断点恢复

工作流执行过程中保存进度到 `workflow.json`：

```json
{
  "name": "自动写作",
  "progress": {
    "ideation": "done",
    "outline": "done",
    "chapter": {"current": 15, "total": 20},
    "review": "pending"
  }
}
```

编排器启动时检查进度，从断点继续。

## UI 集成

### 工作流面板（新增）

```
┌─ 自动写作 ─────────────────────┐
│                                 │
│  书名：[________________]       │
│  题材：[玄幻      ▼]           │
│  风格：[轻松幽默  ▼]           │
│  章数：[20        ]            │
│                                 │
│  [开始写作]  [暂停]  [继续]     │
│                                 │
│  ── 进度 ──                     │
│  ✅ 立意完成                    │
│  ✅ 大纲完成                    │
│  🔄 写第 15/20 章              │
│  ⬜ 审核                        │
│  ⬜ 校对                        │
│                                 │
└─────────────────────────────────┘
```

### AgentPanel 扩展

在现有 Agent 面板增加"自动写作"按钮，点击弹出工作流配置面板。

## 实现优先级

| 阶段 | 内容 | 复杂度 |
|------|------|--------|
| P0 | WorkflowRunner 基础框架 | 中 |
| P0 | 技能匹配 + 单步执行 | 低 |
| P1 | 循环步骤（逐章写作） | 中 |
| P1 | 文件读写 + 上下文传递 | 低 |
| P2 | 进度保存 + 断点恢复 | 中 |
| P2 | UI 进度面板 | 中 |
| P3 | LLM 动态编排 | 高 |
| P3 | 定时灵感注入 | 低 |
| P4 | 质量门控（审核不过重写） | 高 |

## 新增智能体的扩展方式

1. 在 `config/agents.json` 添加新智能体，配好 `skills`
2. 在工作流模板中添加 `needs: "新技能"` 的步骤
3. 编排器自动匹配，无需改代码

或者使用 LLM 动态编排，连工作流模板都不用改。
