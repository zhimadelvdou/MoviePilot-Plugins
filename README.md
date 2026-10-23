# Xiaojiejie-Scraping

MoviePilot V3 直连多来源刮削插件。使用 MoviePilot 自带 WebUI 配置，通过已有 qBittorrent 连接筛选完成任务，识别后交给 MoviePilot 原生整理。**不复用 MetaTube，不修改 MoviePilot 核心，不自行搬运媒体。**

这是开发版本。0.8.3 在官方 MoviePilot V3 SDK 离线回归通过 `1544 tests` 和 `7 subtests`。真实现场累计核验两个 Manko 样本：ADN-765 的既有整理与刮削，以及 ADN-775 本轮原生无 ID 预览、单次硬链接整理和 NFO/图片刮削审计。两次核验均未改动源媒体；ADN-775 的目标与源为同一 inode。现场证据见[ADN-765 专题记录](../records/manko-metadata-artwork-fix-2026-10-22.md)和[ADN-775 翻译去重记录](../records/manko-translation-dedup-fix-2026-10-23.md)。其它来源和真实 qBittorrent 新下载自动整理仍未验收。默认禁用，自动模式默认只读发现。

## 功能与边界

- 直连元数据：Manko、FANZA、MGStage、DUGA、SOD、JavBus、Jav321、FC2Hub、FC2PPVDB/FC2CMADB
- 演员缩略图：可选 GFriends 官方公开索引，写入 NFO 的远程 thumb；插件不下载头像
- 原生手动入口：注册来源列表及同步/异步搜索、识别、图片、NFO 方法，供 MoviePilot 手动整理和刮削调用
- 插件独立手动页：从本地文件系统选源文件/目录和既有目标，指定已启用来源后预览并明确提交硬链接整理；自动识别目录只作快捷参考
- 自动入口：qBittorrent 已完成任务按 tag、path、tag_or_path 或 tag_and_path 选择；tag-only 使用 MoviePilot 映射任务路径，不从原始下载路径拼接候选
- 原生交接：先预览，完整核对后调用公开 `TransferChain.do_transfer`；仅支持复制和硬链接，保留做种源文件
- 持久状态：插件独立 SQLite 账本原子占位，区分预览、受理、完成、失败和不确定；未知结果不会盲目重提

除 Manko 两个现场样本的搜索、刮削与整理验证外，其他来源目前仅有**离线适配验证**。不执行 JavaScript、年龄确认、登录、Cookie 认证、CAPTCHA 或反爬挑战绕过。遇到访问门槛、布局变化、身份矛盾或搜索分页无法证明完整性时返回空；真实歧义不自动选择，Manko 严格等价多 ID 的处理见下文。

## 来源状态

| 来源 | 已实现合同与限制 |
| --- | --- |
| Manko | 原公开 API/base91 合同；详情须逐条证明精确番号并要求完整搜索总数；自动识别接受唯一 ID，或元数据严格等价的多个 ID（稳定选择最小 ID）；仅对 ADN 编号从 ATTACKERS 官方详情补缺失的导演、片商和时长 |
| FANZA | 现代 `/av/content/` Product JSON-LD；不从 CID 猜番号，不支持未经验证的旧目录 |
| MGStage | 原生产品 URL/品番详情；保留数字前缀；遇年龄门槛不设置确认 Cookie |
| DUGA | 原生 URL、mpn 与详情字段；未知布局失败关闭 |
| SOD | 产品详情表；不执行年龄/会话握手 |
| JavBus/Jav321 | 公开软件记录的详情身份与番号字段，严格核对来源原生 ID |
| FC2Hub | `id{number}` permalink 及经搜索确认的当前详情路由；不猜新版 slug |
| FC2PPVDB / FC2CMADB | 匿名普通 GET `/articles/{video_id}` 的初始 Inertia `Articles/Show` 合同；核对页面路径和 video_id，不把 article.id 当产品 ID；不请求有访问争议的延迟演员接口 |
| GFriends | 官方 `Filetree.json`：演员名精确匹配、按质量顺序选图；只取公开索引 |

FC2PPVDB 起初因迁站和现行响应合同不明而暂缓；随后找到[当前 MDCx 合同](https://github.com/z291173301/MDCx/blob/05ca8a8675cd40259823acc482309edee60501b4/mdcx/crawlers/fc2ppvdb.py#L168-L196)及[独立匿名合成 fixture](https://github.com/peach-mitao/peach/blob/489653d0edff03104c091a814679ca4b4ebdcc24/tests/test_metadata_fc2.py#L115-L140)，已实现保守适配。这并不证明线上匿名访问始终可用，也不意味着所有内容必须登录。带连字符的 `fc2ppv-db.com` 是另一服务，本插件没有静默切换到它。

## 插件独立手动整理

管理员可从插件侧栏打开“Manko 整理”页，从本地文件系统选择源文件或目录、任意独立的既有目标目录，并从已启用来源中选择元数据源。既有自动识别目录仅作为快捷参考，不限制手动选择。页面先展示每个文件的来源身份和原生目标预览；只有有效预览可提交。服务端保存媒体身份、源文件版本、选择范围、目标及 15 分钟有效期，客户端不能替换这些数据。

首版只允许硬链接、重命名、刮削和不覆盖既有目标；最多扫描 1000 个目录项、50 个视频。实际源/目标必须为独立的本地既有路径，不能选择文件系统根目录；符号链接不参与扫描，父目录可浏览。预览会先检查源和目标在同一卷，否则拒绝硬链接。提交状态可能为“宿主已受理”或“结果不确定”；状态页只有回读确认源文件版本未变、目标为同一 inode 的硬链接后才标记 `completed`，并注明 `completion_scope=file_transfer`。这只证明文件整理，不证明 NFO、图片或刮削已完成；请查看 MoviePilot 原生历史。未知结果不会自动重提。原文件始终保留。

## WebUI 设置

插件设置使用联邦 `Config` 页面，复用 MoviePilot 宿主的 Vuetify 组件和主题，并保留全部既有配置项。0.8.0 可直接编辑自动匹配方式、下载器、标签、识别目录及目标路径；保存完整配置时保留未编辑字段和自动发现开关状态。插件侧栏的“Manko 整理”和插件详情页使用联邦页面，提供文件选择、预览与提交。后台仍保留 `get_form()`、`get_page()` 合同，但启用 Vue 模式后，宿主不再渲染其 JSON 页面。

| 设置 | 默认 | 含义 |
| --- | --- | --- |
| enabled | false | 启用插件能力；初始化不联网 |
| sources | Manko | 多选元数据来源，选择顺序即自动查询顺序 |
| storage | local | 当前只支持本地挂载文件，暂不接受远程存储 |
| recognize_dirs | 空 | 每行一个 MoviePilot 容器内绝对输入目录，含子目录 |
| automatic_match_mode | 新配置默认 tag_or_path；旧配置缺省 tag_and_path | 自动匹配来源：tag、path、tag_or_path 或 tag_and_path。OR 模式至少需标签或路径之一；tag-only 从宿主映射的任务路径建立本次范围 |
| downloader | 空 | MoviePilot 已配置并启用的 qBittorrent 名称；其它下载器拒绝 |
| torrent_tag | 空 | 单个专用标签，按逗号分隔后的完整标签精确匹配；是否必填取决于匹配方式 |
| discovery_enabled | false | 每五分钟检查完成任务，行为由自动模式决定 |
| auto_new_only | 新配置默认 true，旧配置缺省 false | true 时只接受服务启动边界后完成的 qBittorrent 任务；同配置重载保留边界，禁用/重新启用或触发范围变化会重新设界 |
| handoff_mode | discover | discover 只发现；preview 查询并预览；execute 原生整理并刮削 |
| target_path | 空 | 既有本地目标目录，不允许与输入目录相互包含 |
| transfer_type | 空 | 必须明确选择 copy 复制或 link 硬链接；无默认搬运方式 |
| ownership_ack | false | 执行前确认相同来源已排除在宿主原生自动监控之外 |
| auxiliary_ack | false | 执行前确认理解原生 TMDB 辅助补充风险并核对预览 |
| handoff_limit | 1 | 每轮最多准备/处理的新文件数，范围 1–5 |
| image_hosts | 空 | 批准的图片 HTTPS 精确主机，每行一个；空值不提供图像地址 |
| actor_images | false | GFriends 演员缩略图，还须批准 raw.githubusercontent.com 主机 |

### Docker 路径

**输入和目标都填写 MoviePilot 容器看到的路径。** 插件使用宿主下载器返回的、已完成路径映射的 `content_path/path`。不使用原始 qBittorrent `save_path` 重拼，不再进行第二次 Docker 路径映射。

预览/执行还通过公开 `torrent_files` 清单核对 hash 的文件所有权：只将既有范围内路径与清单相对名相交，锚点使用宿主已映射的 save_path，不猜路径或重复映射。拒绝绝对名/穿越/重复冲突/未完成文件，忽略 priority=0 的未选择项；共享目录里不属于该种子的额外视频不会入队。入队和提交前都重新核对。

目录判断要求路径和文件实际存在，拒绝相对路径、根目录 `/`、`..`、目录前缀碰撞和符号链接逃逸。词法路径与真实目标都要在允许范围内；提交前再次核对真实路径、设备/inode、大小和修改时间。

### 自动模式

1. 只读发现：不查询元数据源，不提交整理，不修改种子标签
2. 预览：会查询所选元数据源、可选 GFriends 索引及宿主原生辅助来源，并可能写缓存/临时规划状态；不搬运媒体。目标目录和整理方式必须填好
3. 执行：还必须开启两项确认。插件先调用原生预览，要求成功且仅对应当前源文件、目标落在明确目录内，再保存提交意图并同步交给 MoviePilot。不会使用 force，不清理旧历史，不附带整理其它文件

不得让同一来源同时由本插件和宿主原生下载器/目录监控整理。插件不会自动修改监控设置，必须由用户配置和确认。

新发现使用宿主 `TRANSFER` 完成过滤。宿主可能在首个文件结束时为整条种子加“已整理”；因此每条种子的全部 eligible 文件会先在一个事务里入插件账本，后续既有队列不把该标签误当成整个插件批次完成。

`auto_new_only=true` 使用独立 `auto-trigger.sqlite3` 保存启用边界，不与文件交接记录混写。只接受公开原始 qBittorrent 行中的有限 `completion_on`，且要求不早于启动边界、不晚于当前服务端时间；缺少字段、旧任务后来才加标签和未来时间均拒绝。相同范围的服务重载保留边界；插件禁用/再启用、改存储类型或更改下载器、标签、根目录、目标、来源、整理方式会重新设界。旧配置缺少该项时为 false，维持原轮询行为。

管理员 API `GET /automatic/status` 只读当前配置/边界/轮询摘要；`POST /automatic/check` 只读检查公开下载器完成行能力、时间字段覆盖和宿主目录监控重叠，不启用轮询、不入队或调用整理。检查输出仅含安全汇总，不返回下载器配置或路径明细。

继续排队文件时，插件通过公开 SDK 服务目录取得 qBittorrent 实例，能力检查后只读调用 `get_completed_torrents(ids=[hash], tags=None)`；还要求已知安全原始状态、有限数值 progress≥1、amount_left=0 和当前专用标签。映射路径仍从宿主链式查询取得。**不只信任普通 hash 查询归一后的 completed 状态**。能力缺失、未知原始状态、用户移除标签或路径变化时停止交接；不访问私有 qbc、不读取凭据、不删除或补写标签。这个实例能力已在 v3.1.1 验证，未来版本缺失时失败关闭。

每轮最多查看 100 个种子、入队 500 个视频文件，使用持久公平游标；不会为了凑数量拆开一条种子的准入。单条种子超过 500 个候选视频时保守拒绝自动处理。默认每轮仅处理一个新文件。

**原生副作用仍由 MoviePilot 管理**：实际执行可能产生历史记录、元数据文件、通知和原生“已整理”标签。插件本身不额外改标签，也不把宿主的整种子标记当成每个文件都完成的证据。

## 什么才算完成

公开 `do_transfer` 的 True 可能只是受理或历史跳过，不能直接算完成。

插件用公共只读历史查询核对：来源+原生 ID、源路径/存储、文件大小/mtime、下载器/hash、整理方式、精确预览目标、新历史 ID，并确认目标文件存在且大小吻合。已有同版本、同意图的成功历史可核实后跳过重复操作。证据不足保留 accepted/uncertain；后续只读核实，不再次提交。这里的 completed 仅表示文件整理已核实，宿主后续异步 NFO/图片刮削是独立步骤，不冒称已经逐项验证完成。

执行所有权按物理源文件版本建立，独立于目标、来源顺序、qbit hash 或路径别名等可变配置。更改设置不能绕过 submitting/accepted/uncertain 记录重新整理同一版本。需要重整请先在 MoviePilot 历史/队列核实并使用其明确手动流程。

账本不自动过期、淘汰或重置；损坏/不兼容时失败关闭。容量满仅拒绝新准入，既有队列和只读核实继续。进程在 preparing 阶段退出会保留准备状态等待复核；在实际调用前后退出或响应不明时，保留未知状态，绝不猜测没执行。不要删除账本来“重试”未知操作。

## 原生手动整理与刮削

启用后，宿主来源列表、来源搜索、手动整理及手动刮削后台合同都能调用本插件同步/异步方法。只有明确选择本插件已启用来源才查询；来源为空或为普通内置来源时退出，不捕获全局影片。

来源查询也服务于既有身份回查，不能仅凭方法签名声称识别出了“手动调用”。这些元数据查询没有文件路径，因此不套用 qBittorrent 自动触发目录规则；实际手动文件操作仍由 MoviePilot 手动入口和用户选择决定。独立页面另有源目录和目标目录范围校验，部署和实屏验收由环境记录维护。

Manko 的原生同步/异步手动搜索与自动识别会合并完整元数据及演员集合相同、仅标题或简介译文互补的同番号记录，并选用最小原生 ID。出现同语译文冲突、重复语言项、无效译文结构、组内无共同语言锚点、其它未映射字段差异、元数据不完整或演员集合不同，仍保留多个手动候选并拒绝自动选择。已有平面标题可用时，缺少或空的标题译文数组继续兼容。当前演员接口按作品 ID 返回集合，已观察到 `size/page` 参数不改变该集合；此检查比较各作品当前返回值，不证明上游演员目录完整。人工选定原生 ID 后的回查仍保持来源和身份校验。

V3.1.8 原生文件管理整理窗口选择“电影”后会显示媒体 ID 控件；空 ID 的原生窗口仍依赖既有 MoviePilot 热修才能按来源自动识别，手动填入已知 Manko ID 可按原生流程预览。插件侧栏的独立“Manko 整理”页直接从文件名/允许的父目录提取番号、按明确选择的来源识别并固定身份，不依赖该原生窗口热修。两条入口分开验收；独立页预览成功也不代表硬链接或 NFO/图片已写盘。工作流验收规格见 [Manko 原生识别与整理规格](.scratch/manko-native-workflow/spec.md)。

Manko 的 `cover_image` 映射为 poster。`backdrop_images` 按来源顺序最多检查 8 个通过 `image_hosts` 精确 HTTPS 主机校验、且不与封面重复的候选；插件匿名读取最多 256 KiB 图片头，选取首个宽高至少 320×180、横向且边长不超过 8192、像素数不超过 4000 万的背景图。没有合格背景图时只返回 poster，不会把封面重复用作背景图。该设置默认空，空值不会提供图像地址。URL 通过插件校验只表示允许交给宿主，不保证 MoviePilot 下载或落盘成功。有效正整数 `video_duration` 映射为分钟。对于精确 `ADN-数字` 番号，插件还会请求固定的 ATTACKERS 官方详情页，仅在品番字段完整匹配时补入缺失导演、ATTACKERS 片商标记和官方分钟时长；页面不可用或结构不匹配不会影响 Manko 身份识别。官方时长只补缺失字段，不覆盖 Manko 数据，也不改写原文件已有 NFO；`tags`、`imdb_score` 和 `rating` 尚未映射。

## 宿主 TMDB 辅助补充限制

官方 v3.1.1 原生整理仍会明确调用 `supplement_tmdb_info()`。对应能力是列表聚合且 `plugin_short_circuit=False`，不能靠本插件空返回或取消 SEARCH_SOURCE 阻止。

主来源与标题保留，但可能追加不匹配的 TMDB/IMDb/TVDB/合集 ID、类型 ID 和别名。宿主分类会读 `genre_ids`。插件的 NFO 从独立验证缓存生成，不输出后来追加的辅助身份；这不等于所有宿主功能都被隔离。自动交接明确关闭类型/类别子目录选项，但宿主仍可能使用辅助信息。

先用预览核对命名与目标，再决定是否开启执行。插件不伪造 TMDB ID、不伪装音乐类型、不 monkey patch、不修改宿主。

## 网络、缓存与安全输出

所有直连请求每次读取 `app.sdk.config.settings.PROXY`，没有独立代理。TLS 验证开启；匿名来源禁用环境隐式认证/netrc和站点Cookie，代理仍显式继承MoviePilot设置；SDK 自动重定向关闭。次源只手动处理最多 4 次、相同 HTTPS origin（含端口）的已校验详情路由跳转，拒绝循环、凭据、登录/挑战/年龄确认和跨来源跳转。

只发送严格校验的番号或来源 ID，不发送完整文件路径。来源+ID 独立缓存；不选搜索首项、不猜 CID 转番号、不补零。支持保守番号形式、数字厂牌前缀及 FC2 编号。JSON 元数据缓存限量、过期、原子写入，不读取旧 pickle；同宿主进程实例共享路径锁并重读合并，未提供多个 OS 进程共享 JSON 目录的事务保证。自动交接 SQLite 使用独立事务账本，不修改宿主数据库。

XML 使用库转义并去除非法字符。图片 URL 限批准 HTTPS 主机，拒绝凭据、IP、本地主机及控制/空白字符；固定 poster.jpg/fanart.jpg 文件名。GFriends 配置或主机授权撤销后，缓存 NFO 也停止输出相应缩略图。图像保存交由宿主；宿主自身图片下载器的 DNS/重定向策略不由插件保证。

## 查询诊断（0.4）

在 MoviePilot 的 INFO 日志中搜索 `Xiaojiejie-Scraping diagnostic`。保存设置时记录启用状态和已选来源；查询时记录来源、阶段、缓存命中及接受/拒绝原因。Manko 另外记录以下细分原因，同步和异步入口均适用：

- `disabled` / `source_unselected`：插件未启用，或调用来源不在插件当前所选列表
- `number_not_unique`：输入未提取到唯一可接受番号；不代表网站未收录
- `http_response` / `http_redirect` / `http_error`：已得到 SDK 响应；只有响应提供有效整数时才带 `http_status`。例如 403 表示请求被拒绝，不能据此判断作品是否收录
- `transport_unavailable` / `request_exception`：SDK 未返回响应或调用抛出异常；不猜测是代理、DNS、TLS、超时还是其它问题，不编造 HTTP 状态
- `invalid_json` / `payload_invalid`：响应不能解析为 JSON，或 JSON/base91 载荷不符合既有合同
- `schema_invalid` / `search_incomplete`：字段形状不兼容，或总数/分页证据不足，无法证明结果完整
- `no_candidates`：格式和总数通过校验的搜索返回零候选；`no_exact_match`：取得候选详情后没有精确匹配；`ambiguous_match`：多个不同原生 ID 精确命中
- `identity_mismatch` / `number_evidence_invalid`：详情身份或番号证据不一致；`actors_invalid` 只表示可选演员列表无效
- `cache_hit` / `cache_miss`、`accepted` / `empty_result` / `query_exception`：插件查询整体结果；其它来源目前只提供这一层诊断，不据空结果猜测其 HTTP 状态或具体解析原因

示例：`Xiaojiejie-Scraping diagnostic source=manko operation=search reason=http_error http_status=403`。每行仅包含固定词表和有限整数，不包含番号、原生 ID、搜索词、完整 URL/参数、代理地址、请求头、Cookie、响应正文、异常文本、标题或文件路径。SDK 未提供状态时省略该字段；诊断不增加请求或自动重试、不改变匹配与访问边界，也不绕过访问门槛。并发查询应结合日志时间和阶段阅读，不能把其它查询的相邻行当成同一个请求的证据。

## 测试、版本与许可

0.8.3 在完整等价检查中合并互补的 Manko 标题与简介译文，保留最小原生 ID；同语言冲突、重复语言项、无效译文结构、无共同语言锚点或其它字段不一致时仍拒绝自动合并。只含平面标题的旧记录仍可通过插件识别和手动搜索。

0.8.3 使用官方 MoviePilot V3 SDK 的离线合成测试通过 `1544 tests` 和 `7 subtests`。2026-10-23 现场核验中，ADN-775 与 ADN-765 的 Manko 搜索各返回一个规范 ID；ADN-775 在未手填 ID 的原生手动预览中自动识别，且只执行一次。宿主状态确认文件整理完成，源与目标为同一 inode、源 SHA-256 不变；新增 NFO 的身份及标题、年份、简介、演员、导演、片商、时长字段齐全。poster (800×538) 与横向 fanart/backdrop (800×534) 均已生成；背景图与 poster 哈希不同，fanart 与 backdrop 哈希一致。此现场结果覆盖这两个查询样本及 ADN-775 单次整理，不代表真实 qB 新下载自动流程已验收。

0.8.2 增加匿名、有界的背景图图片头探测，跳过竖版缩略图并选用首个合格横图；宿主配置重载会清空探测 memo，探测期间若来源配置撤销则不返回过期图片地址。0.8.1 改进 Manko 的 poster/fanart 分离、有效时长及 NFO 导演/片商字段；ADN 编号使用受限的 ATTACKERS 官方详情补充缺失信息。元数据缓存升级为 `metadata-v2.json`，旧缓存保留但不再读取。官方详情解析以合成 HTML 测试，不访问真人站点。

原有离线测试基线使用官方 MoviePilot v3.1.1（commit `6ff7dd86a14c403b0e408f778aa3174e13e15ef1`）。0.5 全量回归使用热修前官方基线 `05967cdc0f10c93b55a7a01acd34c57d5f15df67`、真实 V3 SDK 和 Python 3.14.7，`1427 passed in 37.90s`；没有叠加核心补丁、联网或挂载生产媒体/配置。原生执行集成用真实模块 dispatcher、本地存储、SQLite、事件处理器及刮削链；只有远程元数据 provider 使用合成数据。NFO 的标题、年份、简介、演员、来源原生 ID 和番号已核验；图片下载未覆盖。联邦页面另有 12 项 Vue 组件测试通过，最终构建通过。

0.6 自动触发使用相同的热修前真实 V3 SDK，完整离线回归 `1456 passed in 38.46s`，另有 15 项 Vue 组件测试及最终构建通过。该回归禁止真实 DNS/socket，不连接生产下载器或真实媒体站点、不挂载生产媒体/配置；这些测试不代表真实新下载已经完成自动整理。

0.8.0 的断网真实 SDK 后端全量回归为 `1491 passed in 42.54s`；真实 Vuetify 组件测试为 `30/30`，独立前端构建通过。测试使用合成数据并阻断真实网络；原生硬链接、NFO 和图片落盘仅在隔离合成文件上验证。它们不证明生产媒体处理或真实新下载的自动整理已验收。

0.8.0 增加自动匹配方式选择和手动自选本地源/目标；旧自动配置保留 tag_and_path 语义。tag-only 以 MoviePilot 映射的任务路径限制候选，并在发现/交接边界检查原生监控重叠。模块 `plugins.v3/xiaojiejiescraping`，类 `XiaojiejieScraping`，索引 `package.v3.json`。页面开关读取宿主插件配置 API 的 `{success,message,data}` 响应，要求成功且 `data` 为包含布尔型 `enabled`、`discovery_enabled` 的完整对象后，才只更新 `discovery_enabled` 并 PUT；状态 API 回读确认切换结果。旧版直接返回原始配置对象时，仅在上述必需字段有效时兼容。页面会按 `auto_new_only` 显示启用边界。此前版本 `0.2` 在版本比较中低于历史开发版 `2.0.0`，既有安装不一定出现自动升级提示。宿主支持的 PEP 440 范围为 `>=3.1.1,<4`，不硬锁持续更新的 V3；未来 V3 兼容性尚未验证。

### 部署后核验

部署时保留现有仓库绑定、配置和插件数据。2026-10-23 的 0.8.3 部署核对了插件版本及仓库绑定，core6/public1167、配置和 automatic mode 均保持不变；manual ledger 更新且完整性检查通过，handoff/auto ledger 未变。ADN-775 的原生无 ID 预览、单次执行、宿主状态回读和五个目标文件审计均通过；源哈希、大小、mtime 保持不变，源与目标 inode 相同。ADN-765 本轮仅重新核验了搜索计数及规范 ID；此前整理和刮削验收见上述专题记录。真实 qB 新下载自动流程仍未验收，具体主机和安装状态由环境记录维护。原生旧整理窗口空 ID 自动识别仍依赖对应 MoviePilot 热修。多 ID 的稳定选择只适用于完整元数据与演员集合相同且标题/简介译文无冲突的候选组；真实差异仍保留歧义。

历史说明：已核对的 MoviePilot v3.1.1 原生在线仓库 install API 合同，仅适用于已确认绑定在线仓库的安装。若当前来源身份为 `local_only`，不得据该历史合同切换到在线仓库。

```sh
export MOVIEPILOT_BACKEND=/absolute/path/to/MoviePilot
cd /absolute/path/to/MoviePilot-Plugins
"$MOVIEPILOT_BACKEND/.venv/bin/python" -m pytest tests -q
```

测试使用官方引导、临时 CONFIG_DIR、真实 SDK/模型/SQLite和合成 HTTP/执行回放，收集及执行阶段禁止真实 DNS/socket。未访问真实媒体站点、图片、付费模型、实际下载器或 NAS，未启动生产应用生命周期。没有 GitHub Actions、凭据、宿主改动。

GPL-3.0，完整文本见 [LICENSE](LICENSE)，来源说明见 [NOTICE](NOTICE)。沿用 [hyuan280/JAVRecognize 1.1](https://github.com/hyuan280/MoviePilot-Plugins/tree/main/plugins.v2/javrecognize) 的独立来源/原生整理思路；Manko 编码合同保留原作者署名，其余适配器依据公开协议事实独立编写，未复制 MetaTube 实现。

## 工程技能配置

工程技能配置见 [AGENTS.md](AGENTS.md) 及 [docs/agents/](docs/agents/)；工程 Issue 使用 `.scratch/` 中的本地 Markdown.
