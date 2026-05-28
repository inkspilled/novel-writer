# 工作流编排器设计文档

## 目标

实现自动化写作流程：用户输入书名和题材，编排器自动调度多个智能体协作完成小说创作。

## 核心原则

- **智能体自描述**：每个 Agent 通过 `skills` 字段声明能力，编排器自动匹配
- **技能驱动**：工作流步骤绑定"需要什么技能"，不绑定具体智能体名称
- **新增即生效**：添加新智能体后无需改代码，编排器自动发现新能力
- **文件传递**：步骤之间通过 MD 文件传递上下文

## 实现状态

| 功能 | 状态 | 说明 |
|------|------|------|
| 技能匹配（needs → skills） | ✅ 已实现 | WorkflowRunner.find_agent() |
| 单步执行 | ✅ 已实现 | run_single_step() |
| 循环步骤（repeat） | ✅ 已实现 | 逐章写作 |
| 定时触发（every） | ✅ 已实现 | 每 N 章灵感注入 |
| 文件输入/输出 | ✅ 已实现 | 读取 MD 文件作为上下文 |
| 模板变量插值 | ✅ 已实现 | {n}, {title}, {genre} 等 |
| 进度回调 | ✅ 已实现 | on_step_start / on_step_end |
| 断点恢复 | ✅ 已实现 | workflow.json 保存进度 |
| UI 进度面板 | ✅ 已实现 | workflow_panel.py 步骤状态、进度条、执行日志 |
| LLM 动态编排 | ✅ 已实现 | generate_workflow() 根据智能体技能自动生成工作流 |
| 质量门控 | 🔲 设计中 | 审核不过自动重写 |

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
  "description": "从立意到审校的全流程",
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
      "prompt": "为一部{genre}题材的{style}风格小说确定核心立意、目标读者、卖点。书名：{title}",
      "output": "planning/立意.md"
    },
    {
      "id": "outline",
      "needs": "故事结构",
      "prompt": "根据立意设计完整的故事大纲，包括主要人物、世界观、主线冲突",
      "input": ["planning/立意.md"],
      "output": "planning/大纲.md"
    },
    {
      "id": "chapter",
      "needs": "正文写作",
      "prompt": "根据大纲写第{n}章正文，保持与前文连贯",
      "input": ["planning/大纲.md", "planning/人物设定.md", "prev_chapters"],
      "output": "chapters/{n:03d}.md",
      "repeat": 20
    },
    {
      "id": "inspiration",
      "needs": "灵感激发",
      "prompt": "基于当前剧情进展，提供3个意想不到的转折方向",
      "input": ["prev_chapters"],
      "output": "inspiration/{n:03d}_灵感.md",
      "every": 3,
      "optional": true
    },
    {
      "id": "review",
      "needs": "剧情审查",
      "prompt": "审查全部章节的剧情逻辑、人物一致性、节奏",
      "input": ["planning/*", "chapters/*.md"],
      "output": "review/审核报告.md"
    },
    {
      "id": "proofread",
      "needs": "错别字检查",
      "prompt": "校对全部章节的错别字、语法、标点",
      "input": ["chapters/*.md"],
      "output": "review/校对报告.md"
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
| `repeat` | int | 循环执行次数（如逐章写作） |
| `every` | int | 每隔 N 步执行一次（如定期灵感注入） |
| `optional` | bool | 找不到匹配 Agent 时跳过而非报错 |

## 编排器实现

### WorkflowRunner

```python
class WorkflowRunner:
    def __init__(self, agents: dict[str, BaseAgent], project_dir: Path, project_info: dict):
        self._agent_by_skill: dict[str, list[BaseAgent]] = {}
        self._build_skill_index()

    def find_agent(self, skill: str) -> BaseAgent | None:
        """根据技能找到匹配的智能体。"""
        candidates = self._agent_by_skill.get(skill, [])
        return candidates[0] if candidates else None

    async def run(self, workflow: WorkflowDef, progress: dict | None = None) -> dict:
        """执行完整工作流，返回进度状态。"""

    async def run_single_step(self, step_id: str, workflow: WorkflowDef, n: int = 1) -> str:
        """执行单个步骤，返回输出内容。"""
```

### 技能匹配

```python
def _build_skill_index(self):
    """建立技能 → 智能体的反向索引。"""
    for agent in self.agents.values():
        for skill in agent.config.skills:
            self._agent_by_skill.setdefault(skill, []).append(agent)
```

### 文件传递上下文

```python
def _build_context(self, input_files: list[str], n: int) -> str:
    """读取输入文件，拼接上下文。"""
    for f in input_files:
        if f == "prev_chapters":
            # 读取前面所有章节
        elif "*" in f:
            # 通配符匹配
        else:
            # 读取指定文件
```

### LLM 动态编排（可选）

```python
async def generate_workflow(agents: dict, project_info: dict, llm: BaseLLM) -> WorkflowDef:
    """让 LLM 根据可用智能体动态生成工作流。"""
```

LLM 根据项目信息（书名、题材、风格）和可用智能体的技能列表，自动生成最优的工作流步骤定义。生成的工作流自动保存到 `workflow.json`。

### UI 进度面板

```python
class WorkflowPanel(QWidget):
    """工作流进度面板 — 显示步骤状态、进度条、执行日志。"""
```

面板功能：
- 步骤卡片列表：每个步骤显示状态图标（○ 等待 / ◉ 运行 / ● 完成 / ✗ 出错）
- 总体进度条：实时显示工作流完成百分比
- 循环步骤进度：显示当前章节 / 总章节数
- 执行日志：实时输出每步的执行信息
- 控制按钮：开始/停止/重置/ AI 生成工作流
- 菜单入口：工作流(Ctrl+Shift+W) 打开面板

## 项目目录结构

```
data/projects/{project_name}/
├── meta.json                    # 项目元信息
├── planning/
│   ├── 立意.md                  # 主编输出
│   ├── 大纲.md                  # 策划输出
│   ├── 人物设定.md              # 策划输出
│   ├── 世界观.md                # 策划输出
│   ├── 时间线.md                # 策划输出
│   ├── 主线.md                  # 策划输出
│   ├── 支线.md                  # 策划输出
│   └── 伏笔.md                  # 策划输出
├── chapters/
│   ├── 001_章节名.md            # 写手输出
│   ├── 002_章节名.md
│   └── ...
├── inspiration/
│   ├── 003_灵感.md              # 灵感注入（每3章一次）
│   └── 006_灵感.md
├── review/
│   ├── 审核报告.md              # 审核输出
│   └── 校对报告.md              # 校对输出
└── workflow.json                # 工作流定义（保存当前进度）
```

## 断点恢复

工作流执行过程中保存进度到 `workflow.json`：

```json
{
  "progress": {
    "ideation": "done",
    "outline": "done",
    "chapter": {"current": 15, "total": 20},
    "review": "pending"
  }
}
```

编排器启动时检查进度，从断点继续。

## 实现优先级

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0 | WorkflowRunner 基础框架 | ✅ 已完成 |
| P0 | 技能匹配 + 单步执行 | ✅ 已完成 |
| P1 | 循环步骤（逐章写作） | ✅ 已完成 |
| P1 | 文件读写 + 上下文传递 | ✅ 已完成 |
| P2 | 进度保存 + 断点恢复 | ✅ 已完成 |
| P2 | UI 进度面板 | ✅ 已完成 |
| P3 | LLM 动态编排 | ✅ 已完成 |
| P3 | 定时灵感注入 | ✅ 已完成 |
| P4 | 质量门控（审核不过重写） | 🔲 待实现 |

## 新增智能体的扩展方式

1. 在 `config/agents.json` 添加新智能体，配好 `skills`
2. 在工作流模板中添加 `needs: "新技能"` 的步骤
3. 编排器自动匹配，无需改代码

或者使用 LLM 动态编排，连工作流模板都不用改。
