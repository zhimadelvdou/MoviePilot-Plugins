Status: resolved
Type: task

# FC2 不建演员目录并按番号整理

## 验收标准

- 手动预览/执行及自动 handoff 对可信 FC2 同号身份跳过演员目录和演员缺失阻断；客户端字段不构成证据。
- 非 FC2 缺少演员仍按既有规则失败关闭；配置、generation/boot、文件 fingerprint、目标、身份、重放保护保持。
- FC2 formatter 使用已验证 packet canonical 番号，沿用可信年份；完整 title、NFO title/overview 不变。
- 更新版本至 0.8.19、README、spec 和本 issue；仅改本票源码与测试，不改 dist/旧发布物。

## Answer

FC2 可信 packet 同号身份在手动预览、提交前复核、自动 handoff 和 formatter 事件中校验；整理使用基础目标和 canonical 番号，演员目录请求字段保留用于配置签名。年份只取 owned projected year，无可信年份时 formatter year 置空；格式化只改 formatter 副本，NFO 与 MediaInfo 的完整标题和简介保持。

验证：当前镜像 `bb59...c84` 与当前 SDK 快照隔离测试为 1702 passed、7 subtests、8 warnings；两项 FC2 原生端到端用例通过，覆盖有年/无年、无演员目录、硬链接、NFO 与 JPEG。0.8.19 已部署并完成 audit；配置、core/public、downloaders、三份账本保持，queue 为 0。

线上原生 `preview=true` 已通过：插件启用且 actress_subfolder=true，响应 total=2、success=2、failed=0。视频目标为 `/media/Link/AV/FC2-PPV-4863830/FC2-PPV-4863830.mp4`，字幕目标为同目录 `FC2-PPV-4863830.default.chi.zh-cn.ass`；无演员层、无年份。源视频与 ASS 的前后 hash/stat 均一致，nlink 均为 2；新目标目录预览前后不存在。此次只预览，未执行整理，旧长标题目录未改。

兼容：配置、自动 scope/cutoff、账本字段与 manual intent payload schema 不变；复用原 `actress_subfolder` 字段保持旧 config signature 校验一致。签名的 FC2 bypass 只取本插件 owned packet，精确要求来源、media ID、canonical number 和记录编号集合匹配。
