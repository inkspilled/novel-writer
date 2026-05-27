# Novel Writer 开发指南

## 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | >= 3.10 | 运行时 |
| PySide6 | >= 6.7 | GUI 框架 |
| Pydantic | >= 2.0 | 数据模型 |
| openai | >= 1.0 | OpenAI 兼容接口 |
| anthropic | >= 0.40 | Claude API |
| httpx | >= 0.27 | HTTP 客户端 |

## 项目结构

```
novel-writer/
├── run.sh                          # Linux 一键启动
├── pyproject.toml                  # 项目配置 & 依赖
├── config/
│   └── default_agents.json         # 默认 Agent 配置
├── src/novel_writer/
│   ├── __main__.py                 # python -m novel_writer 入口
│   ├── app.py                      # QApplication 启动
│   ├── locales.py                  # i18n（中/英，130+ 条字符串）
│   ├── core/
│   │   ├── llm/                    # LLM 抽象层
│   │   │   ├── base.py             # BaseLLM 基类
│   │   │   ├── openai_compat.py    # OpenAI 兼容接口
│   │   │   ├── claude.py           # Anthropic Claude
│   │   │   └── ollama.py           # Ollama 本地模型
│   │   ├── agents/                 # Agent 系统
│   │   │   ├── __init__.py         # AGENT_CONFIGS 注册表
│   │   │   ├── base.py             # BaseAgent + AgentConfig
│   │   │   ├── editor.py           # 主编
│   │   │   ├── planner.py          # 策划
│   │   │   ├── writer.py           # 写手
│   │   │   ├── proofreader.py      # 校对
│   │   │   ├── reviewer.py         # 审核
│   │   │   └── polisher.py         # 润色
│   │   └── workflow.py             # 工作流引擎
│   ├── models/                     # Pydantic 数据模型
│   │   ├── project.py              # Project
│   │   ├── chapter.py              # Chapter + ChapterStatus
│   │   └── character.py            # Character
│   └── ui/                         # PySide6 界面
│       ├── styles.py               # 5 套主题 + build_style()
│       ├── main_window.py          # 主窗口（三栏布局）
│       ├── sidebar.py              # 侧边栏
│       ├── editor_panel.py         # 编辑区
│       ├── agent_panel.py          # Agent 面板（对话、Markdown）
│       ├── agent_animation.py      # Agent 状态动画
│       └── settings_dialog.py      # 独立设置对话框（外观/模型/Agent）
```

## 架构设计

### LLM 抽象层 (`core/llm/`)

统一接口 `BaseLLM`，所有供应商实现 `chat()` 和 `stream_chat()`：

```python
class BaseLLM(ABC):
    async def chat(self, messages, temperature, max_tokens) -> LLMResponse
    async def stream_chat(self, messages, temperature, max_tokens) -> AsyncIterator[str]
```

| 类 | 供应商 | 说明 |
|----|--------|------|
| `OpenAICompatLLM` | DeepSeek / Kimi / GLM / 通义 / OpenAI | 统一用 openai SDK |
| `ClaudeLLM` | Anthropic Claude | anthropic SDK |
| `OllamaLLM` | Ollama 本地 | 原生 API，支持思考模型 |

### Agent 系统 (`core/agents/`)

每个 Agent 继承 `BaseAgent`，持有独立的 `AgentConfig`：

```python
@dataclass
class AgentConfig:
    name: str
    role: str
    title: str
    system_prompt: str
    skills: list[str]
    model: str          # 空则用全局默认
    temperature: float
    max_tokens: int
```

Agent 可配置独立模型（如主编用 DeepSeek，写手用 Ollama），互不影响。

### UI 架构 (`ui/`)

```
MainWindow
├── Sidebar          # 项目管理、章节树、字数统计
├── EditorPanel      # 正文/大纲/备注 三 Tab
└── AgentPanel       # Agent 选择、对话、快捷操作
```

- `AgentWorker(QThread)`：后台线程执行 Agent 调用，信号驱动 UI 更新
- 流式输出：`chunk_received` 信号逐块更新 Markdown 渲染
- 主题系统：`styles.py` 定义 5 套主题色，`build_style()` 生成 QSS

## AI 模型配置

### 支持的供应商

| 供应商 | 类型 | Base URL |
|--------|------|----------|
| DeepSeek | openai_compat | `https://api.deepseek.com/v1` |
| Moonshot (Kimi) | openai_compat | `https://api.moonshot.cn/v1` |
| 智谱 GLM | openai_compat | `https://open.bigmodel.cn/api/paas/v4` |
| 通义千问 | openai_compat | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| OpenAI | openai_compat | `https://api.openai.com/v1` |
| Claude | claude | (SDK 内置) |
| Ollama (本地) | ollama | `http://localhost:11434` |

### 添加新供应商

在 `settings_dialog.py` 的 `DEFAULT_PROVIDERS` 列表中添加：

```python
{"name": "新供应商", "type": "openai_compat", "base_url": "https://api.example.com/v1"}
```

只要支持 OpenAI 兼容接口即可，无需修改代码。

### Ollama 本地模型

1. 安装 Ollama：https://ollama.com
2. 拉取模型：`ollama pull qwen3.5:9b`
3. 在 设置 → 模型 中选择 Ollama，模型会自动列出

支持思考模型（如 qwen3.5），自动提取 `<think>` 内容。

### 高级配置

在 `config.json` 的 `current_provider` 中可添加额外参数：

```json
{
  "current_provider": {
    "name": "DeepSeek",
    "type": "openai_compat",
    "api_key": "sk-xxx",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-reasoner"
  }
}
```

## 数据存储

| 文件 | 说明 |
|------|------|
| `~/.novel-writer/config.json` | 全局配置（主题、语言、模型、Agent） |
| `~/.novel-writer/projects/*.json` | 小说项目数据 |

项目 JSON 结构：

```json
{
  "id": "uuid",
  "title": "小说标题",
  "genre": "玄幻",
  "style": "轻松",
  "theme": "热血",
  "target_words": 200000,
  "synopsis": "简介",
  "world_setting": "世界观设定",
  "chapters": [
    {
      "id": "uuid",
      "number": 1,
      "title": "第一章",
      "outline": "章节大纲",
      "content": "正文内容",
      "notes": "备注",
      "status": "outlined"
    }
  ],
  "characters": [
    {
      "id": "uuid",
      "name": "张三",
      "personality": "性格",
      "background": "背景"
    }
  ]
}
```

## 开发环境

```bash
# 克隆项目
git clone <repo-url>
cd novel-writer

# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# 安装依赖（开发模式）
pip install -e .

# 启动
python -m novel_writer
```

## 扩展 Agent

1. 在 `core/agents/` 下创建新文件（如 `translator.py`）
2. 继承 `BaseAgent`，实现自定义逻辑
3. 在 `core/agents/__init__.py` 注册到 `AGENT_CONFIGS`
4. 在 `config/default_agents.json` 添加默认配置

或直接在 设置 → Agent 中添加自定义 Agent，无需改代码。
