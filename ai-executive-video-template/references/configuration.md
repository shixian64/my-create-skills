# 配置文件说明

`config.json` 只允许修改内容字段，模板参数由脚本锁定。

```json
{
  "template_id": "sageroad-ai-executive-v1",
  "intro_hint": "内部 AI 应用进展",
  "title": "AI 正在融入研发日常",
  "intro_audio": "content/audio/intro.wav",
  "end_value": "工程师聚焦验证 质量与决策",
  "sections": [
    {
      "stage": "开发环节",
      "title": "代码辅助编写",
      "clip": "content/sections/01_代码辅助编写.mp4",
      "value_title": "开发起步更快",
      "value_detail": "需求到可运行代码"
    }
  ],
  "output": "output/AI研发进展汇报.mp4"
}
```

## 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `template_id` | 是 | 必须为 `sageroad-ai-executive-v1` |
| `intro_hint` | 是 | 首页小标题，建议保持默认 |
| `title` | 是 | 首页主标题，建议保持默认 |
| `intro_audio` | 是 | Cherry 首页旁白 WAV |
| `end_value` | 是 | 结尾管理价值句 |
| `sections` | 是 | 1–8 个章节 |
| `sections[].stage` | 是 | 首页卡片上方小字，如“开发环节” |
| `sections[].title` | 是 | 章节名称，建议不超过 10 个汉字 |
| `sections[].clip` | 是 | 已完成的 15 秒标准章节片 |
| `sections[].value_title` | 是 | 结尾卡片主结论，建议不超过 8 个汉字 |
| `sections[].value_detail` | 是 | 结尾卡片说明，建议不超过 12 个汉字 |
| `output` | 是 | 最终 MP4 输出路径 |

路径相对于 `config.json` 所在目录解析。不要在配置里增加坐标、字号、颜色、时长或转场字段；这些参数故意不开放。
