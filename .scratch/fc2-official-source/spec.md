# FC2 官方商品元数据源

Status: resolved

## 授权与验收

用户授权实现 FC2 官方元数据源，并在测试通过后部署与启用。本地实现与离线回归已完成；Root 随后完成了本票授权范围内的候选验证、DSM 部署、来源启用及部署后验收。

- 基线：`224923422b4b98fbccefa512916860f54add00e4`
- 分支：`scraping/feature/fc2-official-source`
- 目标：来源键 `fc2`，显示名 `FC2官方`，支持纯数字/`FC2-1229107`/`FC2-PPV-1229107` 查询，官方商品页详情按数字 ID 回查。
- 验收：同步与异步搜索/回查、同一 JSON-LD Product 的精确身份、歧义与访问门槛失败关闭、无演员/日期推断、图片候选仍受既有白名单、注册及顺序合同、离线网络阻断下测试通过。
- 版本：插件 `0.8.17` 升至 `0.8.18`；README 与必要发布清单同步。
- 边界：实现限于 `plugins.v3/xiaojiejiescraping`、相关 tests、README、发布记录及本票文件；不改 MoviePilot 核心、主前端、UI worktree 或媒体数据。部署仅修改插件来源列表与图片 host 配置；不执行媒体整理或自动下载。本票不自行提交 Git。
- 访问门：不执行年龄确认、登录、Cookie 或挑战交互。没有有效 Product 或精确身份的年龄/验证门页返回空；页面仅被动附带成人提示、同时公开返回有效且精确匹配的 Product 时，仍可读取该 Product。
- 响应体限制：2,000,000 字节是 SDK 完整读取响应后的解析入口上限，不是网络流式传输或缓冲内存硬上限。

## 已知证据

截至 2026-10-28，FC2 官方 Product JSON-LD 的 `image.url` 实测为 HTTP，主机为 `storage57000.contents.fc2.com`。仅对该精确主机、无端口/凭据/fragment 的 HTTP URL 改写 scheme 为 HTTPS，保留 path/query 原样，不请求 HTTP 图片；其他 HTTP 主机仍拒绝。改写后的 HTTPS URL 仍须通过 `image_hosts` 精确主机批准。Root 已对同一官方主机的样本和部署中实际候选图片完成 HTTPS 读取及图片解码验证；证据仅覆盖本票的 FC2-PPV-1229107，不代表其它图片或部署环境。当前适配不得引入登录、年龄确认或挑战绕过。

## 追加证据与解析决策

- 2026-10-28 只读真实候选诊断：`outputs/fc2-official-20261028/real-probe-diagnostic-r2.json` 记录 sync/async 均为 HTTP 200、官方响应 URL 精确、HTML MIME，页面各有一个唯一 Product，sku/productID 与目标号匹配，og:url 精确。lxml `recover=False` 对两份响应均抛 `XMLSyntaxError`；`recover=True` 可解析并读到同一商品身份。
- 因此 HTML document parser 使用 `recover=True` 兼容普通浏览器式 HTML 布局错误，同时保持 `no_network=True`、UTF-8 和现有大小限制；JSON-LD 仍用重复键拒绝与非有限数字拒绝，商品身份、官方 URL、唯一商品及响应门禁不变。恢复解析只用于 HTML 树容错，不放宽 JSON 或身份合同。
- 图片候选仅增加精确官方 storage 主机的 HTTP→HTTPS scheme 升级；同步与异步公共 provider 回归覆盖接受该候选及拒绝未知 HTTP 主机、显式端口、凭据和 fragment。最终 URL 仍由既有 `image_hosts` 精确批准。

## 最终验收（2026-10-28）

- SDK regression-r5：1689 passed、8 warnings、7 subtests、exit 0；该运行不包含 `tests/test_javrecognize_provider.py`。完整 SDK r3 有 44 failed、1729 passed；Root 用 clean 0.8.17 baseline-control-r1 做大小写敏感的失败节点集合比较，失败集合相同、无新增失败。故完整 suite 不记为全绿；既有 Manko 策略测试与 API-order-first-complete 语义冲突，本票未改该 provider 或旧测试。
- 最终候选 real-probe-r6 的 sync/async 均精确识别 1229107；标题/简介非空，图片 HTTPS 返回 200、JPEG、10,839 bytes、200×368。正式 installed-probe-r2 的原生 API 搜索恰一条 `source=fc2`、`media_id=1229107`，详情 ID 精确匹配，标题/overview 非空，封面同样成功解码。安装前后模块 hash 与自动状态一致；post-query protected audit 全部通过、queue 为 0。完整汇总见 [`validation-summary.json`](../../../outputs/fc2-official-20261028/validation-summary.json)。
- DSM `192.168.100.133` 的 `moviepilot-v2` 已部署并启用 0.8.18，sources 顺序为 `[fc2,manko,javbus]`，`image_hosts` 新增精确主机 `storage57000.contents.fc2.com` 并保留其它项。只变更来源列表与图片 host；Downloaders、identity、core 6、public 1167 和其它配置保持。自动 `enabled/armed/new_only` 均为 true、handoff `execute`、category `AV`、`started_at=1793068381`、queue 0。演员和日期缺失保持空值，不把 seller 映射为演员。
- 仅完成商品查询、详情与内存图片校验；未执行 preview、execute、transfer、scrape、媒体写入或新下载完整自动链验收。metadata cache 只新增该样本的 2 个键，移除的 4 条已验证过期，其它有效条目未变。
- 备份位于 `/volume3/NetBackup/moviepilot-pre-fc2-official-20261028-final`，220 files，manifest SHA-256 `2adf3f9556e4e19017f1915c58f5fa877e0da5bab234dc1cab25749d842fe5f5`；权限复核为目录 0700、文件 0600。部署与恢复过程和收据见[最终记录](../../../records/fc2-official-source-2026-10-28.md)。
- 源码提交和 main 集成结果以[最终部署记录](../../../records/fc2-official-source-2026-10-28.md)及[`validation-summary.json`](../../../outputs/fc2-official-20261028/validation-summary.json)中的 `source_commit` 为准。
