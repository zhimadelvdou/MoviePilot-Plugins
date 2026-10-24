# JavBus 匿名接入

## Notes

- 主仓库基线：`main@f0ddf803dd7df8f8574f425d951ddf3bb4419e42`，任务开始时工作树干净。
- `JavBusProvider` 已注册为 `offline_adapter`，匿名传输已验证 TLS、不使用 Cookie；本轮需要补足解析和受限状态反馈。
- 2026-10-25 ROOT 只读实站结果：代理下遇到 `/doc/driver-verify` 重定向或 HTTP 429；没有取得元数据，也未设置 Cookie。该外部门禁仍待后续单独验收。
- 2026-10-25 SDK 验证：定向 504 passed；完整 22 文件 1558 passed/44 failed/8 warnings/7 subtests passed。44 个失败 nodeid 与 0.8.9 基线 SHA-256 `48bbe2a2b6dde3b87fe1067a3378374813917b391ed310e5325c23e6e9514b51` 完全一致。
- FANZA 当前弃用，不改其适配器或可用状态。

## Decisions-so-far

- 保持一个 `sources` 多选控制手动和自动；缺省仍只有 Manko，保存的显式来源不被追加或替换，用户保存选择 JavBus 后才可用。
- 仅可信 Manko 演员标记可支撑默认女优和自动分目录；JavBus 演员不携带可信性别/女优证据。
- 先以合成 SDK 测试确认解析与诊断合同；实站访问门禁由用户后续确认。
- 手动预览失败反馈通过 ContextVar 捕获本次识别的白名单诊断事件，限长后只映射固定中文提示；JavBus 的 HTTP 状态分类不扩展到其它来源。

## Fog

- JavBus 当前代理下是否可稳定返回正常详情 HTML 未知；只有用户已确认“无需登录”，不代表当前访问条件已经通过。
- 正常详情结构中若含登录弹窗/验证码脚本，解析应保持正常；分类回归已覆盖此边界，仍需实站正常页验证。

## Tickets

- [01-javbus-anonymous-integration](issues/01-javbus-anonymous-integration.md) — resolved；匿名实现与离线 SDK 验收完成，实站门禁仍未通过。

## Answer

实现票已解决，代码已具备显式 opt-in 的 JavBus 匿名识别、固定安全诊断和手动预览失败反馈。默认来源仍只有 Manko；图片 host 不自动批准，FANZA 未启用。定向及完整测试结果、候选归档与未部署边界见 [验证记录](../../outputs/javbus-anonymous-20261025/README.md)。
