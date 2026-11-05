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

### 导入

「从小说导入」调用 `DramaStore.sync_assets_from_canon()`：

- 默认导入**人物 + 地点**（`kinds=("character","location")`）；
- 按 `(kind, name)` 去重，重复点不会生成重复行；
- 道具条目可能有上百个，需要时传 `kinds=("character","location","prop")` 再导。

分镜页的「提取资产」（`extract_assets_from_storyboard`）是另一条路：按分镜的
角色/地点列补登记，`notes` 标「从分镜自动提取」。

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
