# Issue tracker: Local Markdown

本仓库的工程 Issue 与规格文档使用本地 Markdown。

## 约定

- 每个功能一个目录：`.scratch/<feature-slug>/`。
- 规格文档：`.scratch/<feature-slug>/spec.md`。
- 每张实施票单独保存：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，从 `01` 编号。
- Issue 顶部使用 `Status:` 记录分类状态，名称见 `triage-labels.md`。
- 评论和讨论追加到文件底部的 `## Comments`。
- Skill 要求“发布到 issue tracker”时，在对应功能目录创建文件；要求“读取相关 ticket”时，读取指定的 Issue 文件。

## Wayfinding

- 地图：`.scratch/<effort>/map.md`，记录 Notes、Decisions-so-far、Fog。
- 子票：`.scratch/<effort>/issues/NN-<slug>.md`；`Type:` 为 research、prototype、grilling 或 task。
- 依赖：顶部 `Blocked by: NN, NN`；所有前置票为 `resolved` 后才可领取。
- 按编号选择未关闭、未被领取且无阻塞的票；开始工作前保存 `Status: claimed`。
- 完成后追加 `## Answer`，改为 `Status: resolved`，并在地图中追加结论与链接。
