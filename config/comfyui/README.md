# 本机短剧图片工作流

两条流程都使用已安装的 Qwen-Image 2.1 / qwen3vl_8b / Qwen2.1 VAE，不需要新下载模型。

- `drama_portrait_ui.json`：可直接导入 ComfyUI 的定妆图编辑版。
- `drama_reference_ui.json`：可直接导入 ComfyUI 的参考图一致性编辑版。
- `drama_portrait_api.json`：软件首次生成时用的 API 模板。
- `drama_reference_api.json`：软件上传定妆图后用的 API 模板，LoadImage 输入为 `{{reference_image}}`。

分辨率不写死：软件按资产类型传 `{{width}}`/`{{height}}`——
人物 960×1280（3:4 竖屏）、场景环景 2048×1024（2:1）、道具 1024×1024（1:1）。
20 步，euler/simple，CFG=1，缓存放 CPU/int8，适配本机 RTX4060 8GB。
参考图流程保持首张参考图尺寸。
**注意**：CFG=1 时负向提示词实际不生效——类型/外观/排除项必须写进正向词，
软件的 `ASSET_PROMPT_PLANS` 已按正面式表述设计；要真正用负面词把 CFG 调到 3–7。

软件中「模型与智能体 → 图片生成 API → ComfyUI → 使用内置短剧工作流」可选用模板。
本机服务地址是 http://127.0.0.1:8188；ComfyUI 启动后才能生成。

人物的正面、左侧、右侧和正后方分别保存在 `drama/assets/generated/makeup/`。
侧面和背面生成时使用同一张正面照；「定妆图（固定）」列可填已有外观参考，用于新正面照。
参考图画幅与生成目标不同时，软件会留边适配目标画布。
不同资产有不同且稳定的种子；种子本身不能锁定人物，主要由参考图约束外观。

这是静态图片流程，参考图能改善一致性，仍需人工验图；不包含视频生成或多人物镜头合成。
