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
│   ├── default_agents.json         # 默认智能体配置（含 system_prompt）
│   └── default_providers.json      # 默认模型供应商列表
├── data/                           # 用户数据（gitignore）
│   ├── config.json                 # 用户配置（主题、语言、模型、智能体）
│   ├── chat.db                     # 聊天记录（SQLite）
│   └── projects/*.json             # 小说项目数据
├── src/novel_writer/
│   ├── __main__.py                 # python -m novel_writer 入口
│   ├── app.py                      # QApplication 启动
│   ├── locales.py                  # i18n（中/英，130+ 条字符串）
│   ├── core/
│   │   ├── llm/
│   │   │   ├── base.py             # BaseLLM 基类 + LLMMessage/LLMResponse
│   │   │   └── client.py           # 统一 LLMClient（OpenAI 兼容）
│   │   └── agents/
│   │       ├── __init__.py         # load_default_agents() 从 JSON 加载
│   │       └── base.py             # BaseAgent + AgentConfig
│   ├── models/                     # Pydantic 数据模型
│   │   ├── project.py              # Project
│   │   ├── chapter.py              # Chapter + ChapterStatus
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

所有智能体配置集中在 `config/default_agents.json`，运行时通过 `load_default_agents()` 加载。

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
    "DeepSeek Chat": {
      "type": "openai_compat",
      "model": "deepseek-chat",
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "sk-xxx"
    }
  },
  "agents": {
    "editor": { "model": "DeepSeek Chat", ... },
    "writer": { "model": "", ... }
  }
}
```

- `current_provider`：全局默认模型
- `saved_models`：已保存的模型方案，供智能体下拉选择
- 智能体的 `model` 字段引用 `saved_models` 的 key，空值使用全局默认

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

或编辑 `config/default_agents.json` 添加新的内置智能体。

## 数据存储

| 文件 | 说明 |
|------|------|
| `data/config.json` | 全局配置（主题、语言、模型、智能体） |
| `data/chat.db` | 聊天记录（SQLite） |
| `data/projects/*.json` | 小说项目数据 |

## 开发环境

```bash
git clone <repo-url>
cd novel-writer
python -m venv .venv
source .venv/bin/activate   # Linux/Mac
pip install -e .
python -m novel_writer
```
