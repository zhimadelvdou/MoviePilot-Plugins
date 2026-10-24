Status: resolved
Type: task

# 按 API 顺序选择完整 Manko 同番号数据包

## 验收标准

- 同一精确番号命中多个不同原生 ID 时，按 API 顺序选择首个包含所需刮削字段及有效演员列表的完整数据包。
- 返回值只含所选原生 ID 自身的详情和演员；不合并译文、图像、演员或其它字段。
- 若没有完整候选，自动 `search` 返回 `None`，手动 `search_matches` 返回空列表；同步/异步行为一致，日志列出固定缺项字段。
- 保留精确番号和完整总数校验、唯一候选及精确 ID 查询兼容、非 Manko 来源行为。
- Manko 番号缓存切换到版本化策略键，旧号码键不读取且本版不主动清理；ID 缓存和其它来源键不变。
- README、插件/索引版本和规格同步。

## Progress

- 已实现完整字段核验、API 顺序候选回退、原始 packet 返回、无完整候选拒绝及固定缺项诊断。
- 已增加公共四入口选择回归、字段缺失回退、无完整候选、总数不完整、平面标题兼容和缓存策略回归。
- 已更新插件版本至 0.8.8 及 README、规格草案。
- 标准库 Provider replay 已通过；官方 MoviePilot V3 SDK 定向回归 `503 passed, 7 subtests`。
- 0.8.8 已部署生产，73 runtime 文件核验通过；部署清单中 core 有 6 个文件、public 有 1167 个文件；配置/binding revision 9、3 个下载源及对应媒体、3 份 ledger 和两份 metadata 缓存均保持，auto off、unarmed、queue 0。
- 生产 `GET /media/search` smoke 仅使用 Manko：ADN-765 与 ADN-775 各返回 1 项，标题、简介、poster、background、日期与 runtime 字段完整。搜索前后 ledger/cache/config/media 保持；最终部署审计与备份基线一致。证据见 `../../../../outputs/manko-scrape-data-selection-20261024/live-search-smoke.log` 和 `../../../../outputs/manko-scrape-data-selection-20261024/final-audit.jsonl`。
- 无前端改动，未新增前端测试或 build；本轮未做 Chrome 新 UI 验收、preview、transfer 或 scrape。

## Answer

完成按 API 顺序选择首个完整数据包、字段缺失回退、全不完整时同步/异步拒绝、固定字段诊断、缓存策略隔离及部署/现场 GET 搜索验收。正式 SDK 回归通过，ADN-765 与 ADN-775 现场各返回一个完整结果，状态审计保持基线。
