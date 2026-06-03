# 工作流编排器设计文档

## 目标

实现自动化写作流程：用户选择模式和章节数，编排器自动调度多个智能体协作完成小说创作。

## 核心原则

- **智能体自描述**：每个 Agent 通过 `skills` 字段声明能力，编排器自动匹配
- **技能驱动**：工作流步骤绑定"需要什么技能"，不绑定具体智能体名称
- **新增即生效**：添加新智能体后无需改代码，编排器自动发现新能力
- **文件传递**：步骤之间通过文件传递上下文（规划用 `.md`，正文用 `.txt`）

## 工作流模式

| 模式 | 说明 | 步骤 |
|------|------|------|
| 📖 新书 | 从立意到审校全流程 | 立意→大纲→人物→世界观→时间线→主线→支线→伏笔→标题校准→逐章写作(灵感→推演→写作→概要→目录→润色→校对→摘要→规划反哺)→审核 |
| ✍️ 续写 | 从已有章节继续 | 标题校准→逐章写作→规划反哺→审核（自动检测起始章节） |
| 🔍 查漏补缺 | 补写缺失或空章节 | 扫描目录，只为缺失/空章节（0字节）生成写作步骤 |
| ✅ 校验 | 审核+校对已有章节 | 标题校准→目录→审核→校对 |

启动时弹出模式选择对话框，可配置起始/结束章节号。

## 实现状态

| 功能 | 状态 | 说明 |
|------|------|------|
| 技能匹配（needs → skills） | ✅ 已实现 | WorkflowRunner.find_agent() |
| 单步执行 | ✅ 已实现 | run_single_step() |
| 循环步骤（repeat） | ✅ 已实现 | 逐章写作 |
| 定时触发（every） | ✅ 已实现 | 每 N 章灵感注入 |
| 文件输入/输出 | ✅ 已实现 | 章节 .txt，规划 .md |
| 模板变量插值 | ✅ 已实现 | {n}, {title}, {genre} 等 |
| 进度回调 | ✅ 已实现 | on_step_start / on_step_end |
| 断点恢复 | ✅ 已实现 | workflow.json 保存进度 |
| 工作流模式 | ✅ 已实现 | 新书/续写/查漏/校验 |
| 章节标题提取 | ✅ 已实现 | 从 Agent 响应中提取第一个标题作为文件名 |
| 章节文件复用 | ✅ 已实现 | 已有章节覆盖写入，不重复创建 |
| 标题校准 | ✅ 已实现 | LLM 根据正文内容重新生成标题，改文件名+heading |
| 章节概要 | ✅ 已实现 | 每章自动生成结构化概要卡片（.summary.md） |
| 目录更新 | ✅ 已实现 | 每章自动更新 planning/目录.md |
| 规划反哺 | ✅ 已实现 | 每10章自动更新大纲/人物/伏笔 |
| 标题约束 | ✅ 已实现 | 已有文件的章节，prompt 注入标题约束 |
| UI 进度面板 | ✅ 已实现 | 办公室场景联动 + 迷你进度条 |
| 空文件检测 | ✅ 已实现 | 0 字节章节视为缺失，查漏补缺自动补写 |
| 对话记录联动 | ✅ 已实现 | 工作流调用 Agent 时对话自动写入 chat.db |
| 章节侧边栏刷新 | ✅ 已实现 | 新章节生成后侧边栏自动更新 |
| 质量门控 | 🔲 设计中 | 审核不过自动重写 |

## 架构

```
用户选择模式 + 章节范围
         │
         ▼
   ┌─ build_workflow() ─┐
   │  根据模式构建步骤    │
   │  新书: 12步          │
   │  续写: 4步           │
   │  校验: 2步           │
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
      "id": "chapter",
      "needs": "正文写作",
      "prompt": "根据大纲写第{n}章正文，保持与前文连贯",
      "input": ["planning/大纲.md", "planning/人物设定.md", "prev_chapters"],
      "output": "chapters/{n}_chapter.txt",
      "repeat": 20
    },
    {
      "id": "review",
      "needs": "剧情审查",
      "prompt": "审查全部章节的剧情逻辑、人物一致性、节奏",
      "input": ["planning/*", "chapters/*.txt"],
      "output": "review/审核报告.md"
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
| `update_planning` | bool | 强制覆盖 planning/ 文件（用于规划反哺步骤） |

## 编排器实现

### WorkflowRunner

```python
class WorkflowRunner:
    def __init__(self, agents, project_dir, project_info)

    def find_agent(self, skill) -> BaseAgent | None:
        """根据技能找到匹配的智能体。"""

    async def run(self, workflow, progress) -> dict:
        """执行完整工作流，返回进度状态。"""

    def _find_chapter_file(self, n) -> str | None:
        """查找第 n 章的已有文件名，避免重复创建。"""
```

### 章节输出逻辑

1. 执行章节步骤时，先检查 `chapters/` 目录是否已有该章节文件
2. 有且非空 → 跳过（不调用 LLM）
3. 有但空 → 调用 LLM，写入已有文件名（保留原文件名）
4. 无 → 从 Agent 响应中提取第一个 Markdown 标题作为文件名，如 `1_风起云涌.txt`
5. 无标题 → 使用默认名 `1_第1章.txt`
6. 已有文件名时，prompt 注入标题约束，防止 LLM 改标题

### 智能上下文组装

```python
def _build_context(self, input_files, n) -> str:
    """7 层上下文组装。"""
    # 1. 长期记忆（11桶）         → memory.py
    # 2. 反模式约束               → anti_patterns.py
    # 3. 追读力指导               → reading_power.py
    # 4. 角色推演结果             → character_sim.py
    # 5. 章节概要（最近10章）      → project_io.load_chapter_summaries
    # 6. 最新摘要 + 最近3章全文   → project_io.latest_summary
    # 7. RAG检索结果              → rag.py
```

### 纯工具步骤

以下步骤不需要 LLM，直接执行文件操作：
- `fix_titles`：LLM 根据正文内容生成标题，重命名文件+heading
- `toc`：生成 planning/目录.md
- `chapter_summary`：LLM 生成每章结构化概要

## UI 进度面板

工作流进度整合到智能体面板中：

- **WorkflowMiniBar**：进度条（显示百分比如 `45%`）+ 步骤摘要 + 开始/停止按钮 + 可展开的执行日志
- **执行日志**：实时记录每步的开始/完成/出错，有新日志自动展开
  - `▸ 写手 开始 chapter (第3章)`
  - `✓ 写手 完成 chapter (第3章)`
  - `✗ 写手 出错: ...`
- **办公室场景联动**：工作流执行时，办公室里的 Agent 实时切换状态
  - 当前步骤的 Agent → 脉冲光圈 + 旋转弧线 + 任务标签气泡
  - 其余 Agent → 等待中（⏳ 图标）
  - 步骤完成 → 弹跳 + 👍
  - 出错 → 摇晃 + ❗
  - 全部完成 → 全员庆祝

## 项目目录结构

```
data/projects/{project_name}/
├── meta.json
├── chat.db                      # 项目级聊天记录
├── planning/
│   ├── 立意.md                  # 主编输出
│   ├── 大纲.md                  # 策划输出（每10章自动反哺）
│   ├── 人物设定.md              # 每10章自动反哺
│   ├── 世界观.md
│   ├── 时间线.md
│   ├── 主线.md
│   ├── 支线.md
│   ├── 伏笔.md                  # 每10章自动反哺
│   └── 目录.md                  # 每章自动更新
├── chapters/
│   ├── 1_章节名.txt             # 写手输出（.txt）
│   ├── 1_章节名.summary.md      # 章节概要（自动生成）
│   ├── 1_章节名.outline.md      # 细纲（可选，.md）
│   └── ...
├── inspiration/
│   ├── 3_灵感.md                # 灵感注入（每3章一次）
│   └── 6_灵感.md
├── review/
│   ├── 审核报告.md
│   └── 校对报告.md
└── workflow.json                # 工作流定义 + 进度
```

## 断点恢复

```json
{
  "workflow": { ... },
  "progress": {
    "ideation": "done",
    "outline": "done",
    "chapter": {"current": 15, "total": 20},
    "review": "pending"
  }
}
```

编排器启动时检查进度，从断点继续。

## 扩展方式

1. 在 `config/agents.json` 添加新智能体，配好 `skills`
2. 在工作流模板中添加 `needs: "新技能"` 的步骤
3. 编排器自动匹配，无需改代码
