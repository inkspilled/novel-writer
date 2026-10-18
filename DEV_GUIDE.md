# Novel Writer 开发指南

> **算法口径**（质量检查/追读力/上下文组装/工作流/记忆/世界状态等）已拆至
> **[docs/](docs/README.md)** —— 新增文档只在 `docs/README.md` 登记，本文件不再维护算法清单。
> 出现口径冲突时，以 `docs/README.md` 的「权威归属」表裁决；表未覆盖的以 `src/novel_writer/` 实现为真值。

## 技术栈

| 技术 | 版本 | 用途 | 许可 |
|------|------|------|------|
| Python | >= 3.10 | 运行时 | PSF |
| PySide6 | >= 6.7 | GUI 框架 | LGPL-3.0（动态链接合规） |
| dataclass | 标准库 | 数据模型（无 Pydantic 依赖） | PSF |
| openai | >= 1.0 | OpenAI 兼容 LLM 接口 | Apache-2.0 |
| anthropic | >= 0.40 | Claude 原生接口 | MIT |
| httpx | >= 0.27 | HTTP 客户端 | BSD-3 |
| reportlab | >= 4.0 | PDF 导出 | BSD-3 |

> EPUB 导出为**纯标准库**（`zipfile`），无第三方依赖。原 `ebooklib`（AGPL）因商用风险已移除。

## 项目结构

```
novel-writer/
├── main.py                         # 根目录入口（VSCode 可直接运行）
├── pyproject.toml                  # 项目配置 & 依赖
├── novel-writer.spec               # PyInstaller onedir 配置（DLL 模式，启动快）
├── build_installer.bat             # 一键打包脚本
├── installer.iss                   # Inno Setup 安装包脚本
├── ChineseSimplified.isl           # 中文安装语言包
├── logo.png                        # 应用图标
├── docs/                           # 项目文档（20 篇；完整清单与权威归属见 docs/README.md）
│   └── README.md                   # 唯一文档总目录（性质标签 + 权威归属）
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
│   ├── test_workflow_runner.py
│   ├── test_character_isolation.py
│   ├── test_ui_layout.py
│   ├── test_workflow_package.py
│   ├── test_workflow_runner.py
│   ├── test_rag.py
│   ├── test_exporter.py
│   └── test_bugfixes.py            # 关键 BUG 修复回归
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
    │   │   ├── base.py             # BaseAgent + AgentConfig（usage_sink / run_with_tools）
    │   │   ├── loop.py             # 工具循环（轮数/预算/降级）
    │   │   └── tools.py            # 只读工具集 NovelToolkit / ToolRegistry
    │   ├── skills.py               # 技能包注册表（渐进披露）
    │   ├── canon/
    │   │   └── events.py           # append-only 正典事件（known_by/hidden/hash chain）
    │   ├── characters/
    │   │   ├── registry.py         # 角色档案/分档/状态机/知识冻结
    │   │   └── view.py             # CharacterView（角色 POV 隔离视图）
    │   ├── project_io.py           # → docs/项目存储与文件格式.md
    │   ├── app_config.py           # 应用路径常量 + 用户配置读写
    │   ├── chat_history.py         # 聊天记录持久化（项目级 SQLite）
    │   ├── world_state.py          # → docs/大世界状态算法.md
    │   ├── story_threads.py         # → docs/故事线台账算法.md
    │   ├── workflow/               # → docs/工作流引擎算法.md
    │   │   ├── constants.py        # 技能名与步骤 id 分组
    │   │   ├── definition.py       # WorkflowStep/Def、内置模板、build_workflow
    │   │   ├── context.py          # → docs/分层上下文组装算法.md
    │   │   ├── prompts.py          # → docs/节奏控制与角色锚定算法.md
    │   │   ├── builtin_steps.py    # 特殊步骤处理器注册表
    │   │   ├── sediment.py         # → docs/写后沉淀算法.md
    │   │   ├── journal.py          # runs/*.jsonl 运行日志
    │   │   └── runner.py           # WorkflowRunner（重试/超时/DAG/工具）
    │   ├── quality_checker.py      # → docs/质量检查算法.md
    │   ├── exporter.py             # 导出功能（TXT/EPUB/PDF）
    │   ├── reading_power.py        # → docs/追读力算法.md
    │   ├── anti_patterns.py        # → docs/反模式追踪算法.md
    │   ├── memory.py               # → docs/长期记忆算法.md
    │   ├── rag.py                  # → docs/RAG检索算法.md
    │   ├── chapter_tracker.py      # → docs/章节四维追踪算法.md
    │   ├── character_sim.py        # → docs/角色推演算法.md
    │   └── text_signals.py         # 钩子/爽点/微兑现词表（唯一权威）
    ├── skills/                     # 内置写作技能包（chapter-craft/dialogue/...）
    ├── models/
    │   ├── project.py              # Project（基于目录的存储）
    │   ├── chapter.py              # Chapter（文件 IO）
    │   └── character.py            # Character
    ├── storage/                    # 存储抽象层（占位，实际 IO 在 project_io）
    ├── assets/                     # 图标资源
    ├── designsystem/               # 设计 token（主题色唯一来源）
    │   └── tokens.py               # 四套主题色板 + get_theme_colors
    └── ui/                         # GUI 壳（依赖方向：ui → core/models，反向禁止）
        ├── main_window.py          # 主窗口骨架（状态/信号/生命周期）
        ├── app_shell/              # 主窗口行为 Mixin（按关注点拆）
        │   ├── theme_mixin.py      # 主题/菜单/Toast/关于
        │   ├── llm_mixin.py        # LLM 与 Agent 初始化
        │   ├── project_mixin.py    # 项目/章节/规划/导出
        │   ├── chat_mixin.py       # Agent 对话与流式
        │   ├── settings_mixin.py   # 设置对话框入口
        │   └── workflow_mixin.py   # 工作流启动/进度/停止
        ├── common/                 # 共享基建
        │   ├── styles.py           # Qt 样式表生成（色值读 designsystem）
        │   ├── workers.py          # AgentWorker / 连接测试线程
        │   ├── chat_rendering.py   # Markdown 渲染 + 颜色池
        │   └── chat_widgets.py     # 消息气泡 / 输入框
        ├── agent/                  # 智能体
        │   ├── agent_panel.py      # 办公室 + 工作流条 + 对话
        │   ├── agent_dialog.py     # 智能体管理
        │   ├── agent_animation.py  # 指示器动画
        │   └── office_scene.py     # 办公室场景（QPainter）
        ├── editor/                 # 写作区
        │   ├── editor_panel.py     # 正文 / 规划 / 世界 三级导航
        │   └── sidebar.py          # 章节列表
        ├── character/              # 世界页
        │   ├── character_page.py   # 人物（分档/状态/auto_sim）
        │   ├── world_page.py       # 世界四页容器（人物/物品/地点/故事线）
        │   ├── storyline_page.py   # 故事线台账（时间线/主线/支线/伏笔）
        │   └── character_dialog.py # 角色卡对话框
        ├── workflow/               # 工作流
        │   ├── workflow_panel.py   # WorkflowThread
        │   └── workflow_bar.py     # 迷你进度条
        ├── project/                # 项目
        │   ├── project_dialogs.py  # 打开项目 / 模式选择
        │   └── new_project_dialog.py
        ├── settings/               # 设置
        │   ├── model_dialog.py
        │   └── appearance_dialog.py
        └── shell/                  # 启动壳
            └── splash.py
```

## 架构约定（守护测试 `tests/test_arch_guard.py`）

1. **`core/` / `models/` 禁止 import Qt** — 纯 Python，可脱离 GUI 测试
2. **`core/` 不得 import `ui/`** — 依赖单向：`ui → core/models`
3. **`ui/` 根只允许 `main_window.py`** — 功能页进子包（common/agent/editor/workflow/project/settings/shell）
4. **色值只来自 `designsystem/tokens.py`** — 样式表生成在 `ui/common/styles.py`
5. **主窗口行为拆进 `ui/app_shell/`** — Mixin 按关注点分文件，`main_window.py` 只留骨架

> 各子系统的**原理、口径、公式、边界**见 `docs/` 对应文档；本节只留入口与一句话职责。

### 日志系统 (`core/logger.py`)

参照 Java Logback 配置，统一入口 `get_logger(__name__)`。三路输出（info.log / error.log / 控制台彩色）、按天滚动 + 100MB 上限 + 保留 30 天、`threading.Lock` 线程安全、PyInstaller `sys.frozen` 兼容。

### LLM 层 (`core/llm/`) → [docs/LLM多驱动接口.md](docs/LLM多驱动接口.md)

**统一 OpenAI 兼容 API**：`api_key + base_url + model` 三要素，无类型区分。支持 Ollama / llama.cpp / vLLM / LM Studio / 云端 API。模型列表可拉取（`GET /models`）下拉选择。**懒加载** SDK 规避 shiboken 导入链冲突；显式 `certifi.where()` 规避 Windows 证书库卡顿。

### Agent 系统 (`core/agents/`)

配置集中在 `config/agents.json`，每个 Agent 可配独立模型。`skills` 字段用于工作流技能匹配（`WorkflowStep.needs` → `find_agent`）。

### 工作流引擎 (`core/workflow/`) → [docs/工作流引擎算法.md](docs/工作流引擎算法.md)

五模式（NEW_BOOK / NEW_BOOK_PLANNING / CONTINUE / FILL_GAPS / VALIDATE）由共享步骤列表组合。支持 `every` 定时、`repeat` 循环、断点恢复、智能跳过，以及步骤级 `retry/timeout/depends_on`。特殊步骤（toc / fix_titles / chapter_summary / world_state_update / quality_check / proofread / review / review_panel / 细纲）走 `BUILTIN_STEP_HANDLERS` 注册表，不走标准 LLM 路径。

**故事线反哺**：`world_state_update` 与每章写后沉淀完成后调用
`runner._sync_story_ledger(n)`（纯规则、零 LLM），把伏笔/支线/时间线对账进
`story_threads.json` 并 `on_event("story_track")`；UI 收到步骤完成信号即刷新「世界→故事线」。

### 智能体工具与技能 → [docs/智能体运行时与工具.md](docs/智能体运行时与工具.md)

写作/润色等步骤可声明只读工具（`tools`）与技能包（`skills`）：`AgentLoop` 在受限轮数内执行「请求→工具→再请求」，工具结果回填，异常转文本；模型不支持 tools 时自动降级纯文本。技能包目录优先级：项目 `skills/` > `config/skills/` > 包内置。

### 上下文组装 (`core/workflow/context.py`) → [docs/分层上下文组装算法.md](docs/分层上下文组装算法.md)

四层 ContextTier（GLOBAL/WORLD/NARRATIVE/WORKING），按步骤类型映射；超限按 RAG → 旧章节 → 规划 → 推演 → 概要优先级压缩。规划文档 BM25 检索只注入相关片段；人物设定全文注入。

### 质量检查 (`core/quality_checker.py`) → [docs/质量检查算法.md](docs/质量检查算法.md)

10 维规则打分（0–100 加权总分）+ 问题/建议列表。纯规则，不调 LLM。

### 追读力 (`core/reading_power.py`) → [docs/追读力算法.md](docs/追读力算法.md)

钩子 5 类 × 强度、爽点、微兑现、阅读债务；滑窗统计生成下一章写作指导。

### 记忆与世界状态 → [docs/长期记忆算法.md](docs/长期记忆算法.md) · [docs/大世界状态算法.md](docs/大世界状态算法.md)

- `memory.py`：11 桶跨章事实，同键去重 + outdated 审计。
- `world_state.py`：世界/角色/物品/时间线**当前快照**，深度合并 + LLM 结构化更新。
- `story_threads.py`：时间线/主线/支线/伏笔**统一台账**——解析四份规划稿，与 memory/world_state
  对账出状态与进度（手工编辑优先），供「世界→故事线」UI 展示并反哺写作上下文。
- 分工：**world_state 存快照，memory 存流水**。

### 写后沉淀 (`core/workflow/sediment.py`) → [docs/写后沉淀算法.md](docs/写后沉淀算法.md)

每章写完自动抽取：状态变化 / 伏笔 / 章节事件 / 四维追踪 → 记忆；追读力分析落盘。

### 角色推演 (`core/character_sim.py`) → [docs/角色推演算法.md](docs/角色推演算法.md)

`parse_characters` 解析人物设定角色档案；工作流 `sim` 步骤写 `sim_cache/sim_{n}.md`，`load_sim_cache` 带标记注入写作上下文。

### 导出功能 (`core/exporter.py`)

```python
from novel_writer.core.exporter import Exporter
exporter = Exporter(project_dir)
exporter.export_txt()    # TXT
exporter.export_epub()   # EPUB（纯标准库）
exporter.export_pdf()    # PDF（需 reportlab）
```

### UI 架构

```
MainWindow (splitter: 240 / 680 / 480)
├── Sidebar         # 项目管理、章节树、字数统计
├── EditorPanel     # 正文 / 规划（9 份文档）/ 世界（人物/物品/地点/故事线）
└── AgentPanel      # 办公室场景 + 工作流进度/日志 + 对话区
```

- 聊天记录按项目隔离：`data/projects/<name>/chat.db`
- 工作流日志只走 `WorkflowMiniBar` / `runs/*.jsonl`，**不写聊天记录**（避免污染 chat.db）
- `AgentWorker(QThread)` 后台执行，信号驱动 UI
- 流式输出按 Agent 隔离
- 三级导航「正文 / 规划 / 世界」；Agent 区是 104–182px 状态条，不是假 3D 办公室
- `Ctrl+Shift+F` 专注模式：隐藏左右栏，只留正文

### 世界页（`ui/character/`）

「世界」一级标签内是左侧菜单 + 右侧内容的分栏：**人物 / 物品 / 地点 / 故事线**。

- 人物/物品/地点围绕 `world_state.json`（`character_page.py` / `world_page.py`）
- 故事线围绕 `story_threads.json`（`storyline_page.py`）：KPI 卡 + 按类型/卷分组的线程树 +
  右侧详情编辑；双击线程 `chapter_activate_requested` → 编辑器跳章；手工改动写 `user_edited`
- `WorldPage.reload()` 会顺带对账故事线；`reload_storyline()` 供工作流落盘后轻量刷新

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

# 启动（三选一）
python main.py                   # 根目录入口
python -m novel_writer           # 模块入口
# VSCode：打开 main.py → 右上角 ▶

# 测试
python -m pytest tests/ -v

# 打包（onedir DLL 模式，启动快）
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

自动流程：创建/检查 venv → 安装依赖（含 PyInstaller）→ `logo.png` 生成 `logo.ico` → PyInstaller **onedir**（DLL 模式）→ Inno Setup 安装包。

输出：`output/NovelWriter-Setup.exe`

### 打包脚本

| 文件 | 用途 |
|------|------|
| `novel-writer.spec` | PyInstaller onedir 配置（DLL 独立，启动快，杀毒误报少） |
| `build_installer.bat` | 一键打包脚本 |
| `installer.iss` | Inno Setup 安装包脚本（中英双语安装） |
| `ChineseSimplified.isl` | 中文安装语言包 |

### 分发

```
dist/NovelWriter/
├── NovelWriter.exe          # 主程序
├── _internal/               # DLL/pyd/依赖（onedir 独立加载）
├── data/                    # 用户数据（运行时创建）
├── config/                  # 配置文件
└── logs/                    # 日志（运行时创建）
```

onedir 模式 vs onefile：启动速度快（无需解压临时目录）、杀毒误报少。
