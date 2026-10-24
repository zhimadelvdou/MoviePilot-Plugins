# 女主演目录

## Notes

- [规格](spec.md)：女主演目录资格由本次 Manko 原始演员 packet 的可信字段确定。
- [首轮布局实施票](issues/01-layout.md)：已有布局、官方证据与原生 transfer 边界。
- [来源演员标记实施票](issues/02-source-actor-flag.md)：以 `avdbs_actor is True` 限定自动与人工女主演候选。

## Decisions-so-far

- 只把本次识别结果中 `packet.actors[*].avdbs_actor is True` 的安全姓名作为女主演候选，并要求姓名与当前识别 MediaInfo 演员精确对应。
- 不删除任何 cast；女主演资格不改变 NFO 演员写入。
- 执行重校验时，自动选择仍须唯一且为原选择；手动只要求所选演员仍匹配且标记为 true，不相关候选变化不使选择失效。

## Validation

- SDK 候选 2：`372 passed, 7 subtests`。候选 1 的 6 个失败均为 SDK 序列化暴露 MediaInfo 动态属性；改用插件内弱引用证据映射并验证 generation 后通过，未删除或放宽断言。
- 前端 `44/44`，production build 成功。
- 0.8.7 插件-only 部署及新版页面现场：ABF-382 未手选演员，按文件名识别后 1 success/0 failed，预览目标 `/media/Link/电影/鈴村愛裏/ABF-382 (2026)/ABF-382(2026).mp4`；未执行整理。
- ADN-765 现场预览在女主演筛选前被既有身份歧义阻断（2 个候选、两条 detail 200、`ambiguous_match` count=2），不作为本规则的通过或失败；保持既有身份去重，本票未扩展该修复。
- 目前已有 ADN-765、ADN-775 与 ABF-382 三部媒体完成过硬链接整理；ABF-382 的成功整理发生于 0.8.6，NFO 导演/片商缺失使元数据审计未通过。0.8.7 本轮只对 ABF-382 做预览，没有重新整理。
- 部署审计：核心 6、公开插件 1167、配置/绑定 revision 9、三份媒体与源保持，autooff、queue 0。preview 造成的 ledger/cache 预期变化不视为部署损坏。

## Fog

- 本票规则已通过离线验证并随 0.8.7 插件-only 部署。ABF-382 现场预览覆盖单候选自动选择；ADN-765 未到候选筛选阶段。所有本轮现场操作均仅预览，不代表媒体整理或执行流程已验收。
