# 角色生命周期与 POV 推演

> 引擎：`src/novel_writer/core/canon/events.py`、`src/novel_writer/core/characters/{registry,view}.py`
> 工作流：`pov_sim`（内置步骤，默认只有显式开启 `auto_sim` 的角色才会跑）
> 性质：`参考`（设计说明，改动需同步代码与测试）

## 一句话总结

**角色是数据，Agent 是临时实例。** 正典事件 append-only，角色视图只取 `known_by` 自己的事件；角色多了靠分档、选角、离场 tick 控制成本，而不是一人一个常驻 Agent。

## 1. 两条推演链路

| | `sim`（author_sim） | `pov_sim` |
|---|---|---|
| 视角 | 作者/导演 | 角色本人 |
| 输入 | 大纲、全局世界状态、伏笔、工具 | 只有 `CharacterView` |
| 输出 | 角色反应预测、戏剧建议 | `intent/action/dialogue/expected_effects/knowledge_used` |
| 缓存 | `sim_cache/sim_{n}.md` | `sim_cache/pov/{character_id}/sim_{n}.json` |
| 消费者 | 写手 | `character_panel` / 裁判 / 写手 |

铁律：

- `pov_sim` 不读 `planning/`、不读 `world_state.json`、不读 `memory.json`、不给工具；
- `pov_sim` 不读 `sim_cache/sim_{n}.md`（作者侧推演可能含未来/秘密）；
- `author_sim` 可以读 POV 提案，但输出不能回流给 POV。

## 2. 正典事件（`CanonLog`）

`canon/events.jsonl`，每行一个事件，append-only + 哈希链：

```json
{
  "id": "evt_...",
  "chapter": 12,
  "scene": "药圃",
  "type": "action",
  "actor": "楚夜",
  "targets": ["林牧"],
  "observable": "楚夜从药圃离开",
  "hidden": "我去取魔尊传承",
  "visibility": "private",
  "known_by": ["楚夜"],
  "causes": ["evt_..."],
  "timeline": "main",
  "prev_hash": "evt_..."
}
```

规则：

- `observable` 是别人能看到的；`hidden` 永远不进别人的视图；
- `known_by` 可以写角色 ID、姓名或别名（视图构建时会把三者都匹配）；
- `chapter` 晚于当前章的事件不进入视图；
- 死亡角色按 `knowledge_cutoff` 冻结；
- 不允许 update/delete；纠错只能追加补偿事件；
- `verify_chain()` 校验 `prev_hash` 链接与内容哈希，检测手改。

## 3. 角色注册表（`CharacterRegistry`）

`canon/characters.json`：

```json
{
  "version": 1,
  "characters": [
    {
      "id": "char_林牧",
      "name": "林牧",
      "aliases": ["牧哥"],
      "tier": "core",
      "status": "alive",
      "first_seen": 1,
      "last_seen": 12,
      "location": "灵药圃",
      "goals": ["通过杂役考核"],
      "public_profile": {"identity": "青玄宗外门弟子"},
      "private_profile": {"personality": "谨慎", "motivation": "查清符文", "weakness": "重情"},
      "relationships": {"楚夜": {"belief": "生死之交", "trust": 0.8}},
      "state": {"cultivation": "炼气三层", "injuries": ["左臂骨裂"]},
      "auto_sim": true,
      "knowledge_cutoff": 0,
      "death_chapter": 0
    }
  ]
}
```

### 3.1 分档（LOD）

| 档位 | 谁 | 推演 |
|---|---|---|
| `core` | 主角/常驻 3–8 人 | 每场可 POV |
| `recurring` | 重要配角 | 在场才推演，离场低频 tick（规划中） |
| `extra` | 群演/背景 | 不建 Agent，只作为观察者 |

`auto_sim` 默认 **false**：即使建了卡，也不会自动跑 POV；必须显式打开（后续 UI 提供勾选）。

### 3.2 状态机

```
alive → dead → resurrected / ghost / missing
```

- `mark_dead(chapter)`：`status=dead`、`knowledge_cutoff=chapter`、`auto_sim=false`；
- 死亡角色 `can_act=False`，不会进入 `simulatable()`；
- `resurrect(chapter)`：恢复行动，但 `knowledge_cutoff` 保留——死后发生的事他不知道，除非之后有告知事件；
- 需要让复活角色知道死后的事时，显式追加 `knowledge` 事件并调整 cutoff（后续实现）。

### 3.3 新角色入场

1. `ensure(name, chapter)`：先建 `extra` 临时卡，不生成完整人设；
2. 满足条件（≥2 场 / 与核心对话 / 有目标 / 用户标记）后 `promote`；
3. 补 backstory 事件（`type="backstory"`，`known_by=本人`）；
4. 视图按 `first_seen` 过滤：入场前的目击事件不会因为 `known_by` 误填而泄漏；
5. 别名通过 `merge` 合并，不建第二张卡。

## 4. POV 视图（`CharacterViewBuilder`）

```python
builder = CharacterViewBuilder(project_dir)
view = builder.build("林牧", chapter=12)
print(view.text)
```

视图只包含：

- 自己的公开档案 + 自己的私人设定（动机/弱点/背景）；
- 自己的状态（境界/伤势/位置/背包）；
- 自己的目标、自己视角下的关系；
- `known_by` 含自己的事件（别人的 `hidden` 被剥离）；
- 自己的判断（允许是错的）。

视图不包含：

- 大纲/章节细纲/伏笔；
- 其他角色的属性、背包、秘密；
- 全局世界规则（除非通过事件得知）；
- “你不知道 XX”这类负向提示。

`validate_knowledge_used(proposal, view)` 检查提案引用的 `knowledge_used` 是否都在 `view.event_ids` 内；不在则拒绝。

## 4.1 感知抽取（`percept_extract`）

每章正文落盘后（polish 之后）执行，把正文转成 `CanonEvent`：

- `observable`：旁人能看到/听到的客观行为；
- `hidden`：只有本人知道的真实意图；
- `known_by`：只在场的角色；不在场的人不能凭空知道；
- 每章最多 12 条；同一章已抽过则跳过（幂等）；
- 角色注册表为空时，先从 `planning/人物设定.md` 建 extra 卡（不会自动开 POV）。

写手上下文会读取 `sim_cache/pov/{id}/sim_{n}.json`，注入
`=== 角色当前意图（作者参考） ===`，让角色提案真正影响本章写作。

## 4.2 角色会审（`character_panel`）

每章写作前，仅对显式 `auto_sim` 的角色：

1. 选角（最多 4 人，`simulatable` 优先），提案并行 fan-out；
2. 每个角色用隔离 `CharacterView` 写提案（intent/action/dialogue/knowledge_used）；
3. 裁判用 `validate_knowledge_used` 拒绝引用视图外事件；
4. 导演拿到本章公开场景 brief（大纲/细纲片段，仅导演可见）+ 提案，按 drama/consistency/surprise 评分，选/合并一个走向；
5. 落盘 `sim_cache/pov/panel_{n}.json` + 每个角色 `sim_{n}.json`；
6. 写手上下文注入「角色当前意图 + 导演裁决」。

成本：N 个 auto_sim 角色 ≈ N+1 次调用/章；默认没有 auto_sim 时 0 次。

## 4.3 章节封印（`seal_chapter`）

- `project_info.seal_chapters=true` 时，正文定稿后把 SHA256 写入
  `canon/events.jsonl` 的 `chapter_sealed` 事件；
- 封印后工作流拒绝覆写该章（`ChapterWriteRejected`）；
- 封印后手动编辑走 `chapters/versions/`：原件不动，`index.json` 指向最新版本；重新打开项目展示最新版。
- 手动版本目前不重新封印；重新定稿需要显式“封印当前版本”操作（下一步）。

## 5. `pov_sim` 步骤

- 默认工作流改用 `character_panel`（每章，N+1 次调用，仅 auto_sim）；`pov_sim` 保留给自定义工作流（每 5 章，零额外导演调用）；
- 只跑 `auto_sim=True` 且 `can_act` 的角色，默认最多 4 人；
- 每个角色一次独立 LLM 调用，`keep_history=False`，临时 `BaseAgent`，不共享历史；
- 输出 JSON，校验 `knowledge_used` 后落盘；
- 无可用角色时直接跳过，**零调用**；
- 提案经 `character_panel` 裁判/导演后注入写手上下文，间接影响正文。

## 5.1 离场 tick

- 每 5 章 1 次批量调用，最多 8 个离场角色；
- 只写 `sim_cache/offscreen/tick_{n}.json`，不产生正文；
- 下一章上下文注入「离场角色近况（作者参考）」；
- `enable_offscreen_tick=false` 可关。

## 6. 角色多了怎么办

| 问题 | 做法 |
|---|---|
| 100 个角色 | 注册表只是数据；`simulatable()` 只取 `auto_sim` + 能行动的，默认 4 人 |
| 新角色不断加入 | `ensure` 临时卡；满足条件才 `promote`；入场前事件过滤 |
| 离场角色 | “世界 tick”：每 5/10 章只处理有活跃计划的角色；**8 是 LLM 调用上限，不是角色数上限**；状态变化/批量事件不计入 |
| 死亡角色 | 不进 POV；靠闪回/遗策/他人记忆推动剧情 |
| 别名/重复 | `merge` 合并，保留 alias 列表 |
| 成本 | 无 `auto_sim` = 0 调用；每 5 章最多 4 次；面板另算 |

### 6.1 一次杀二三十个主要配角怎么办

**死亡是状态变化 + 正典事件，不是 30 次角色推演。** 处理分三档：

| 要处理的事 | 方式 | LLM 调用 |
|---|---|---|
| 30 个角色死亡 | 1 个 mass death 事件 + `mark_dead_many(..., cause=...)`，一次落盘 | **0** |
| 主角/凶手/幸存者目睹 | 3–5 个在场角色的 POV / 反应 | 3–5 |
| 势力/政治后果 | 1 次 `author_sim` 或导演评估 | 1 |
| 其中 2–3 个重要角色的遗策/遗物 | 追加 legacy 事件（可批量） | 0–1 |
| **合计** | | **约 4–7 次，而不是 30 次** |

代码已经提供 [`mark_dead_many()`](../src/novel_writer/core/characters/registry.py)：

- 30 个角色 → 30 次内存状态更新 + **1 次 `save()`**；
- 所有死者 `knowledge_cutoff=死亡章`、`auto_sim=false`；
- `pov_sim` 的 `simulatable()` 为空 → **0 次调用**；
- 死者的秘密保留在正典 `hidden` 里，只有 `known_by` 的人知道；后续可用遗书/残魂/调查事件揭示。

**“离场”不是排队等推演。** 离场只是 `last_seen` 不再更新；没有活跃目标的角色不占 tick 名额。8 个名额是“这一轮最多给几个活着的离场角色各发一次 LLM 询问”，不是“只能有 8 个离场角色”。

如果 30 个主要配角都活着、且都需要在同一时间独立行动，那不是 tick 容量问题，而是**群像结构问题**：应合并为势力/代表性角色，或改成批量 prompt（一次处理 10 个低细节角色）。30 条独立 POV 线读者也跟不住。

## 7. 当前边界（诚实说明）

- `canon/events.jsonl` 由 `percept_extract` 自动写入；但感知抽取依赖 LLM，仍可能漏事件/把旁白当 observable；
- `pov_sim`/`character_panel` 默认没有角色开启 `auto_sim`，所以实际不会调用；可在「角色管理（Ctrl+Alt+R）」里打开；
- 离场 tick、角色 panel、裁判/导演评分、UI 花名册尚未实现；
- `mark_dead_many()` 已实现，但“批量死亡正典事件”的自动写入（如灭门事件）还要等感知抽取层；
- `CharacterView` 的 `scene_brief` 需要调用方保证是公开信息；
- 角色自己的 `private_profile.background` 可能包含别人的秘密（人物设定写法导致），建卡时需要人工/LLM 拆分。

## 8. 测试

`tests/test_character_isolation.py`（12 例）：

- 正典 append-only / hash chain / tamper 检测；
- `known_to` 只返回该角色知道的事件；
- 视图不含别人的 `hidden`、未来事件、大纲/主角/负向提示；
- 死亡知识冻结；
- 新角色看不到入场前事件；
- `knowledge_used` 越界拒绝；
- POV handler 只在 `auto_sim` 时调用、死亡角色零调用；
- 100 个角色只有 3 个可推演；
- 30 个主要配角批量死亡：1 次落盘、0 次 LLM、后续 POV 零调用。
