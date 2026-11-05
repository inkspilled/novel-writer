# 短剧素材与 ComfyUI

短剧「资产」页登记人物 / 场景 / 道具三类素材，并负责把它们生成为参考图。

## 资产 ↔ 小说正典

资产不是独立的一套数据，而是**指向小说正典的引用**：

| 资产类型 | 数据来源 | 关联字段 |
|---|---|---|
| 人物 `character` | `canon/characters.json`（角色卡） | `entity` = 角色 `id` |
| 场景 `location` | `world_state.json` 的 `locations` | `entity` = 地点名 |
| 道具 `prop` | `world_state.json` 的 `items_catalog` | `entity` = 物品名 |

资产记录结构（`drama/assets/index.json`）：

```json
{
  "kind": "character",
  "id": "char_顾言",
  "name": "顾言",
  "prompt": "顾言，人物设定参考图，……",
  "ref": "generated/char_顾言.png",
  "status": "ready",
  "notes": "从小说导入",
  "entity": "c1",
  "image": "generated/char_顾言.png"
}
```

- `entity` 是回指正典的键：人物优先存角色卡 id，退化为名称；
- `ref` 给 UI 显示，`image` 是生成图相对路径（两者生成后一致）；
- 表格里没有 `entity` / `image` 列，但**保存时会按 id 合并保留**，不会丢关联。

## 来源：正典 vs 短剧原创

资产表里 `source` 三种，回答「这行是从哪来的」：

| source | 含义 | 怎么产生 |
|---|---|---|
| `canon` | 小说正典里有 | 「从小说导入」；或分镜里的名字**匹配上了**角色卡/地点/物品 |
| `storyboard` | **短剧原创**（小说里没有） | 分镜提取时匹配不上：龙套、临时茶棚、一次性道具 |
| `manual` | 手工登记 | 「＋ 资产」 |

所以出现两种来源是**设计如此**，不是重复：

- 主角/主要场景必须挂 `canon`，改小说设定时以正典为准；
- 龙套和临时场景挂 `storyboard`，`entity` 为空，**允许存在，也不强行塞回小说**。

匹配规则（`match_canon_entity`）：名称归一化（去空格、去「（主角）」这类括号注解）后
精确匹配；再按角色 `aliases` 做包含匹配。命中即回填 `entity`（人物用角色卡 id），
未命中才标原创。

## 一致性怎么保证

1. **同一 `(类型, 归一化名称)` 只留一行**：导入与分镜提取都幂等，重复点不会翻倍；
2. **导入只补缺、不覆盖**：手改过的提示词、已生成的图不会被「从小说导入」冲掉；
3. **保存按 id 合并**：表格里没展示的 `entity`/`image` 不会因为点保存而丢失；
4. **正典改名会暴露出来**：`canon` 资产的 `entity` 对不上任何人时进「正典失联」，
   用「检查资产」列出来，确认后改 `entity` 或删行；
5. **原创资产不参与正典校验**，只要求能生成图，不要求和小说人物表对齐。

## 资产体检（「检查资产」）

| 维度 | 含义 | 处理 |
|---|---|---|
| 来源分布 | 正典 / 原创 / 手工 各多少 | 判断是不是分镜跑出来的龙套太多 |
| 缺提示词 | `prompt` 为空 | 点「生成提示词」 |
| 缺参考图 | `image` 为空 | 点「生成图片」 |
| 正典失联 | `canon` 资产 entity 找不到对应角色/地点 | 改名/删行/重新导入 |
| 重名 | 归一化后同名 | 删掉重复行 |

状态口径：`draft`（刚登记）→ `ready`（已生成图）。没有「剧情质量评分」——
短剧资产只保证**形象可用、和正典不打架**，不评价画风好坏。

### 导入

「从小说导入」调用 `DramaStore.sync_assets_from_canon()`：

- 默认导入**人物 + 地点**（`kinds=("character","location")`）；
- 按 `(kind, 归一化名称)` 去重，重复点不会生成重复行；
- 道具条目可能有上百个，需要时传 `kinds=("character","location","prop")` 再导。

分镜页的「提取资产」（`extract_assets_from_storyboard`）是另一条路：
按分镜的角色/地点列补登记——能对上正典的标 `canon` 并回填 entity，
对不上的标 `storyboard`（短剧原创）。

## 提示词怎么来

`DramaStore.asset_prompt(asset)`：

- **手填优先**：`prompt` 非空就原样用；
- 否则按「名称 + 类型 + 正典摘要 + 题材 + 风格」拼，例如：
  `顾言，人物设定参考图，主角；外形：白衣，仙侠题材，快节奏风格，竖屏短剧，写实电影感，柔和布光，背景干净，高清细节，正面参考`；
- 负向词固定为 `ASSET_NEGATIVE`（低清晰度/畸形手指/文字水印/多余肢体…）。

「生成提示词」按钮只补**空提示词**的行，不会覆盖手改。

## 接入 ComfyUI

### 1. 准备一个「API 格式」工作流

在 ComfyUI 里搭好流程（加载 checkpoint、LoRA、采样器、SaveImage 等），
用 **Save (API Format)** 导出 JSON（不是界面导出那种 `nodes/links` 结构）。

把需要按资产变化的三处输入改成占位符：

| 占位符 | 填到哪个节点 | 说明 |
|---|---|---|
| `{{positive}}` | CLIPTextEncode 的 text | 正向提示词 |
| `{{negative}}` | 负面 CLIPTextEncode 的 text | 负向提示词 |
| `{{seed}}` | KSampler 的 seed | 每张随机；整个值就是占位符时会替换成整数（ComfyUI 只认 int） |
| `{{prefix}}` | SaveImage 的 filename_prefix | 用资产 id，方便回查 |
| `{{width}}` / `{{height}}` | EmptyLatentImage | 默认 768×1344（竖屏） |

例：

```json
"6": {"class_type": "CLIPTextEncode",
      "inputs": {"text": "{{positive}}", "clip": ["4", 1]}},
"3": {"class_type": "KSampler",
      "inputs": {"seed": "{{seed}}", "steps": 20, "cfg": 7, ...}}
```

### 2. 填地址与模板

「设置 → 模型设置 → 图像生成（ComfyUI）」：

- **ComfyUI 地址**：默认 `http://127.0.0.1:8188`；
- **工作流模板**：上面导出的 `workflow_api.json`。

存到 `data/config.json` 的 `comfyui` 段：

```json
"comfyui": {"base_url": "http://127.0.0.1:8188", "workflow": "D:/ai/comfy/workflow_api.json"}
```

### 3. 生成

资产页选中若干行（不选 = 全部）→「生成图片」：

```
ComfyUIClient.generate()
  ├─ load_workflow(template)          # API 格式 JSON
  ├─ fill_workflow(占位符替换)
  ├─ POST /prompt {prompt, client_id} # 返回 prompt_id
  ├─ 轮询 GET /history/{prompt_id}     # 1s 一次，默认 300s 超时
  └─ GET /view?filename=…             # 下载图片字节
```

拿到字节后 `DramaStore.save_generated_image(asset_id, data)`：

- 写入 `drama/assets/generated/{asset_id}.png`；
- 回填 `image` / `ref`，`status` 置 `ready`；
- 表格自动刷新，参考图列显示路径。

单张失败不打断整批（记日志继续），全失败才报错。

## 边界

- 一次只生成**一张图/资产**，不做多 seed 挑选、不做视频；
- ComfyUI 侧模型、LoRA、分辨率都在模板里定，程序只换提示词与 seed；
- 参考图不参与后续分镜生成（暂未做 img2img / 角色一致性锁定）；
- ComfyUI 必须是本机或可访问的 HTTP 服务，不支持跨队列池。
