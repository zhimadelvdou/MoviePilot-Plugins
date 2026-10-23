Status: resolved
Type: task

# 实现按女主演分目录

## 验收标准

- 增加默认关闭的“按女主演分目录”配置；手动页默认继承该值并能覆盖本次操作。
- 自动与手动路径采用“所选媒体库 / 女主演 / 原生影片目录 / 视频”，由原生 transfer 负责最终影片目录及硬链接、改名和刮削。
- 只以已验证的官方 `女優` 行证据自动确定唯一女主演；多位、缺失或不匹配时停止原生预览/执行并提供演员候选人工选择。不得从演员顺序或名字推断性别。
- 执行重核来源和媒体身份，且必须使用重新预览得到的同一目标；姓名与演员子目录满足路径和符号链接安全约束。
- 自动流程无法确定时留下 `primary_actress_choice_required` 待人工处理状态，不自动重试或完成。
- 旧缓存、关闭开关和旧预览维持安全行为；配置签名包含新开关；公开 MediaInfo 与 ledger schema 不变。
- 版本更新至 0.8.4；相关测试通过并完成静态检查。dist 由 Root 构建。

## 首个 RED

在真实 MoviePilot SDK 测试环境，用离线合成插件媒体项验证：开启女主演目录但缺少官方唯一女優证据时，手动预览返回需人工选择和本片演员候选，并且不调用原生 transfer。测试只使用公开手动预览入口和 transfer seam。

## 范围

仅插件源码、其 web/src 与 web/tests、相关 tests、README/package.v3.json 和本功能 spec/issue。不得访问网络或 NAS，不修改 MoviePilot 核心、Inputs 或其它插件；dist、测试构建、打包与部署由 Root 负责。

## Comments

## 验证记录

- [x] 官方 MoviePilot V3 SDK 全量离线合成回归：`1552 passed, 7 subtests`（Root，2026-10-23）。
- [x] 合成 real-native 执行及完整刮削覆盖；Vue 组件测试 `36/36`，dist build 成功（Root，2026-10-23）。这些执行/刮削均为隔离合成测试。
- [x] 现场部署 `0.8.4`、运行文件清单和状态不变量核验（Root，2026-10-23）：57 runtime files；core6/public1167 不变；配置 apply 时不变、binding revision 9；三个 ledger 不变。config-resume-v3 单次 PUT 200，仅启用 `actress_subfolder`，自动流程关闭且 queue=0。UI 勾选状态已核验（截图见工作空间根目录 `outputs/manko-actress-layout-20261023/ui-actress-enabled.png`）；默认目标仍为 `/media/Link/电影`。
- [x] 现场 NAS 对 ADN-765/775 的无 ID、无人工选择 preview 均成功，目标分别为 `/media/Link/AV/美園和花/ADN-765 (2026)/ADN-765(2026).mp4` 与 `/media/Link/AV/栗山莉緒/ADN-775 (2026)/ADN-775(2026).mp4`。审计通过：manifest/source 匹配、手动 ledger integrity OK、handoff/auto ledger 不变、源 stat+nlink=2 保持，既有平铺目录、sidecars 与 AV root 不变。
- 现场验收范围限于部署、UI 和两个 NAS preview；没有在 NAS 执行新的整理/迁移，也没有创建新的 NAS 目录。因此 issue 实现与预览验收完成，但真实 NAS 执行落盘和副产物结果不属于本票已验证范围。

## Answer

按女主演分目录实现、隔离合成验证和现场部署/preview 核验均已完成。两项真实 NAS preview 能在无 ID、无人工选角时解析出演员目录目标；本次没有执行真实 NAS 整理或迁移，未验证新目录创建、硬链接/改名及刮削副产物落盘。
