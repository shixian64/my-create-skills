# 源工程与中间产物

这里保存的不是成片截图，而是母版实际制作过程中留下的可编辑工程层。

## 目录

- `chapter-blueprints/code-writing/`：代码辅助编写章节的 ASS、FFmpeg filtergraph、旁白文本、旁白音频、阶段片和 PPT 包装片。
- `chapter-blueprints/code-check/`：代码检查章节的传统阶段、AI 阶段、价值阶段、转场工程和全部中间片。
- `chapter-blueprints/problem-analysis/`：问题分析章节的传统阶段、AI 阶段、价值阶段、转场工程和全部中间片。
- `master-composition/`：首页、总字幕、结尾、45 秒章节母片及总片滤镜脚本。
- `voice/`：最终 Cherry 旁白 WAV、旁白生成脚本及声音参数。
- `original-source-map.json`：原始录屏路径、时长、大小和 SHA-256 证据。

## 为什么不复制三支原始录屏

三支原始录屏合计约 2.35GB，而且它们正是同事复刻时必须替换的“内容”。模板真正需要锁定的是裁切逻辑、画面框架、看板、字体、字幕、时间轴、转场、旁白规则及可回归母版；这些已全部保存在本目录和 `scripts/` 中。原始录屏的身份使用 SHA-256 留证，没有把内容文件伪装成模板资产。

## 章节源工程通用链

1. 把新录屏作为 FFmpeg 输入 0，把对应旁白作为输入 1。
2. 复制最接近的章节蓝图，只改 `trim/crop` 和 ASS 文案，不改颜色、坐标、字号、面板尺寸和转场时长。
3. 先生成传统、AI、价值三个阶段片；不适用的阶段仍需用同版式结果镜头补齐 15 秒结构。
4. 使用 `ppt_*.ffscript` 加企业背景和外置字幕。
5. 使用 `combine*.ffscript` 以 0.25 秒转场合成标准 15 秒章节片。
6. 标准章节片再交给 `scripts/render_locked_video.py` 组装总片。

从模板根目录执行 FFmpeg。所有复制后的 filtergraph 已改成模板内相对路径，因此 ASS 和工程文件不会再依赖原 `work/` 目录。

## 参考母版重建

```powershell
python scripts/rebuild_reference.py --workspace D:\Temp\ai-video-reference-rebuild
```

重建目录会保留 `intro.mp4`、每章标准化 MP4、`end.mp4`、`main.mp4`、全部 `.ffscript`、输入哈希快照、完整命令记录、QA 抽帧和最终 MP4。最后自动与锁定母版做 SSIM 回归；低于门槛直接失败。
