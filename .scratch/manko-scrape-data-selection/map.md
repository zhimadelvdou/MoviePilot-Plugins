# Manko 同番号完整数据包选择

## Notes

- [规格](spec.md)：同番号多 ID 时按 API 顺序选择首个完整原始数据包。
- [实施票](issues/01-selection.md)：提供者实现、公共接口回归、号码缓存策略隔离和文档。

## Decisions-so-far

- 完整度只看规格列出的刮削字段；不要求未映射字段一致，不通过跨记录合并“补齐”。
- 完整数据包选择保持 API 顺序；不用最小 ID 或语言数量作为优先级。
- 空选择时同步/异步的自动搜索与手动候选接口一致拒绝。

## Validation

- Provider HTTP replay：四个公开同步/异步入口对 API 首个完整 packet 的选择一致；输出见 `../../../outputs/manko-scrape-data-selection-20261024/selection-contract-green.log`。
- 官方 MoviePilot V3 SDK 定向回归：`503 passed, 7 subtests`；日志为 `../../../outputs/manko-scrape-data-selection-20261024/manko-scrape-selection-sdk-20261024-1.log`。
- 0.8.8 已部署生产，73 runtime 文件核验通过；部署清单中 core 有 6 个文件、public 有 1167 个文件；配置/binding revision 9、3 个下载源及对应媒体、3 份 ledger 和两份 metadata 缓存均保持，auto off、unarmed、queue 0。
- 本版无前端改动，未新增前端测试或 build。
- 生产 `GET /media/search` smoke 仅查询 Manko，ADN-765 与 ADN-775 各返回 1 项且标题、简介、图像、日期、runtime 均完整；搜索前后状态保持，最终部署审计与备份基线一致。记录见 `../../../outputs/manko-scrape-data-selection-20261024/live-search-smoke.log` 和 `../../../outputs/manko-scrape-data-selection-20261024/final-audit.jsonl`。

## Fog

- 本轮未做 Chrome 新 UI 验收，也未执行 preview、transfer 或 scrape。
