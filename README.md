

# Novel Writer - AI 小说写作桌面应用

多 Agent 协作的小说创作工具，从立意到定稿全流程覆盖。

## 核心特性

- **多 Agent 协作**：7 种专业智能体（主编、策划、写手、校对、审核、灵感、润色），各司其职
- **全流程覆盖**：支持新书立意、全流程写作、续写、查漏补缺、校验五种模式
- **智能上下文**：4 层分层架构 + RAG 检索，按需注入信息，避免 token 爆炸
- **大世界状态**：像游戏存档一样追踪角色、物品、地点、时间线
- **节奏控制**：根据目标章节数自动划分 7 个阶段，防止长篇节奏失控
- **规划反哺**：每 10 章自动更新大纲、人物设定、伏笔清单
- **质量检查**：11 项指标自动评估章节质量

## 快速开始

### 安装与运行

```bash
cd novel-writer
pip install -e .
python -m novel_writer
```

### Windows 一键打包

```bash
# 双击运行
build_installer.bat
```

自动完成：创建 venv → 安装依赖 → 生成 ico → PyInstaller 打包 → Inno Setup 生成安装包

输出：`output/NovelWriter-Setup.exe`

**前提条件**：
- [Inno Setup 6](https://jrsoftware.org/isdl.php) — 生成安装包
- Python >= 3.10

### 手动打包

```bash
pip install pyinstaller
pyinstaller novel-writer.spec
```

## 项目结构

```
src/novel_writer/
├── core/                    # 核心业务逻辑
│   ├── llm/                 # LLM 接口层
│   │   ├── base.py          # 抽象基类
│   │   ├── client.py        # OpenAI 兼容客户端
│   │   ├── claude.py        # Claude 原生接口
│   │   ├── ollama.py        # Ollama 本地模型
│   │   └── openai_compat.py # OpenAI 兼容客户端
│   ├── agents/              # Agent 系统
│   │   └── base.py          # Agent 抽象基类
│   ├── workflow.py          # 工作流引擎
│   ├── quality_checker.py   # 质量检查模块
│   ├── reading_power.py     # 追读力系统
│   ├── world_state.py       # 大世界状态
│   ├── rag.py               # RAG 检索
│   ├── exporter.py          # 导出功能
│   ├── project_io.py        # 项目文件操作
│   ├── memory.py            # 长期记忆
│   ├── chapter_tracker.py   # 章节追踪
│   ├── character_sim.py     # 角色推演
│   └── anti_patterns.py     # 反模式追踪
├── ui/                      # 用户界面
│   ├── main_window.py       # 主窗口
│   ├── agent_panel.py       # Agent 面板
│   ├── editor_panel.py      # 编辑面板
│   ├── sidebar.py           # 侧边栏
│   ├── office_scene.py      # 智能体办公室
│   ├── workflow_panel.py    # 工作流面板
│   └── settings_dialog.py   # 设置对话框
├── models/                  # 数据模型
│   ├── project.py           # 项目模型
│   ├── chapter.py           # 章节模型
│   └── character.py         # 角色模型
└── locales.py               # 国际化

tests/                       # 单元测试
config/                      # 配置文件
data/                        # 数据存储
```

## 技术栈

- **Python 3.10+**
- **PyQt6** — 跨平台 GUI 框架
- **异步编程** — asyncio/await 提升并发性能
- **SQLite** — 轻量级聊天记录存储
- **BM25** — 规划文档检索算法
- **PyInstaller/Inno Setup** — 桌面应用打包

## 界面布局

```
┌──────────┬──────────────────┬───────────────────────────┐
│  侧边栏   │     编辑区        │   智能体办公室（占 50%+）   │
│          │                  │                           │
│ 项目管理   │  正文 + 8个规划标签 │  办公室场景 + Agent 动画    │
│ 章节树    │  实时字数统计      │  进度条（带百分比）+ 执行日志 │
│ 字数统计   │  规划文档独立编辑   │  💬 对话区                │
└──────────┴──────────────────┴───────────────────────────┘
```

## 写作流程

| 阶段 | Agent | 职责 |
|------|-------|------|
| 立意 | 📋 主编 | 题材、风格、目标读者、核心主题 |
| 大纲 | 🗺️ 策划 | 章节结构、人物设定、世界观、伏笔 |
| 初稿 | ✍️ 写手 | 按大纲逐章写作 |
| 校对 | 🔍 校对 | 错别字、语法、标点、一致性 |
| 审核 | ✅ 审核 | 剧情逻辑、人物一致性、节奏 |
| 灵感 | 💡 灵感 | 创意激发、脑洞拓展、题材联想 |
| 润色 | ✨ 润色 | 文笔提升、场景描写、情感表达 |

## 工作流模式

| 模式 | 说明 | 步骤 |
|------|------|------|
| 📝 新书立意 | 只生成规划文档，不写正文 | 8 步（立意→大纲→人物→世界观→时间线→主线→支线→伏笔） |
| 📖 新书全流程 | 从立意到审校全流程 | 完整流程（可配置目标章节数） |
| ✍️ 续写 | 从已有章节继续 | 自动检测起始章节 |
| 🔍 查漏补缺 | 补写缺失或空章节 | 按需（自动扫描） |
| ✅ 校验 | 审核 + 校对已有章节 | 2 步 |

### 续写工作流执行顺序

每章执行顺序：
1. 🔧 **标题校准**（首次）→ LLM 根据正文内容修正所有章节标题
2. 💡 **灵感**（每3章）→ 提供创作方向
3. 🎭 **推演**（每章）→ 角色行为推演
4. ✍️ **写作**（每章）→ 正文写作
5. 📝 **章节概要**（每章）→ 自动生成结构化概要卡片
6. 🌍 **大世界状态更新**（每章）→ 更新人物/物品/地点/时间线
7. 📑 **目录更新**（每章）→ 更新 planning/目录.md
8. ✨ **润色**（每2章）→ 提升文笔
9. 📝 **校验**（每章）→ 错别字检查
10. 📋 **摘要**（每5章）→ 剧情摘要
11. 🔄 **规划反哺**（每10章）→ 更新大纲/人物/伏笔
12. 🔍 **审核**（最后）→ 剧情逻辑审查

## 智能上下文（4层分层架构）

写作时按任务类型分层组装上下文，规划文档通过 BM25 检索注入相关片段：

| 层级 | 内容 | 适用步骤 |
|------|------|----------|
| Tier 0 (GLOBAL) | 项目元信息 | 校对、工具步骤 |
| Tier 1 (WORLD) | + 世界设定/角色状态/角色约束 | 规划、推演 |
| Tier 2 (NARRATIVE) | + 剧情摘要/伏笔/反模式/追读力 + **RAG 检索规划文档** | 灵感、润色、审核 |
| Tier 3 (WORKING) | + 章节概要/全文/推演/RAG 检索历史章节 | 章节写作 |

## 大世界状态系统

像游戏存档一样追踪小说世界的结构化数据：

```json
{
  "world": {"locations": {"青云宗": {...}}, "power_system": {"levels": ["炼气", "筑基", ...]}},
  "characters": {"凌尘": {"cultivation": "炼气二层", "hp": 100, "gold": 5, "inventory": [...]}},
  "items_catalog": {"青霜剑": {"effect": "冰系攻击", "uses": -1, "life_save": false}},
  "timeline": [{"chapter": 1, "event": "发现修炼坪异常"}]
}
```

## 质量检查报告

每章自动生成包含 11 项指标的质量报告：

| 指标 | 说明 |
|------|------|
| total_score | 总分 0-100 |
| word_count_score | 篇幅得分 |
| structure_score | 结构得分 |
| dialogue_score | 对话得分 |
| scene_score | 场景得分 |
| rhythm_score | 节奏得分 |
| hook_score | 钩子得分 |
| cool_point_score | 爽点得分 |
| micro_payoff_score | 微兑现得分 |
| consistency_score | 一致性得分 |
| sentence_variety_score | 句式多样性得分 |

## 导出功能

支持将小说导出为多种格式：

| 格式 | 说明 |
|------|------|
| TXT | 纯文本格式，包含目录和所有章节 |
| EPUB | 电子书格式，支持目录导航 |
| PDF | PDF 格式，支持排版和样式 |

导出路径：`data/projects/{project_name}/export/`

## 数据存储

| 文件 | 说明 |
|------|------|
| `data/config.json` | 全局配置（主题、语言、模型） |
| `data/projects/*/meta.json` | 项目元信息（含目标章节数） |
| `data/projects/*/chat.db` | 项目级聊天记录（SQLite） |
| `data/projects/*/world_state.json` | 大世界状态（人物/物品/地点/时间线） |
| `data/projects/*/memory.json` | 长期记忆（11桶） |
| `config/agents.json` | 智能体配置 |
| `config/default_agents.json` | 智能体默认配置（重置用） |

## 日志系统

日志按级别分文件存储，按天滚动，单文件上限 100MB，保留 30 天：

| 文件 | 级别 | 说明 |
|------|------|------|
| `logs/info.log` | INFO / DEBUG | 常规运行日志 |
| `logs/error.log` | WARNING+ | 警告和错误 |
| 控制台 | INFO+ | 带彩色高亮 |

用法：
```python
from novel_writer.core.logger import get_logger
logger = get_logger(__name__)
logger.info("操作完成")
```

## 单元测试

项目包含完整的单元测试，覆盖核心模块：

```bash
# 运行所有测试
python -m pytest tests/ -v

# 运行特定测试
python -m pytest tests/test_quality_checker.py -v
python -m pytest tests/test_reading_power.py -v
python -m pytest tests/test_workflow.py -v
python -m pytest tests/test_exporter.py -v
```

**测试覆盖**：
- 质量检查模块（21 个测试）
- 追读力模块（16 个测试）
- 工作流模块（20 个测试）
- 导出功能（11 个测试）

## 项目目录结构（单个小说）

每个小说项目独立存储为一个目录：

```
data/projects/{project_name}/
├── meta.json                    # 项目元信息（标题、题材、风格、目标字数、目标章节数）
├── chat.db                      # 聊天记录（项目级 SQLite）
├── planning/                    # 规划文档（.md）
│   ├── 立意.md
│   ├── 大纲.md                  # 每10章自动反哺更新
│   ├── 人物设定.md              # 每10章自动反哺更新
│   ├── 世界观.md
│   ├── 时间线.md
│   ├── 主线.md
│   ├── 支线.md
│   ├── 伏笔.md                  # 每10章自动反哺更新
│   └── 目录.md                  # 每章自动更新
├── world_state.json             # 大世界状态（人物/物品/地点/时间线）
├── chapters/                    # 章节正文（.txt）
│   ├── 1_章节名.txt
│   ├── 1_章节名.summary.md      # 章节概要（自动生成）
│   ├── 1_章节名.bak.txt         # 章节备份（写入前自动备份）
│   ├── 1_章节名.outline.md      # 细纲（可选，.md）
│   └── ...
├── inspiration/                 # 灵感记录（.md）
│   └── 3_灵感.md
├── review/                      # 审校产出（.md）
│   ├── 审核报告.md
│   └── 校对报告.md
└── workflow.json                # 工作流进度（断点恢复用）
```

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| Ctrl+N | 新建项目 |
| Ctrl+O | 打开项目 |
| Ctrl+S | 保存项目 |
| Ctrl+, | 外观设置 |
| Ctrl+Shift+W | 开始工作流 |
| Ctrl+Enter | 发送消息 |

## 扩展开发

### 添加新 LLM 供应商

1. 继承 `BaseLLM` 抽象基类
2. 实现 `chat()` 和 `stream_chat()` 方法
3. 在设置对话框中注册供应商

### 扩展智能体

编辑 `config/agents.json`，配置新的 Agent：

```json
{
  "name": "新智能体",
  "title": "🎭 新智能体",
  "system_prompt": "你的系统提示词...",
  "skills": ["写作", "润色"]
}
```

## 更多信息

- [开发指南 (DEV_GUIDE.md)](DEV_GUIDE.md) — 技术架构、项目结构

## 许可证

本项目基于 MIT 许可证开源。

## 打赏支持

如果这个项目对你有帮助，欢迎请作者喝杯咖啡 ☕

| 微信 | 支付宝 |
|:----:|:------:|
| ![微信打赏](assets/wx.jpg) | ![支付宝打赏](assets/zfb.png) |