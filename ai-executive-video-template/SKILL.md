---
name: ai-executive-video-template
description: 以锁定的萨瑞企业视觉模板制作或复刻 AI 研发进展汇报视频，支持 1 至 8 个可变章节。用于同事提供新的录屏、旁白或章节数量后，保持画布、背景、字体、颜色、字号、字幕条、转场、Cherry 音色、首页与结尾样式完全一致，只替换内容并执行自动验收。
---

# AI 研发汇报视频锁定模板

## 核心约束

- 先读取 `references/template-contract.md`；它是不可修改的模板合同。
- 再读取 `references/source-engine.md`；不允许只交最终章节片而缺少源工程和中间产物。
- 章节数量允许为 1 至 8，不得写死为三段。
- 只允许修改配置中的内容字段和 `content/` 素材。
- 不得改 `assets/locked/`、字体、坐标、字号、颜色、转场和技术参数。
- 新章节必须先制作成 15 秒的标准章节片，再进入总片组装。
- 旁白默认使用 Qwen3-TTS `Cherry`；不得混用其他音色。
- 首页和结尾使用“本次展示/当前展示”口径，不使用“已覆盖”。
- 面向管理层的类别统一使用“代码检查”；不要在同一层级混用“代码审查”。

## 工作流

1. 运行 `scripts/new_project.py <目标目录>` 创建项目副本。
2. 从 `assets/source-engine/chapter-blueprints/` 复制最接近的章节蓝图，按 `references/content-workflow.md` 替换录屏和文案。
3. 保留 ASS、FFmpeg filtergraph、旁白文本/音频、阶段片和包装片，再导出 1920×1080、30fps、15 秒的标准章节片。
4. 使用 `scripts/generate_qwen_tts.py` 生成 Cherry 旁白；旁白只读自然中文，不朗读路径、命令或符号。
5. 修改项目中的 `config.json`。字段说明见 `references/configuration.md`。
6. 先运行 `scripts/validate_template.py --config <config.json>`，失败时停止渲染。
7. 运行 `scripts/render_locked_video.py --config <config.json>`。
8. 检查输出目录中的 MP4、`qa.json` 和 `qa_frames/`。
9. 检查 `output/.locked-template-work/` 中间工程是否齐全。
10. 首次部署或脚本变更后运行 `scripts/rebuild_reference.py`，必须通过参考母版 SSIM 回归。
11. 对照 `assets/reference/master_reference_3_sections.mp4` 与 `master_contact.jpg` 做人工终检。

## 可变与不可变

允许变化：

- 章节数量（1 至 8）
- 章节名称、阶段名称、价值标题、价值说明
- 录屏内容、旁白文本、字幕内容
- 输出文件名

禁止变化：

- 萨瑞背景底图与品牌位置
- 1920×1080、30fps、H.264/AAC、48kHz 单声道
- Microsoft YaHei 字体及固定字级
- 首页 8.3 秒、章节 15 秒、最后章节结果停留、结尾停留与淡入
- 页头、字幕条、卡片、价值看板的颜色与坐标
- Cherry 音色、企业汇报语气和音量目标
- `assets/source-engine/` 中的坐标、颜色、字号和转场结构

## 可变章节数量规则

- 1 至 4 个章节：首页和结尾使用单排卡片。
- 5 至 8 个章节：首页和结尾使用双排卡片。
- 卡片位置由渲染脚本计算，不允许手动拖动。
- 超过 8 个章节时拆分成多支视频，不缩小字体。

## 验收门槛

- `assets/locked/manifest.json` 哈希校验通过。
- FFmpeg 版本和 Microsoft YaHei Bold 字体哈希与 `environment-lock.json` 一致。
- 所有章节均为 15 秒标准章节片。
- 中间工程、filtergraph、命令记录和输入哈希快照完整。
- 最终视频完整解码，无持续黑帧。
- 最终视频分辨率 1920×1080，帧率 30fps。
- 音频为 AAC、48kHz、单声道；目标综合响度约 -18 LUFS，峰值不高于 -2 dBFS。
- 首页、首个章节、最后结果页和结尾抽帧无文字截断、重叠或模板漂移。

## 交付说明

这是 FFmpeg 锁定模板，不是 HyperFrames 工程，因此 `hyperframes lint/inspect` 不适用。以脚本配置校验、资源哈希、`ffprobe`、完整解码、黑帧检查、响度检查和抽帧检查作为等价验收。
