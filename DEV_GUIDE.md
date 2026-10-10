# Novel Writer 开发指南

> **算法口径**（质量检查/追读力/上下文组装/工作流/记忆/世界状态等）已拆至
> **[docs/](docs/README.md)** —— 新增文档只在 `docs/README.md` 登记，本文件不再维护算法清单。
> 出现口径冲突时，以 `docs/README.md` 的「权威归属」表裁决；表未覆盖的以 `src/novel_writer/` 实现为真值。

## 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | >= 3.10 | 运行时 |
| PySide6 | >= 6.7 | GUI 框架 |
| Pydantic | >= 2.0 | 数据模型 |
| openai | >= 1.0 | OpenAI 兼容 LLM 接口 |
| anthropic | >= 0.40 | Claude 原生接口 |
| httpx | >= 0.27 | Ollama HTTP 接口 |
| ebooklib | >= 0.18 | EPUB 导出 |
| reportlab | >= 4.0 | PDF 导出 |

## 项目结构

```
novel-writer/
├── pyproject.toml                  # 项目配置 & 依赖
├── novel-writer.spec               # PyInstaller 配置
├── build_installer.bat             # 一键打包脚本
├── installer.iss                   # Inno Setup 安装包脚本
├── logo.png                        # 应用图标
├── docs/                           # 项目文档（总索引见 docs/README.md）
│   ├── README.md                   # 唯一文档总目录（性质标签 + 权威归属）
│   ├── 质量检查算法.md              # 十维规则打分
│   ├── 追读力算法.md                # 钩子/爽点/微兑现/债务
│   ├── 反模式追踪算法.md            # AI 味禁忌库
│   ├── 长期记忆算法.md              # 11 桶记忆
│   ├── 大世界状态算法.md            # 世界/角色/物品/时间线快照
│   ├── 章节四维追踪算法.md          # 资源/情感/信息边界/支线
│   ├── 写后沉淀算法.md              # 章后自动沉淀管线
│   ├── 分层上下文组装算法.md        # 四层 ContextTier + 压缩
│   ├── RAG检索算法.md               # BM25 关键词检索
│   ├── 工作流引擎算法.md            # 五模式 × 步骤组合
│   ├── 节奏控制与角色锚定算法.md    # 七阶段 + 角色硬约束
│   ├── 角色推演算法.md              # 多视角剧情推演
│   ├── LLM多驱动接口.md             # 四驱动统一契约
│   └── 项目存储与文件格式.md        # 目录布局/原子写/IO API
├── logs/                           # 日志目录（gitignore）
│   ├── info.log                    # INFO/DEBUG（按天滚动，100MB 上限）
│   └── error.log                   # WARNING+ 日志
├── config/
│   ├── agents.json                 # 智能体配置（含 system_prompt）
│   ├── default_agents.json         # 智能体默认配置（重置用，自动生成）
│   └── default_providers.json      # 默认模型供应商列表
├── data/                           # 用户数据（gitignore）
│   ├── config.json                 # 用户配置（主题、语言、模型）
│   └── projects/                   # 小说项目目录 → 详见 docs/项目存储与文件格式.md
├── tests/                          # 单元测试
│   ├── test_quality_checker.py
│   ├── test_reading_power.py
│   ├── test_workflow.py
│   ├── test_workflow_package.py
│   └── test_exporter.py
└── src/novel_writer/
    ├── __main__.py                 # python -m novel_writer 入口
    ├── app.py                      # QApplication 启动
    ├── locales.py                  # i18n（中/英）
    ├── core/
    │   ├── logger.py               # 日志配置（get_logger 入口）
    │   ├── llm/                    # → docs/LLM多驱动接口.md
    │   │   ├── base.py             # BaseLLM + LLMMessage/LLMResponse
    │   │   ├── client.py           # 统一 LLMClient（OpenAI 兼容）
    │   │   ├── claude.py           # ClaudeLLM（Anthropic 原生）
    │   │   ├── ollama.py           # OllamaLLM（本地模型）
    │   │   └── openai_compat.py    # OpenAICompatLLM
    │   ├── agents/
    │   │   ├── __init__.py         # load_agents() / save_agents()
    │   │   └── base.py             # BaseAgent + AgentConfig
    │   ├── project_io.py           # → docs/项目存储与文件格式.md
    │   ├── app_config.py           # 应用路径常量 + 用户配置读写
    │   ├── chat_history.py         # 聊天记录持久化（项目级 SQLite）
    │   ├── world_state.py          # → docs/大世界状态算法.md
    │   ├── workflow/               # → docs/工作流引擎算法.md
    │   │   ├── constants.py        # 技能名与步骤 id 分组
    │   │   ├── definition.py       # WorkflowStep/Def、内置模板、build_workflow
    │   │   ├── context.py          # → docs/分层上下文组装算法.md
    │   │   ├── prompts.py          # → docs/节奏控制与角色锚定算法.md
    │   │   ├── builtin_steps.py    # 特殊步骤处理器注册表
    │   │   ├── sediment.py         # → docs/写后沉淀算法.md
    │   │   └── runner.py           # WorkflowRunner 执行器
    │   ├── quality_checker.py      # → docs/质量检查算法.md
    │   ├── exporter.py             # 导出功能（TXT/EPUB/PDF）
    │   ├── reading_power.py        # → docs/追读力算法.md
    │   ├── anti_patterns.py        # → docs/反模式追踪算法.md
    │   ├── memory.py               # → docs/长期记忆算法.md
    │   ├── rag.py                  # → docs/RAG检索算法.md
    │   ├── chapter_tracker.py      # → docs/章节四维追踪算法.md
    │   └── character_sim.py        # → docs/角色推演算法.md
    ├── models/
    │   ├── project.py              # Project（基于目录的存储）
    │   ├── chapter.py              # Chapter（文件 IO）
    │   └── character.py            # Character
    ├── storage/                    # 存储抽象层（占位，实际 IO 在 project_io）
    ├── assets/                     # 图标资源
    └── ui/
        ├── styles.py               # 4 套主题（深夜墨/晨雾白/远山蓝/苍山绿）
        ├── main_window.py          # 主窗口（三栏布局，纯 UI 编排）
        ├── workers.py              # 后台线程（AgentWorker / TestConnectionWorker）
        ├── project_dialogs.py      # 打开项目 / 工作流模式对话框
        ├── appearance_dialog.py    # 外观设置（含 ColorPicker）
        ├── model_dialog.py         # 模型设置
        ├── agent_dialog.py         # 智能体管理
        ├── sidebar.py              # 侧边栏
        ├── editor_panel.py         # 编辑区（正文 + 8个规划文档标签页）
        ├── agent_panel.py          # 智能体面板（办公室+工作流+对话）
        ├── chat_rendering.py       # 聊天 Markdown 渲染 + 颜色池
        ├── chat_widgets.py         # 消息气泡 / 输入框控件
        ├── office_scene.py         # 办公室场景（QPainter + Agent 动画）
        ├── workflow_bar.py         # 工作流迷你进度条
        ├── workflow_panel.py       # WorkflowThread（后台执行）
        └── agent_animation.py      # Agent 指示器动画
```

## 架构速览

> 各子系统的**原理、口径、公式、边界**见 `docs/` 对应文档；本节只留入口与一句话职责。

### 日志系统 (`core/logger.py`)

参照 Java Logback 配置，统一入口 `get_logger(__name__)`。三路输出（info.log / error.log / 控制台彩色）、按天滚动 + 100MB 上限 + 保留 30 天、`threading.Lock` 线程安全、PyInstaller `sys.frozen` 兼容。

### LLM 层 (`core/llm/`) → [docs/LLM多驱动接口.md](docs/LLM多驱动接口.md)

四驱动（OpenAI 兼容 / Claude 原生 / Ollama / 通用兼容）统一 `api_key + base_url + model` 契约。**懒加载** SDK 规避 shiboken 导入链冲突；显式 `certifi.where()` 规避 Windows 证书库卡顿。

### Agent 系统 (`core/agents/`)

配置集中在 `config/agents.json`，每个 Agent 可配独立模型。`skills` 字段用于工作流技能匹配（`WorkflowStep.needs` → `find_agent`）。

### 工作流引擎 (`core/workflow/`) → [docs/工作流引擎算法.md](docs/工作流引擎算法.md)

五模式（NEW_BOOK / NEW_BOOK_PLANNING / CONTINUE / FILL_GAPS / VALIDATE）由共享步骤列表组合。支持 `every` 定时、`repeat` 循环、断点恢复、智能跳过。特殊步骤（toc / fix_titles / chapter_summary / world_state_update / quality_check）走 `BUILTIN_STEP_HANDLERS` 注册表，不走标准 LLM 路径。

### 上下文组装 (`core/workflow/context.py`) → [docs/分层上下文组装算法.md](docs/分层上下文组装算法.md)

四层 ContextTier（GLOBAL/WORLD/NARRATIVE/WORKING），按步骤类型映射；超限按 RAG → 旧章节 → 规划 → 推演 → 概要优先级压缩。规划文档 BM25 检索只注入相关片段；人物设定全文注入。

### 质量检查 (`core/quality_checker.py`) → [docs/质量检查算法.md](docs/质量检查算法.md)

10 维规则打分（0–100 加权总分）+ 问题/建议列表。纯规则，不调 LLM。

### 追读力 (`core/reading_power.py`) → [docs/追读力算法.md](docs/追读力算法.md)

钩子 5 类 × 强度、爽点、微兑现、阅读债务；滑窗统计生成下一章写作指导。

### 记忆与世界状态 → [docs/长期记忆算法.md](docs/长期记忆算法.md) · [docs/大世界状态算法.md](docs/大世界状态算法.md)

- `memory.py`：11 桶跨章事实，同键去重 + outdated 审计。
- `world_state.py`：世界/角色/物品/时间线**当前快照**，深度合并 + LLM 结构化更新。
- 分工：**world_state 存快照，memory 存流水**。

### 写后沉淀 (`core/workflow/sediment.py`) → [docs/写后沉淀算法.md](docs/写后沉淀算法.md)

每章写完自动抽取：状态变化 / 伏笔 / 章节事件 / 四维追踪 → 记忆；追读力分析落盘。

### 角色推演 (`core/character_sim.py`) → [docs/角色推演算法.md](docs/角色推演算法.md)

人物设定 → 角色档案 → LLM 多视角推演 → 剧情建议；`sim_cache/sim_{n}.md` 注入写作上下文。

### 导出功能 (`core/exporter.py`)

```python
from novel_writer.core.exporter import Exporter
exporter = Exporter(project_dir)
exporter.export_txt()    # TXT
exporter.export_epub()   # EPUB（需 ebooklib）
exporter.export_pdf()    # PDF（需 reportlab）
```

### UI 架构

```
MainWindow (splitter: 220 / 560 / 620)
├── Sidebar         # 项目管理、章节树、字数统计
├── EditorPanel     # 正文 + 8个规划文档标签页
└── AgentPanel      # 办公室场景 + 工作流进度/日志 + 对话区
```

- 聊天记录按项目隔离：`data/projects/<name>/chat.db`
- 工作流调用 Agent 时对话自动写入聊天记录
- `AgentWorker(QThread)` 后台执行，信号驱动 UI
- 流式输出按 Agent 隔离

### 菜单结构

```
├── 文件           # 新建/打开/保存、导出（TXT/EPUB/PDF）
├── 模型与智能体     # 模型设置、智能体管理
├── 工作流          # 开始工作流（模式选择）、加载默认工作流
└── 关于           # 外观设置、关于
```

## 常用命令

```bash
# 创建环境
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .

# 启动
python -m novel_writer

# 测试
python -m pytest tests/ -v
python -m pytest tests/test_quality_checker.py -v
python -m pytest tests/test_reading_power.py -v
python -m pytest tests/test_workflow.py -v
python -m pytest tests/test_exporter.py -v

# 覆盖率
python -m pytest tests/ --cov=src/novel_writer --cov-report=html

# 打包
build_installer.bat              # 一键：venv → 依赖 → ico → PyInstaller → Inno Setup
                                 # 输出 output/NovelWriter-Setup.exe
```

## 扩展指南

### 添加新供应商

编辑 `config/default_providers.json`：

```json
{"name": "供应商名", "type": "openai_compat", "base_url": "https://api.example.com/v1"}
```

类型：`openai_compat`（DeepSeek/Kimi/GLM/通义/OpenAI）· `ollama`（本地）。新增类型需同步 `llm/client.py` 分发与模型设置对话框。

### Claude 原生接口

1. `pip install anthropic`
2. 设置 → 模型 → 选择 Claude 供应商
3. 填入 API Key，选择模型

### Ollama 本地模型

1. 安装 [Ollama](https://ollama.com)
2. `ollama pull qwen3.5:9b`
3. 设置 → 模型中选择 Ollama，自动检测已安装模型

### 扩展智能体

设置 → 智能体 中添加，或编辑 `config/agents.json`。

### 新增算法/规范文档

1. 在 `docs/` 撰写（模板：引擎路径 + 一句话总结 + 口径表 + 边界）
2. **只在 [docs/README.md](docs/README.md) 登记**，勿改本文件清单
3. 若涉及口径冲突，更新 `docs/README.md`「权威归属」表

## 打包发布

### 一键打包（推荐）

```bash
build_installer.bat
```

自动流程：创建/检查 venv → 安装依赖（含 PyInstaller）→ `logo.png` 生成 `logo.ico` → PyInstaller 单文件 exe → Inno Setup 安装包。

### 打包脚本

| 文件 | 用途 |
|------|------|
| `novel-writer.spec` | PyInstaller 配置（入口、数据文件、隐藏导入） |
| `build_installer.bat` | 一键打包脚本 |
| `installer.iss` | Inno Setup 安装包脚本 |

### 前提条件

- [Inno Setup 6](https://jrsoftware.org/isdl.php)
- Python >= 3.10

### 手动打包

```bash
pip install pyinstaller
pyinstaller novel-writer.spec
```

### 打包注意事项

1. **数据目录**：打包后 `data/`、`config/` 在可执行文件旁创建
2. **日志目录**：`logs/` 运行时自动创建
3. **依赖项**：PyInstaller 自动收集，动态导入可能需手动加 hidden imports
4. **PySide6 资源**：PyInstaller 自动处理

### 分发

```
dist/NovelWriter/
├── NovelWriter.exe          # 主程序
├── _internal/               # 依赖文件
├── data/                    # 用户数据（运行时创建）
├── config/                  # 配置文件
└── logs/                    # 日志（运行时创建）
```

可直接 zip 分发 `dist/NovelWriter/`。
