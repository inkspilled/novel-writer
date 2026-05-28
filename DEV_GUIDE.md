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
│   ├── config.json                 # 用户配置（主题、语言、模型、智能体）
│   ├── chat.db                     # 聊天记录（SQLite）
│   └── projects/                   # 小说项目目录
│       └── {project_name}/
│           ├── meta.json           # 项目元信息
│           ├── planning/           # 规划文档（立意/大纲/人物设定/世界观等）
│           ├── chapters/           # 章节正文
│           ├── inspiration/        # 灵感记录
│           ├── review/             # 审校报告
│           └── workflow.json       # 工作流进度
├── src/novel_writer/
│   ├── __main__.py                 # python -m novel_writer 入口
│   ├── app.py                      # QApplication 启动
│   ├── locales.py                  # i18n（中/英，130+ 条字符串）
│   ├── core/
│   │   ├── llm/
│   │   │   ├── base.py             # BaseLLM 基类 + LLMMessage/LLMResponse
│   │   │   └── client.py           # 统一 LLMClient（OpenAI 兼容）
│   │   ├── agents/
│   │   │   ├── __init__.py         # load_agents() / save_agents() / reset_agents()
│   │   │   └── base.py             # BaseAgent + AgentConfig
│   │   ├── project_io.py           # 项目目录 IO 操作
│   │   └── workflow.py             # 工作流引擎（技能匹配 + 文件传递）
│   ├── models/                     # Pydantic 数据模型
│   │   ├── project.py              # Project（基于目录的存储）
│   │   ├── chapter.py              # Chapter（轻量元数据，文件 IO）
│   │   └── character.py            # Character
│   └── ui/                         # PySide6 界面
│       ├── styles.py               # 5 套主题 + build_style()
│       ├── main_window.py          # 主窗口（三栏布局）
│       ├── sidebar.py              # 侧边栏
│       ├── editor_panel.py         # 编辑区
│       ├── agent_panel.py          # Agent 面板（对话、Markdown、SQLite 持久化）
│       ├── agent_animation.py      # Agent 状态动画
│       └── settings_dialog.py      # 设置对话框（外观/模型/智能体）
```

## 架构设计

### 项目存储层 (`core/project_io.py`)

所有项目数据以目录形式存储，文件读写统一通过 `project_io` 模块：

```python
init_project_dir(project_dir)      # 创建目录骨架
save_meta / load_meta              # meta.json 读写
read_md / write_md                 # MD 文件读写
scan_chapters(project_dir)         # 扫描 chapters/ 目录
load_workflow / save_workflow       # 工作流进度
list_projects(projects_root)       # 扫描所有项目
```

### LLM 层 (`core/llm/`)

统一用 `LLMClient` 一个类，通过 OpenAI 兼容协议连接所有模型：

```python
class LLMClient(BaseLLM):
    def __init__(self, model, api_key="", base_url="https://api.openai.com/v1")
    async def chat(self, messages, temperature, max_tokens) -> LLMResponse
    async def stream_chat(self, messages, temperature, max_tokens) -> AsyncIterator[str]
```

只需 `api_key + base_url + model` 三个参数。Ollama 自动拼接 `/v1` 端点。

### Agent 系统 (`core/agents/`)

所有智能体配置集中在 `config/agents.json`，运行时通过 `load_agents()` 加载。

```python
@dataclass
class AgentConfig:
    name: str
    role: str
    title: str
    system_prompt: str
    skills: list[str]
    model: str          # 引用 saved_models 的 key，空则用全局默认
    temperature: float
    max_tokens: int
```

Agent 可配置独立模型（如主编用 DeepSeek，写手用 Ollama），互不影响。

智能体重置机制：首次运行时自动将 `agents.json` 复制为 `default_agents.json`，在智能体管理中点击"重置默认"可恢复。

### 数据模型层 (`models/`)

**Project** — 基于目录的项目管理：

```python
class Project:
    _project_dir: Path       # 项目目录路径

    def save()               # 保存到目录（meta.json + 章节文件）
    def load(project_dir)    # 从目录加载
    def add_chapter(title)   # 添加章节（自动创建文件）
    def total_words()        # 总字数（从文件读取）
```

**Chapter** — 轻量元数据，正文通过文件 IO：

```python
class Chapter:
    number: int
    title: str
    status: ChapterStatus
    _content_path: str       # 正文文件路径
    _outline_path: str       # 细纲文件路径

    @property content        # 从文件读取正文
    @content.setter          # 写入正文到文件
    @property outline        # 从文件读取细纲
```

### 工作流引擎 (`core/workflow.py`)

技能驱动的多 Agent 协作编排器：

```python
class WorkflowRunner:
    def __init__(self, agents, project_dir, project_info)
    async def run(workflow, progress)      # 执行完整工作流
    async def run_single_step(step_id)     # 执行单个步骤
    def find_agent(skill)                  # 技能匹配
```

支持：循环步骤（repeat）、定时触发（every）、文件输入/输出、模板变量插值、断点恢复。

### 模型配置系统

用户配置存储在 `data/config.json`：

```json
{
  "current_provider": {
    "name": "DeepSeek",
    "type": "openai_compat",
    "api_key": "sk-xxx",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat"
  },
  "saved_models": {
    "DeepSeek Chat": { ... }
  }
}
```

- `current_provider`：全局默认模型
- `saved_models`：已保存的模型方案，供智能体下拉选择

### UI 架构 (`ui/`)

```
MainWindow
├── Sidebar          # 项目管理、章节树、字数统计
├── EditorPanel      # 正文/大纲/备注 三 Tab
└── AgentPanel       # Agent 按钮行、对话、快捷操作、模型信息、SQLite 持久化
```

- `AgentWorker(QThread)`：后台线程执行 Agent 调用，信号驱动 UI 更新，支持 asyncio task 取消
- 流式输出：`chunk_received` 信号逐块更新 Markdown 渲染，按 Agent 隔离（`_stream_agent` + `_pending_streams`），切换不影响
- 聊天记录：SQLite 持久化到 `data/chat.db`，Python 标准库内置，打包无额外依赖
- Agent 切换时名称行显示当前使用的模型（HTML 富文本）
- 智能体专属模型通过下拉选择已保存模型方案，不支持手动输入
- 关闭应用时 `closeEvent` 取消进行中的请求 + 关闭所有 `AsyncOpenAI` 客户端连接

### 菜单结构

```
├── 文件        # 新建/打开/保存项目、退出
├── 模型与智能体  # 模型设置、智能体管理
└── 关于        # 外观设置、关于
```

## 添加新供应商

编辑 `config/default_providers.json`：

```json
{"name": "供应商名", "type": "openai_compat", "base_url": "https://api.example.com/v1"}
```

只要支持 OpenAI 兼容接口即可，无需修改代码。也可直接在模型设置中手动填写 Base URL。

## Ollama 本地模型

1. 安装 Ollama：https://ollama.com
2. 拉取模型：`ollama pull qwen3.5:9b`
3. 在设置 → 模型中选择 Ollama，模型列表自动列出
4. 支持局域网内其他 Ollama 实例，修改 Base URL 即可

## 扩展智能体

直接在 设置 → 智能体 中添加自定义智能体，配置标题、图标、技能、提示词、专属模型。

或编辑 `config/agents.json` 添加新的内置智能体。

## 数据存储

| 文件 | 说明 |
|------|------|
| `data/config.json` | 全局配置（主题、语言、模型、智能体） |
| `data/chat.db` | 聊天记录（SQLite） |
| `data/projects/*/` | 小说项目目录 |
| `config/agents.json` | 智能体配置 |
| `config/default_agents.json` | 智能体默认配置（重置用） |

## 开发环境

```bash
git clone <repo-url>
cd novel-writer
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
pip install -e .
python -m novel_writer
```
