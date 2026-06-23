# Novel Writer 开发指南

## 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | >= 3.10 | 运行时 |
| PySide6 | >= 6.7 | GUI 框架 |
| Pydantic | >= 2.0 | 数据模型 |
| openai | >= 1.0 | OpenAI 兼容 LLM 接口 |
| anthropic | >= 0.40 | Claude 原生接口 |
| httpx | >= 0.27 | Ollama HTTP 接口 |

## 项目结构

```
novel-writer/
├── pyproject.toml                  # 项目配置 & 依赖
├── logo.png                        # 应用图标
├── logs/                           # 日志目录（gitignore）
│   ├── info.log                    # INFO/DEBUG 日志（按天滚动，100MB 上限）
│   └── error.log                   # WARNING+ 日志
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
│           ├── world_state.json    # 大世界状态（人物/物品/地点/时间线）
│           ├── memory.json         # 长期记忆（11桶）
│           ├── planning/           # 规划文档（.md）
│           ├── chapters/           # 章节正文（.txt）+ 细纲（.md）+ 概要（.summary.md）
│           ├── inspiration/        # 灵感记录（.md）
│           ├── review/             # 审校报告（.md）
│           └── workflow.json       # 工作流进度
├── tests/                          # 单元测试
│   ├── test_quality_checker.py     # 质量检查测试
│   ├── test_reading_power.py       # 追读力测试
│   ├── test_workflow.py            # 工作流测试
│   └── test_exporter.py            # 导出功能测试
├── src/novel_writer/
│   ├── __main__.py                 # python -m novel_writer 入口
│   ├── app.py                      # QApplication 启动
│   ├── locales.py                  # i18n（中/英）
│   ├── core/
│   │   ├── logger.py               # 日志配置（get_logger 入口）
│   │   ├── llm/
│   │   │   ├── base.py             # BaseLLM + LLMMessage/LLMResponse
│   │   │   ├── client.py           # 统一 LLMClient（OpenAI 兼容）
│   │   │   ├── claude.py           # ClaudeLLM（Anthropic 原生）
│   │   │   ├── ollama.py           # OllamaLLM（本地模型）
│   │   │   └── openai_compat.py    # OpenAICompatLLM
│   │   ├── agents/
│   │   │   ├── __init__.py         # load_agents() / save_agents()
│   │   │   └── base.py             # BaseAgent + AgentConfig
│   │   ├── project_io.py           # 项目目录 IO
│   │   ├── world_state.py          # 大世界状态管理
│   │   ├── workflow.py             # 工作流引擎
│   │   ├── quality_checker.py      # 质量检查模块
│   │   ├── exporter.py             # 导出功能（TXT/EPUB/PDF）
│   │   ├── reading_power.py        # 追读力系统
│   │   ├── anti_patterns.py        # 反模式追踪
│   │   ├── memory.py               # 长期记忆
│   │   ├── rag.py                  # RAG 检索
│   │   └── character_sim.py        # 角色推演
│   ├── models/
│   │   ├── project.py              # Project（基于目录的存储）
│   │   ├── chapter.py              # Chapter（文件 IO）
│   │   └── character.py            # Character
│   ├── storage/                    # 存储抽象层
│   ├── assets/                     # 图标资源（icon.svg, status_icon.png）
│   └── ui/
│       ├── styles.py               # 4 套主题（深夜墨/晨雾白/远山蓝/苍山绿）
│       ├── main_window.py          # 主窗口（三栏布局）
│       ├── sidebar.py              # 侧边栏
│       ├── editor_panel.py         # 编辑区
│       ├── agent_panel.py          # 智能体面板（办公室+工作流+对话）
│       ├── office_scene.py         # 办公室场景（QPainter + Agent 动画）
│       ├── workflow_bar.py         # 工作流迷你进度条
│       ├── workflow_panel.py       # WorkflowThread（后台执行）
│       ├── agent_animation.py      # Agent 指示器动画
│       └── settings_dialog.py      # 设置对话框（外观/模型/智能体）
```

## 架构设计

### 日志系统 (`core/logger.py`)

参照 Java Logback 配置，提供统一的日志入口：

```python
from novel_writer.core.logger import get_logger
logger = get_logger(__name__)
```

- **三路输出**：`info.log`（INFO/DEBUG）、`error.log`（WARNING+）、控制台（INFO+ 带彩色）
- **滚动策略**：按天滚动 + 单文件 100MB 上限，保留 30 天
- **线程安全**：`threading.Lock` 保护初始化，支持多线程场景
- **PyInstaller 兼容**：自动检测 `sys.frozen`，日志输出到可执行文件旁

### 文件格式

- 章节正文：`.txt`（纯文本，不需要 Markdown 语法）
- 章节概要：`{n}_标题.summary.md`（自动生成，结构化卡片）
- 规划文档：`.md`（大纲、人物设定等有结构层级）
- 细纲：`{n}_标题.outline.md`
- 灵感：`.md`
- 章节序号：变宽数字，不补零（`1_xxx.txt`），支持任意位数

### 项目存储 (`core/project_io.py`)

```python
chapter_filename(number, title)         # "1_第一章.txt"
scan_chapters(project_dir)              # 按数字排序扫描
read_md / write_md                      # 原子写入（先写.tmp再rename，防崩溃丢数据）
load_workflow / save_workflow            # 工作流进度
rename_chapter(project_dir, n, title)   # 重命名章节（文件名+heading+细纲）
safe_rename_chapter(project_dir, n, title)  # 安全重命名（冲突检测+自动备份）
validate_chapter_content(content)       # 章节内容校验（空/过短/只有标题）
generate_toc(project_dir)               # 生成 planning/目录.md
fix_chapter_titles(project_dir)         # 用文件名标题修正正文 heading
load_chapter_summaries(project_dir)     # 加载章节概要（用于上下文组装）
```

### 大世界状态系统 (`core/world_state.py`)

像游戏存档一样追踪小说世界的结构化数据：

```python
from novel_writer.core.world_state import WorldState
ws = WorldState(project_dir)
ws.load()

# 角色操作
ws.get_character("凌尘")                    # 获取角色属性
ws.update_character("凌尘", {"gold": 10})   # 更新属性（深度合并）
ws.add_character("新角色", {...})            # 添加新角色

# 物品操作
ws.give_item("凌尘", "灵石矿", {...})       # 给予物品
ws.remove_item("凌尘", "枯叶")              # 移除物品
ws.add_item("青霜剑", {...})                # 添加到物品图鉴

# 世界操作
ws.add_location("禁地", {...})              # 添加地点
ws.set_date("宗历1247年 秋")                # 设置时间
ws.add_timeline_event(1, "事件", "地点")    # 记录时间线

# LLM 结构化更新
ws.apply_llm_update(update_json)            # 应用 LLM 返回的状态变化
ws.save()

# 上下文输出
ws.build_context_text()                     # 生成文本摘要，注入 LLM 上下文
```

`world_state.json` 结构：
```json
{
  "world": {"name": "青云修仙界", "locations": {...}, "power_system": {"levels": ["炼气", "筑基", ...]}},
  "characters": {"凌尘": {"cultivation": {"level": "炼气", "sub_level": "二层"}, "hp": 100, "sp": 30, "gold": 5, "inventory": [...], "equipment": {...}, "skills": [...], "location": "杂院"}},
  "items_catalog": {"青霜剑": {"type": "武器", "effect": "冰系攻击", "durability": "...", "uses": -1, "life_save": false}},
  "timeline": [{"chapter": 1, "event": "...", "location": "..."}]
}
```

### LLM 层 (`core/llm/`)

多驱动架构，支持三种后端：

| 驱动 | 文件 | 协议 | 适用场景 |
|------|------|------|----------|
| `LLMClient` | `client.py` | OpenAI 兼容 | DeepSeek/Kimi/GLM/通义/OpenAI |
| `ClaudeLLM` | `claude.py` | Anthropic 原生 | Claude 系列（需 `anthropic` 包） |
| `OllamaLLM` | `ollama.py` | Ollama HTTP | 本地模型（qwen3.5 等） |
| `OpenAICompatLLM` | `openai_compat.py` | OpenAI 兼容 | 通用 OpenAI 兼容接口 |

统一接口：`api_key + base_url + model`，切换后端只需改配置。

注意：所有 LLM 驱动采用懒加载（`_get_async_openai()` 等），避免 PySide6 shiboken 与 openai/anthropic 的导入链冲突。

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
- **智能跳过**：已有内容的章节在 LLM 调用前跳过，提升效率
- **标题约束**：已有文件名的章节，prompt 中注入标题约束防止 LLM 改标题
- **定时步骤**：灵感（每3章）、推演（每章）、概要（每章）、大世界状态（每章）、目录（每章）、润色（每2章）、校验（每章）、质量检查（每章）、摘要（每5章）、规划反哺（每10章）
- **纯工具步骤**：`fix_titles`、`toc`、`chapter_summary`、`world_state_update`、`quality_check` 不走标准 agent 流程

### 质量检查模块 (`core/quality_checker.py`)

自动评估章节质量，10 个维度：

```python
from novel_writer.core.quality_checker import QualityChecker

checker = QualityChecker(project_dir)
report = checker.check_chapter(content, chapter_number)

# 报告包含：
# - total_score: 总分 0-100
# - word_count_score: 篇幅得分
# - structure_score: 结构得分
# - dialogue_score: 对话得分
# - scene_score: 场景得分
# - rhythm_score: 节奏得分
# - hook_score: 钩子得分
# - cool_point_score: 爽点得分
# - micro_payoff_score: 微兑现得分
# - consistency_score: 一致性得分
# - sentence_variety_score: 句式多样性得分
# - issues: 问题列表
# - suggestions: 建议列表
```

### 导出功能 (`core/exporter.py`)

支持多种格式导出：

```python
from novel_writer.core.exporter import Exporter

exporter = Exporter(project_dir)

# TXT 导出
txt_path = exporter.export_txt()

# EPUB 导出（需要 ebooklib）
epub_path = exporter.export_epub()

# PDF 导出（需要 reportlab）
pdf_path = exporter.export_pdf()
```

### 追读力系统 (`core/reading_power.py`)

量化读者体验，追踪章节吸引力：

```python
from novel_writer.core.reading_power import ReadingPowerTracker

tracker = ReadingPowerTracker(project_dir)

# 分析章节
rp = tracker.analyze_chapter(content, chapter_number)
tracker.record(rp)

# 生成指导
guidance = tracker.build_guidance(next_chapter_number)

# 统计
hook_stats = tracker.get_hook_stats(window=10)
cool_point_stats = tracker.get_cool_point_stats(window=10)
debt_total = tracker.get_debt_total()
```

### 智能上下文组装 (`_build_context`)

4层分层架构，按任务类型自动裁剪：

```python
# ContextTier 枚举控制注入量
Tier 0 (GLOBAL):   项目元信息                    # ~100 tokens
Tier 1 (WORLD):    + 世界设定/角色状态/角色约束    # ~500 tokens
Tier 2 (NARRATIVE):+ 剧情摘要/伏笔/反模式/追读力  # ~1500 tokens
Tier 3 (WORKING):  + 章节概要/全文/推演/RAG       # ~4000 tokens

# 步骤 → 层级映射 (STEP_CONTEXT_TIERS)
chapter       → WORKING   # 写作需要完整上下文
polish        → NARRATIVE # 润色需要风格指导
inspiration   → NARRATIVE # 灵感需要剧情进展
review        → NARRATIVE # 审核需要世界观+叙事
sim           → WORLD    # 推演需要角色设定
proofread     → GLOBAL   # 校对只需当前章节
quality_check → GLOBAL   # 质量检查只需当前章节
toc/fix_titles→ GLOBAL   # 工具步骤
```

优化点：
- 角色状态合并去重（world_state + memory.character_state）
- 章节概要窗口 10→5，全文窗口 3→2
- 上下文拆分稳定前缀/变量后缀，优化 Prompt Caching
- Token 估算 + debug 日志

### UI 架构

```
MainWindow (splitter: 220 / 560 / 620)
├── Sidebar         # 项目管理、章节树、字数统计
├── EditorPanel     # 正文/大纲/备注
└── AgentPanel      # 整合面板
    ├── OfficeScene     # 办公室场景 — QPainter + Agent 动画
    ├── WorkflowMiniBar # 进度条（百分比）+ 执行日志 + 开始/停止
    └── ChatArea        # 对话区 — SQLite 持久化
```

- 右侧面板布局：办公室场景占大部分空间，下方依次是进度条、执行日志、对话区
- 聊天记录按项目隔离：`data/projects/<name>/chat.db`
- 对话上下文包含全部规划文档 + 所有章节正文
- 工作流调用 Agent 时对话自动写入聊天记录
- `AgentWorker(QThread)` 后台执行，信号驱动 UI
- 流式输出按 Agent 隔离，切换不影响进行中的流

### 菜单结构

```
├── 文件           # 新建/打开/保存、导出（TXT/EPUB/PDF）
├── 模型与智能体     # 模型设置、智能体管理
├── 工作流          # 开始工作流（模式选择）、加载默认工作流
└── 关于           # 外观设置、关于
```

## 添加新供应商

编辑 `config/default_providers.json`：

```json
{"name": "供应商名", "type": "openai_compat", "base_url": "https://api.example.com/v1"}
```

支持的类型：
- `openai_compat` — OpenAI 兼容协议（DeepSeek/Kimi/GLM/通义/OpenAI）
- `ollama` — 本地 Ollama 模型

## Claude 原生接口

使用 `anthropic` 包直连，无需代理：

1. 安装依赖：`pip install anthropic`
2. 设置 → 模型 → 选择 Claude 供应商
3. 填入 API Key，选择模型（如 `claude-sonnet-4-20250514`）

## Ollama 本地模型

1. 安装 Ollama：https://ollama.com
2. `ollama pull qwen3.5:9b`
3. 设置 → 模型中选择 Ollama，自动检测已安装模型

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

## 运行测试

```bash
# 运行所有测试
python -m pytest tests/ -v

# 运行特定测试
python -m pytest tests/test_quality_checker.py -v
python -m pytest tests/test_reading_power.py -v
python -m pytest tests/test_workflow.py -v
python -m pytest tests/test_exporter.py -v

# 查看测试覆盖率
python -m pytest tests/ --cov=src/novel_writer --cov-report=html
```
