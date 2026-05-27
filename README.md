# Novel Writer - AI 小说写作桌面应用

多 Agent 协作的小说创作工具，从立意到定稿全流程覆盖。Apple 极简风格 UI。

## 启动

```bash
cd ~/novel-writer
./run.sh
```

首次运行自动创建虚拟环境并安装依赖。

## 写作流程

| 阶段 | Agent | 职责 |
|------|-------|------|
| 立意 | 主编 | 题材、风格、目标读者、核心主题 |
| 大纲 | 策划 | 章节结构、人物设定、世界观、伏笔 |
| 初稿 | 写手 | 按大纲逐章写作 |
| 校对 | 校对 | 错别字、语法、标点、一致性 |
| 审核 | 审核 | 剧情逻辑、人物一致性、节奏 |
| 润色 | 润色 | 文笔提升、场景描写、情感表达 |

支持自定义 Agent，可配置专属模型、技能范围和系统提示词。

## 支持的模型

模型名称手动填写，支持所有 OpenAI 兼容接口。

| 供应商 | 示例模型 |
|--------|----------|
| DeepSeek | deepseek-chat, deepseek-reasoner |
| Moonshot (Kimi) | moonshot-v1-8k/32k/128k |
| 智谱 GLM | glm-4-plus, glm-4-flash |
| 通义千问 | qwen-plus, qwen-turbo |
| OpenAI | gpt-4o, gpt-4o-mini |
| Claude | claude-sonnet-4, claude-haiku-4-5 |
| Ollama (本地) | qwen3.5:9b, qwen2.5, llama3.1 等 |

Ollama 自动检测本地运行的模型，支持思考模型（如 qwen3.5）。

## 语言

支持中文/English 双语切换，设置 → 外观 → 语言。

## 主题

5 种主题风格，设置 → 外观 → 立即应用：

| 主题 | 风格 |
|------|------|
| 深夜墨 | 深色底，静谧克制 |
| 晨雾白 | 明亮清爽，Apple 风格 |
| 远山蓝 | 深邃蓝色调 |
| 苍山绿 | 自然绿色调 |
| 自定义 | 自选背景色、面板色、字体色、强调色 |

自定义主题支持 5 个颜色选择器：背景色、面板色、主字体色、副字体色、强调色，可通过色盘或十六进制值设置。

## Agent 管理

- **独立模型**：每个 Agent 可配置专属模型（如主编用 DeepSeek，写手用 Ollama）
- **自定义 Agent**：添加新 Agent，定义技能和提示词
- **技能范围**：为每个 Agent 配置专属技能标签
- 菜单 → Agent → Agent 管理

## 界面

```
┌────────────┬────────────────────┬───────────────────┐
│   侧边栏    │      编辑区         │    Agent 面板     │
│            │                    │                   │
│  项目管理   │   Markdown 编辑器   │   Agent 选择      │
│  章节树    │   正文 / 大纲 / 备注  │   对话交互        │
│  字数统计   │   实时字数统计       │   工作状态动画     │
└────────────┴────────────────────┴───────────────────┘
```

## 项目结构

```
~/novel-writer/
├── run.sh                                    # 一键启动（自动创建 venv）
├── pyproject.toml                            # 项目配置 & 依赖
├── README.md
├── config/
│   └── default_agents.json                   # 默认 Agent 配置
├── src/novel_writer/
│   ├── __init__.py
│   ├── __main__.py                           # python3 -m novel_writer 入口
│   ├── app.py                                # QApplication 启动入口
│   ├── locales.py                            # i18n 国际化（中/英，122 条字符串）
│   ├── core/
│   │   ├── __init__.py
│   │   ├── llm/                              # LLM 抽象层
│   │   │   ├── __init__.py
│   │   │   ├── base.py                       # BaseLLM 基类（chat/stream_chat）
│   │   │   ├── openai_compat.py              # OpenAI 兼容（DeepSeek/Kimi/GLM/通义/OpenAI）
│   │   │   ├── claude.py                     # Anthropic Claude
│   │   │   └── ollama.py                     # Ollama 本地模型（支持思考模型 qwen3.5）
│   │   ├── agents/                           # Agent 系统
│   │   │   ├── __init__.py                   # AGENT_CONFIGS 注册表
│   │   │   ├── base.py                       # BaseAgent + AgentConfig
│   │   │   ├── editor.py                     # 主编 Agent
│   │   │   ├── planner.py                    # 策划 Agent
│   │   │   ├── writer.py                     # 写手 Agent
│   │   │   ├── proofreader.py                # 校对 Agent
│   │   │   ├── reviewer.py                   # 审核 Agent
│   │   │   └── polisher.py                   # 润色 Agent
│   │   └── workflow.py                       # 工作流引擎（串联 Agent 执行）
│   ├── models/                               # Pydantic 数据模型
│   │   ├── __init__.py
│   │   ├── project.py                        # Project（小说项目）
│   │   ├── chapter.py                        # Chapter + ChapterStatus 枚举
│   │   └── character.py                      # Character（人物卡）
│   └── ui/                                   # PySide6 界面
│       ├── __init__.py
│       ├── styles.py                         # 5 套主题样式 + build_style()
│       ├── main_window.py                    # 主窗口（三栏布局、菜单、AgentWorker）
│       ├── sidebar.py                        # 侧边栏（项目管理、章节树、统计）
│       ├── editor_panel.py                   # 编辑区（正文/大纲/备注 Tab）
│       ├── agent_panel.py                    # Agent 面板（对话、按钮、动画）
│       ├── agent_animation.py                # Agent 状态动画（呼吸灯、旋转、气泡）
│       └── settings_dialog.py                # 设置对话框（外观/模型/Agent 三 Tab）
└── .vscode/
    ├── launch.json                           # VS Code 调试配置
    └── settings.json                         # Python 解释器路径
```

### 核心模块说明

| 模块 | 职责 |
|------|------|
| `locales.py` | 所有 UI 文本集中管理，`t(key)` 获取当前语言文本，支持 `zh`/`en` |
| `styles.py` | 5 套主题（深夜墨/晨雾白/远山蓝/苍山绿/自定义），`build_style(theme, config)` 生成 QSS |
| `core/llm/` | 统一 LLM 接口，支持 async chat/stream_chat，Ollama 自动提取思考模型内容 |
| `core/agents/` | 每个 Agent 独立配置模型/温度/技能/提示词，可自定义添加 |
| `models/` | Project/Chapter/Character 数据类，JSON 序列化持久化 |
| `ui/main_window.py` | 三栏 QSplitter 布局，AgentWorker(QThread) 后台执行，项目 CRUD |
| `ui/settings_dialog.py` | 外观（主题+语言+字号）、模型（供应商+API）、Agent（增删改查） |

## 数据存储

- 配置：`~/.novel-writer/config.json`
- 项目：`~/.novel-writer/projects/*.json`

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| Ctrl+N | 新建项目 |
| Ctrl+O | 打开项目 |
| Ctrl+S | 保存项目 |
| Ctrl+, | 打开设置 |
| Ctrl+Q | 退出 |
