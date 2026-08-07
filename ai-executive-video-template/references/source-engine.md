# 源工程复刻规则

## 两层锁定

1. 章节层：`assets/source-engine/chapter-blueprints/` 保存真实中间工程，用它把新录屏制作成 15 秒标准章节片。
2. 总片层：`scripts/render_locked_video.py` 负责首页、章节数量布局、最后结果停留、结尾和最终 QA。

不能绕过章节层，直接把未经模板化的录屏交给总片脚本。

## 每次渲染必须留下的中间产物

`output/.locked-template-work/` 必须至少包含：

- `source-snapshot.json`：配置、底图、旁白和章节片 SHA-256。
- `filtergraphs/01_intro.ffscript`。
- `filtergraphs/02_section_XX.ffscript`。
- `filtergraphs/03_end.ffscript`。
- `filtergraphs/04_main.ffscript`。
- `filtergraphs/05_concat.ffscript`。
- `intro.mp4`、`normalized-sections/*.mp4`、`end.mp4`、`main.mp4`。
- `render-commands.json`：实际执行命令。

缺一项均视为工程不完整，不能声称模板已锁定。

## 可复现边界

- 同一输入文件、同一锁定环境和同一脚本，应通过参考母版 SSIM 回归。
- 新内容的画面不与旧母版做全片 SSIM；应对固定背景、首页、结尾和尺寸规则做校验，并人工检查章节看板。
- 云端 TTS 可能随服务版本产生波形差异。生成后的 WAV 必须作为项目输入保留并进入 `source-snapshot.json`；最终渲染不应临时重新调用 TTS。
