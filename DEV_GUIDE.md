# Novel Writer 开发指南

## 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | >= 3.10 | 运行时 |
| PySide6 | >= 6.7 | GUI 框架 |
| Pydantic | >= 2.0 | 数据模型 |
| openai | >= 1.0 | 统一 LLM 接口（OpenAI 兼容协议） |

## 项目结构

```
novel-writer/
├── pyproject.toml                  # 项目配置 & 依赖
├── logo.png                        # 应用图标
├── config/
│   ├── agents.json                 # 智能体配置（含 system_prompt）
│   ├── default_agents.json         # 智能体默认配置（重置用，自动生成）
│   └── default_providers.json      # 默认模型供应商列表
├── data/                           # 用户数据（gitignore）
│   ├── config.json                 # 用户配置（主题、语言、模型）
│   └── projects/                   # 小说项目目录
│       └── {project_name}/
│           ├── meta.json           # 项目元信息
│           ├── chat.db             # 聊天记录（项目级 SQLite）
│           ├── planning/           # 规划文档（.md）
│           ├── chapters/           # 章节正文（.txt）+ 细纲（.md）
│           ├── inspiration/        # 灵感记录（.md）
│           ├── review/             # 审校报告（.md）
│           └── workflow.json       # 工作流进度
├── src/novel_writer/
│   ├── __main__.py                 # python -m novel_writer 入口
│   ├── app.py                      # QApplication 启动
│   ├── locales.py                  # i18n（中/英）
│   ├── core/
│   │   ├── llm/
│   │   │   ├── base.py             # BaseLLM + LLMMessage/LLMResponse
│   │   │   └── client.py           # 统一 LLMClient（OpenAI 兼容）
│   │   ├── agents/
│   │   │   ├── __init__.py         # load_agents() / save_agents()
│   │   │   └── base.py             # BaseAgent + AgentConfig
│   │   ├── project_io.py           # 项目目录 IO
│   │   └── workflow.py             # 工作流引擎
│   ├── models/
│   │   ├── project.py              # Project（基于目录的存储）
│   │   ├── chapter.py              # Chapter（文件 IO）
│   │   └── character.py            # Character
│   └── ui/
│       ├── styles.py               # 5 套主题
│       ├── main_window.py          # 主窗口（三栏布局）
│       ├── sidebar.py              # 侧边栏
│       ├── editor_panel.py         # 编辑区
│       ├── agent_panel.py          # 智能体面板（办公室+工作流+对话）
│       ├── office_scene.py         # 办公室场景（QPainter + Agent 动画）
│       ├── workflow_bar.py         # 工作流迷你进度条
│       ├── workflow_panel.py       # WorkflowThread（后台执行）
│       ├── agent_animation.py      # Agent 指示器动画
│       └── settings_dialog.py      # 设置对话框
```

## 架构设计

### 文件格式

- 章节正文：`.txt`（纯文本，不需要 Markdown 语法）
- 规划文档：`.md`（大纲、人物设定等有结构层级）
- 细纲：`{n}_标题.outline.md`
- 灵感：`.md`
- 章节序号：变宽数字，不补零（`1_xxx.txt`），支持任意位数

### 项目存储 (`core/project_io.py`)

```python
chapter_filename(number, title)    # "1_第一章.txt"
scan_chapters(project_dir)         # 按数字排序扫描
read_md / write_md                 # 文件读写（通用，不区分扩展名）
load_workflow / save_workflow       # 工作流进度
```

### LLM 层 (`core/llm/`)

统一 `LLMClient`，OpenAI 兼容协议，只需 `api_key + base_url + model`。

### Agent 系统 (`core/agents/`)

配置集中在 `config/agents.json`，每个 Agent 可配独立模型。Agent 的 `skills` 字段用于工作流技能匹配。

### 工作流引擎 (`core/workflow.py`)

```python
class WorkflowMode(Enum):
    NEW_BOOK / CONTINUE / FILL_GAPS / VALIDATE

class WorkflowRunner:
    async def run(workflow, progress)         # 执行完整工作流
    async def run_single_step(step_id, n)     # 执行单个步骤
    def find_agent(skill)                     # 技能匹配

def build_workflow(mode, project_info, start, end) -> WorkflowDef:
    """根据模式构建工作流定义。"""
```

- 章节步骤自动从响应提取标题生成文件名
- 已有章节文件会被复用（覆盖写入），不重复创建
- 支持循环步骤（repeat）、定时触发（every）、断点恢复

### UI 架构

```
MainWindow (splitter: 220 / 560 / 620)
├── Sidebar         # 项目管理、章节树
├── EditorPanel     # 正文/大纲/备注
└── AgentPanel      # 整合面板
    ├── OfficeScene     # 办公室场景（30%）— QPainter 动画
    ├── WorkflowMiniBar # 进度条 + 开始/停止
    └── ChatArea        # 对话区（70%）— SQLite 持久化
```

- 聊天记录按项目隔离：`data/projects/<name>/chat.db`
- 对话上下文包含全部规划文档 + 所有章节正文
- `AgentWorker(QThread)` 后台执行，信号驱动 UI
- 流式输出按 Agent 隔离，切换不影响进行中的流

### 菜单结构

```
├── 文件           # 新建/打开/保存、退出
├── 模型与智能体     # 模型设置、智能体管理
├── 工作流          # 开始工作流（模式选择）、加载默认工作流
└── 关于           # 外观设置、关于
```

## 添加新供应商

编辑 `config/default_providers.json`：

```json
{"name": "供应商名", "type": "openai_compat", "base_url": "https://api.example.com/v1"}
```

## Ollama 本地模型

1. 安装 Ollama：https://ollama.com
2. `ollama pull qwen3.5:9b`
3. 设置 → 模型中选择 Ollama

## 扩展智能体

设置 → 智能体 中添加，或编辑 `config/agents.json`。

## 开发环境

```bash
git clone <repo-url>
cd novel-writer
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m novel_writer
```
