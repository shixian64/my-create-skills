# 参考母版重建证据

此目录由 `scripts/rebuild_reference.py` 从 `assets/project-template/` 全新创建，不是手工复制最终成片。

本次结果：

- 重建成片：55.333333 秒。
- 锁定母版：55.366667 秒。
- 时长差：0.033334 秒。
- 全片 SSIM：0.990580。
- 验收门槛：0.985000。
- 结论：通过。

`project/output/.locked-template-work/` 内完整保留首页、标准化章节、结尾、正文、五类 filtergraph、输入 SHA-256 快照和真实命令记录。`project/output/qa_frames/` 保存关键帧抽检结果。

绝对路径出现在命令记录中只是本次构建证据；同事复刻时应重新运行 `scripts/rebuild_reference.py`，生成属于当前电脑的新证据目录。
