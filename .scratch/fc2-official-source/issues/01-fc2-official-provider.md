# FC2 official product provider

Status: resolved
Type: task

## Goal

Implement the FC2 official product page as a conservative metadata provider in `plugins.v3/xiaojiejiescraping`, register it as `fc2` / `FC2官方`, document source limits, and bump plugin version from 0.8.17 to 0.8.18.

## Authorization and boundary

The user explicitly authorized deployment and enabling after tests pass. This worker is assigned local implementation and offline tests only: no network, deployment, instance config changes, Git commit, UI/dist edits, or changes outside the FC2 plugin/tests/README/release records and this ticket.

## Acceptance

- Sync/async search and fetch; identity exactness; JSON-LD Product in arrays and `@graph`; conflicting or ambiguous IDs fail closed.
- Reject non-FC2 and invalid IDs before requesting. Respect anonymous transport limits, HTML MIME, disabled redirects, official exact host/path, and existing image host allowlist.
- No actor/date inference; synthesize safe fixtures only.
- Integrate source registry/settings and source ordering without changing ordinary number routing.
- Run focused offline tests using README bootstrap where available, plus `py_compile` and `git diff --check`.

## Evidence

Base commit `224923422b4b98fbccefa512916860f54add00e4`; branch `scraping/feature/fc2-official-source`; worktree started clean. User supplied verified 2026-10-28 response contract. No network access is authorized for this worker.

## Comments

- 2026-10-28：Root 保存的只读真实候选诊断 `outputs/fc2-official-20261028/real-probe-diagnostic-r2.json` 显示 sync/async 响应均为 HTTP 200、官方 URL 精确，strict HTML parse 抛 `XMLSyntaxError`；恢复解析读到一个精确 sku/productID 与 og:url。仅将 HTML parser 设为 recover 模式并新增合成布局回归，JSON 解码和身份约束保持严格。

## Answer

FC2 官方来源已实现为保守 Product JSON-LD 适配器，并注册为 `fc2` / `FC2官方`；只映射身份精确匹配 Product 的标题、简介和图片候选，不推断演员或日期，也不把 seller 当演员。官方 metadata 的 HTTP 图片只对精确主机 `storage57000.contents.fc2.com` 改写为同路径 HTTPS；其它 HTTP host 继续拒绝，最终 URL 仍受既有 `image_hosts` 精确白名单约束。

2026-10-28 Root 完成本机候选回归、真实官方样本和 DSM 部署后验收：SDK regression-r5 1689 passed、8 warnings、7 subtests、exit 0；完整 SDK r3 的 44 个失败与 0.8.17 clean baseline 的失败节点集合一致、没有新增失败，不能称完整 suite 全绿。最终真实 sync/async 与安装后原生 API 均精确验证 FC2-PPV-1229107，搜索唯一命中，详情身份匹配，标题/简介非空，实际封面 HTTPS 返回 200 并由 Pillow 解码为 200×368 JPEG。自动状态、queue 与其它保护审计通过。汇总见 [`validation-summary.json`](../../../../outputs/fc2-official-20261028/validation-summary.json)；部署过程见[记录](../../../../records/fc2-official-source-2026-10-28.md)。

验收范围仅此 NAS 的一个 FC2 商品样本；未验证其它或旧商品、NFO/媒体写入、播放、preview/execute/transfer/scrape 或完整新下载自动链。源码提交和 main 集成结果以[最终部署记录](../../../../records/fc2-official-source-2026-10-28.md)及[`validation-summary.json`](../../../../outputs/fc2-official-20261028/validation-summary.json)中的 `source_commit` 为准。
