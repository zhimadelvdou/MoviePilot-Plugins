Status: resolved
Type: task

# 使用 Manko 原始演员可信标记确定女主演

## 验收标准

- 仅本次识别 Manko packet `actors` 中 `avdbs_actor is True` 的安全姓名，且须精确属于当前 MediaInfo 演员集合；整数 1、字符串 `true`、false、空值均不算。
- 自动整理与手动预览默认按来源顺序选择首位可信候选；手动流程可选择其他可信候选并重新预览；无候选时等待可信标记，不允许从 cast 旁路选择。
- 手动预览、执行重核和 qBittorrent 自动整理共用此证据。证据仅绑定本次识别的来源、MediaInfo ID 和番号，不作为公开 MediaInfo/NFO 字段持久化。
- 执行时若选择演员不再有当前可信标记，旧预览必须 fail closed；缓存识别不能以旧证据替代本次 packet。
- 所有 cast 仍写入 NFO；其它来源既有行为、路径选择、整理方式和 `actress_subfolder` 开关保持不变。
- README、版本 0.8.9、dist 和相关规格/票据同步更新。

## Progress

- 已检查领域规格、识别投影、手动预览/执行及自动交接入口；`CONTEXT.md` 与 `docs/adr/` 当前不存在。
- 测试边界：`recognize_media` 当前 packet seam、手动 `preview/execute` 公共接口、自动 handoff coordinator 公共入口及现有 NFO 生成 seam。
- 已实现严格 `is True` 的本次 packet 候选证据、cache hit/fresh cache 隔离、手动与自动重校验，以及完整 NFO cast 保留。
- 官方 MoviePilot V3 SDK 候选 1 为 `366 passed, 7 subtests, 6 failed`；失败均命中新增断言：运行镜像的 `MediaInfo.to_dict()` 会导出动态私有属性。保留该断言，将证据改存插件实例内按 `id(media)` 索引的弱引用映射，核验对象身份与 generation，并在重载/停止时清空。
- 官方 MoviePilot V3 SDK 候选 2 通过：`372 passed, 7 subtests`；前端 `44/44`，production build 成功。当时现场验证待完成，最终结果见下述记录。
- 0.8.7 插件-only 部署后新版页面已加载。ABF-382 未手选演员，文件名识别返回 1 项成功、0 项失败，预览目标为 `/media/Link/电影/鈴村愛裏/ABF-382 (2026)/ABF-382(2026).mp4`；仅 preview，未 execute。现场材料：`../outputs/manko-source-actress-flag-20261024/ui-abf382-auto.txt` 与同名 `.jpg`。
- ADN-765 本轮在来源身份核验阶段停止：search candidates=2、detail 200 两条、`ambiguous_match` count=2，未进入女主演筛选。该预览不作为女主演规则验收结果；本轮不放宽原有身份去重，也不扩展修复身份歧义。现场材料：`../outputs/manko-source-actress-flag-20261024/ui-adn765-identity-ambiguous.txt`、同名 `.jpg` 与 `preview-diagnostics.json`。
- 插件-only 部署审计确认核心为 6、`/public` 共 1167 个文件、配置与绑定 revision 9、三份媒体及源保持；autooff、queue 0。preview 后 ledger/cache 的预期变化不计为部署破坏。审计文件：`../outputs/manko-source-actress-flag-20261024/final-preview-audit.jsonl`。
- ABF-382 在 0.8.6 阶段曾完成一次 Chrome 手动整理且源/目标同 inode，但 NFO 导演、片商为空，整体元数据审计未通过；这不是本次 0.8.7 执行结果。本次只预览，未重新整理。历史见 `../records/manko-chrome-abf382-2026-10-24.md`。

## Answer

已完成规则实现、SDK 与前端验证及插件-only 部署。ABF-382 的单候选自动选角预览通过；ADN-765 因既有来源身份歧义未到女主演规则，不能算作该规则通过或失败。两项现场均只预览，未执行媒体整理。
