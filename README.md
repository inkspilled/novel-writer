

# Novel Writer - AI 小说写作桌面应用

多 Agent 协作的小说创作工具，从立意到定稿全流程覆盖。

## 核心特性

- **多 Agent 协作**：7 种专业智能体（主编、策划、写手、校对、审核、灵感、润色），各司其职
- **全流程覆盖**：支持新书立意、全流程写作、续写、查漏补缺、校验五种模式
- **智能上下文**：4 层分层架构 + RAG 检索，按需注入信息，避免 token 爆炸
- **大世界状态**：像游戏存档一样追踪角色、物品、地点、时间线
- **节奏控制**：根据目标章节数自动划分 7 个阶段，防止长篇节奏失控
- **规划反哺**：每 5 章自动更新大纲、人物设定、伏笔、章节细纲
- **质量检查**：10 维规则打分 + 总分，自动评估章节质量
- **按需检索**：写作 Agent 可调用只读工具查规划/前文/记忆/世界状态/伏笔，不必全量塞上下文
- **写作技能包**：开篇、对话、战斗、情感、悬念、连续性 6 套工艺指南，按需加载
- **多视角会审**：每 10 章三视角并行审稿 + 主编聚合，输出可执行修改清单
- **可观测长跑**：步骤级重试/超时/断点、`runs/*.jsonl` 运行日志、全量 token 计量
- **角色隔离**：正典事件 `known_by` + 角色 POV 视图，角色只知道自己经历的事；死亡冻结知识、新角色不带入场前记忆
- **角色管理**：分档/状态/auto_sim/目标/备注可视化，不用手改 JSON；封印后可版本化修订

## 文档

- **[docs/README.md](docs/README.md)** — 算法与规范文档总索引（唯一登记处）
- **[DEV_GUIDE.md](DEV_GUIDE.md)** — 开发指南（技术栈/结构/命令/打包）

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
├── core/                    # 纯 Python 内核（禁 Qt）
│   ├── llm/                 # LLM 多驱动：base/client/claude/ollama/openai_compat/thinking
│   ├── agents/              # base（usage_sink/run_with_tools）/ loop（工具循环）/ tools（只读工具）
│   ├── workflow/            # definition/runner/context/prompts/builtin_steps/sediment/journal
│   ├── canon/               # events.py：append-only 正典事件 + known_by/hash chain
│   ├── characters/          # registry（分档/状态机）/ view（POV 隔离视图）
│   ├── skills.py            # 技能包注册表（项目 > config > 内置）
│   ├── quality_checker.py   # 10 维质量检查
│   ├── reading_power.py     # 追读力系统
│   ├── world_state.py       # 大世界状态快照
│   ├── rag.py               # BM25 + 可选语义检索
│   ├── memory.py            # 11 桶长期记忆
│   ├── chapter_tracker.py   # 四维追踪
│   ├── character_sim.py     # 人物档案解析 / sim 缓存
│   ├── anti_patterns.py     # 反模式禁忌库
│   ├── exporter.py          # TXT/EPUB/PDF
│   └── project_io.py        # 原子读写/项目目录
├── ui/                      # PySide6 界面（按功能域分包）
│   ├── main_window.py       # 主窗口骨架
│   ├── app_shell/           # theme/llm/project/chat/settings/workflow Mixin
│   ├── agent/               # agent_panel + 状态条(office_scene) + agent_dialog
│   ├── editor/              # editor_panel（正文/规划两级导航）+ sidebar
│   ├── workflow/            # WorkflowThread + 迷你进度条
│   ├── project/             # 新建/打开/工作流模式对话框
│   ├── settings/            # 模型/外观设置
│   └── common/              # styles/workers/chat 渲染/控件
├── skills/                  # 6 个内置写作技能包（.md）
├── models/                  # Project / Chapter / Character
├── designsystem/            # 主题 token（四套色板 + 自定义）
└── locales.py               # 中/英 i18n

tests/                       # 300 个单元/回归测试
config/                      # agents/providers 配置
data/                        # 用户数据（gitignore）
```

## 技术栈

- **Python 3.10+**
- **PySide6** — 跨平台 GUI 框架（LGPL）
- **异步编程** — asyncio/await 提升并发性能
- **SQLite（可选）** — 聊天记录默认不落盘；`save_chat_history=true` 才写 `chat.db`
- **BM25** — 规划文档检索算法
- **PyInstaller/Inno Setup** — 桌面应用打包

## 界面布局

```
┌──────────┬──────────────────────────┬────────────────────────────┐
│  侧边栏   │  编辑区                   │  协作区                     │
│ 项目/章节 │  ┌正文┐┌规划┐┌人物┐      │  Agent 状态条（两行）        │
│ 字数统计  │  正文编辑器               │  工作流进度（规划/正文两段）  │
│          │  规划：左列表 + 右编辑器   │  对话 / 快捷动作             │
└──────────┴──────────────────────────┴────────────────────────────┘
```

- `Ctrl+Shift+F` 专注模式：只留正文，再按恢复；
- 正文写作时编辑区实时追加模型流式输出；正文输出 8192 tokens，规划/大纲 2600–3200；MiMo 关闭思考用 `reasoning_effort=none`，避免 model 仍偷偷生成 reasoning；只有 `sim`（角色推演）强制开启思考，其它步骤关闭；
- `max_context_tokens` 是**输入上下文预算**（默认 48K，设置里最高可到 1M），不是模型窗口本身；
- 一级标签：`正文 / 规划 / 世界`；规划文档用左侧列表切换，不再有 10 个标签溢出；
- 自由对话默认折叠，点右上「自由对话」展开；折叠时工作流日志自动占满剩余空间；聊天不落 SQLite；
- `世界` 是独立页面，内含 **人物 / 物品 / 地点** 三个子页；人物分档/状态/auto_sim/目标/备注，物品记录类型/效果/位置/持有者，地点反查在场角色与地面物品；菜单 `Ctrl+Alt+R` 直接切到人物子页；从人物设定建卡时会自动去括号/角色前缀并推断 core/recurring/extra；自由对话带只读工具，可查大纲/正文/记忆/世界状态；
- 智能体状态条按 4 列两行展示，超过 8 个纵向滚动。

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
| 📝 新书立意 | 只生成规划文档，不写正文 | 9 步（立意→大纲→人物→世界观→时间线→主线→支线→伏笔→章节细纲） |
| 📖 新书全流程 | 从立意到审校全流程 | 完整流程（可配置目标章节数） |
| ✍️ 续写 | 从已有章节继续 | 自动检测起始章节 |
| 🔍 查漏补缺 | 补写缺失或空章节 | 按需（自动扫描） |
| ✅ 校验 | 审核 + 校对已有章节 | 2 步 |

### 续写工作流执行顺序

前置（全书一次）：
1. 📑 **章节细纲（前 10 章）** → 正文前先给前 10 章骨架；之后每 5 章由 `update_chapter_outline` 向前滚动补齐 10 章
2. 🔧 **标题校准**（首次）→ LLM 根据正文内容修正所有章节标题

每章执行顺序：
3. 💡 **灵感**（每3章）→ 提供创作方向
4. 🎭 **作者侧推演**（每章）→ 全局视角的角色反应预测
5. 👤 **角色会审**（每章，仅 auto_sim 角色）→ 多角色 POV 提案 + 导演裁决
6. ✍️ **写作**（每章）→ 正文写作，注入角色当前意图
7. 📝 **章节概要 / 🌍 世界状态 / 📑 目录 / 🔍 校对**（每章）
8. ✨ **润色**（每2章）
9. 🎬 **感知抽取**（每章）→ 正文转角色可感知的正典事件
10. 🔒 **章节封印**（可选 `seal_chapters`）→ 正典锚点，后续工作流禁止覆写；手动编辑自动写 `chapters/versions/`，可手动重新封印
11. 🚶 **离场 tick**（每5章，可关）→ 离场角色批量推进，1 次调用/批
12. 📋 **摘要 / 🔄 规划反哺 / 细纲滚动补齐**（每5章）
13. 🧑‍⚖️ **多视角会审**（每10章）→ 三视角并行 + 主编聚合
14. ✅ **质量检查**（每章）
15. 🔍 **审核**→ 全书分批审校 + 聚合

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
| `data/projects/*/chat.db` | 可选：项目级聊天记录（仅 `save_chat_history=true` 时创建） |
| `data/projects/*/world_state.json` | 大世界状态（人物/物品/地点/时间线） |
| `data/projects/*/memory.json` | 长期记忆（11桶） |
| `data/projects/*/canon/events.jsonl` | 正典事件（append-only，角色知识来源） |
| `data/projects/*/canon/characters.json` | 角色注册表（分档/状态/知识冻结） |
| `data/projects/*/runs/*.jsonl` | 工作流运行日志（步骤/耗时/token/工具） |
| `data/projects/*/skills/` | 项目自定义技能包 |
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

```bash
python -m pytest tests/ -q          # 全量：300 passed
python -m pytest tests/test_workflow_runner.py -v
python -m pytest tests/test_character_isolation.py -v
```

覆盖重点：

- 工作流执行器（断点/重试/并发/工具/会审/细纲）
- 角色隔离（正典 known_by、POV 视图、死亡冻结、批量死亡）
- 质量检查、追读力、RAG、导出
- 编辑器数据安全（切项目不丢章）
- 主窗口布局/专注模式/两级导航

## 项目目录结构（单个小说）

```
data/projects/{project_name}/
├── meta.json                 # 项目元信息（标题/题材/目标字数/目标章节数）
├── chat.db                   # 可选：聊天记录 SQLite（默认不创建）
├── world_state.json          # 大世界状态快照
├── memory.json               # 11 桶长期记忆
├── anti_patterns.json        # 反模式禁忌库
├── reading_power.json        # 追读力数据
├── rag_index.json            # BM25 索引（+ rag_vectors.json）
├── workflow.json             # 工作流定义 + 进度（断点恢复）
├── planning/                 # 9 个规划文档 + 目录.md
│   ├── 立意.md / 大纲.md / 章节细纲.md / 人物设定.md
│   ├── 世界观.md / 时间线.md / 主线.md / 支线.md / 伏笔.md
│   └── 目录.md
├── chapters/                 # {n}_{标题}.txt / .summary.md / .outline.md / .bak.txt
├── canon/                    # events.jsonl（正典事件）/ characters.json（角色注册表）
├── skills/                   # 项目自定义技能包
├── runs/                     # 工作流 JSONL 运行日志
├── review/                   # 审核/校对/质量/会审报告
├── summary/                  # 剧情摘要
├── inspiration/              # 灵感记录
└── sim_cache/                # author 推演 + pov/{角色} 推演
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