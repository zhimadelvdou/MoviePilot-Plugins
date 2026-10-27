# Xiaojiejie-Scraping

MoviePilot V3 直连多来源刮削插件。使用 MoviePilot 自带 WebUI 配置，通过已有 qBittorrent 连接筛选完成任务，识别后交给 MoviePilot 原生整理。**不复用 MetaTube，不修改 MoviePilot 核心，不自行搬运媒体。**

这是开发版本。0.8.15 集成手动整理界面调整：目录浏览默认按修改时间从旧到新排序；有效预览后可直接点击“确认并整理”，无需额外勾选。预览过期或选择变更后仍须重新预览，异常/未知执行结果仍防止重复提交。0.8.14 的 qBittorrent 分类筛选、JavBus 配置 API 与女优证据，以及完整公开文件清单（仅当所有文件 ID 缺失时使用内部位置校验，混合 ID 仍拒绝）兼容保持。

0.8.13 延续 0.8.12 的女主演证据规则，并在 JavBus 详情数据中按顺序提取最多 8 个去重 HTTPS `samples[].src` 图片候选。手动搜索/详情展示时，JavBus 封面和背景字段可使用限时签名的同源图片路由；路由只允许当前 `image_hosts` 已批准的 JavBus 封面/样图及 DMM 视频样图 URL，固定发送 JavBus 根页 Referer，不接收账号 Cookie，并限制响应大小、图片 MIME 和解码尺寸。签名 URL 签发有效期为 1 小时，插件配置重载即失效。该路由只服务搜索和详情展示；`metadata_img` 仍按既有合同返回原始上游 HTTPS URL，由 MoviePilot 原生下载，宿主下载是否成功仍取决于宿主访问条件，未在此版本承诺。完整演员 cast、女优来源证据、NFO 与自动整理边界不变。JavBus JSON API 继续使用单独配置的基址，默认留空；须显式选择来源才参与识别。API 与图片代理验证不代表完整刮削、整理或部署验收；自动整理保持默认关闭、默认只读发现。

## 功能与边界

- 直连元数据：Manko、FANZA、MGStage、DUGA、SOD、JavBus、Jav321、FC2Hub、FC2PPVDB/FC2CMADB
- 演员缩略图：可选 GFriends 官方公开索引，写入 NFO 的远程 thumb；插件不下载头像
- 原生手动入口：注册来源列表及同步/异步搜索、识别、图片、NFO 方法，供 MoviePilot 手动整理和刮削调用
- 插件独立手动页：从本地文件系统选源文件/目录和既有目标，指定已启用来源后预览并明确提交硬链接整理；可选按女主演分目录，自动识别目录只作快捷参考
- 自动入口：qBittorrent 已完成任务按分类、tag、path、tag_or_path 或 tag_and_path 选择；分类为独立精确匹配，既有 tag/path 规则不变。候选仍要求映射任务路径、完整公开文件清单、有效完成状态和新任务边界
- 原生交接：先预览，完整核对后调用公开 `TransferChain.do_transfer`；仅支持复制和硬链接，保留做种源文件
- 持久状态：插件独立 SQLite 账本原子占位，区分预览、受理、完成、失败和不确定；未知结果不会盲目重提

除 Manko 两个现场样本的搜索、刮削与整理验证外，其他来源目前仅有**离线适配验证**。不执行 JavaScript、年龄确认、登录、Cookie 认证、CAPTCHA 或反爬挑战绕过。遇到访问门槛、布局变化、身份矛盾或搜索分页无法证明完整性时返回空；Manko 同番号多 ID 时只接受首个完整数据包，不拼接不同 ID 的字段。

## 来源状态

| 来源 | 已实现合同与限制 |
| --- | --- |
| Manko | 原公开 API/base91 合同；详情须逐条证明精确番号并要求完整搜索总数；同番号多 ID 按 API 顺序选首个完整数据包，否则拒绝；仅对 ADN 编号从 ATTACKERS 官方详情补缺失的导演、片商和时长 |
| FANZA | 现代 `/av/content/` Product JSON-LD；不从 CID 猜番号，不支持未经验证的旧目录 |
| MGStage | 原生产品 URL/品番详情；保留数字前缀；遇年龄门槛不设置确认 Cookie |
| DUGA | 原生 URL、mpn 与详情字段；未知布局失败关闭 |
| SOD | 产品详情表；不执行年龄/会话握手 |
| JavBus/Jav321 | JavBus 使用用户配置的 API JSON 基址直连，需严格核对详情番号；不使用 Cookie/账号、不抓 JavBus 页面 HTML。JavBus `stars` 按来源女优目录语义生成有序候选，过滤不安全姓名/ID并将身份冲突降为无可信候选；不逐人请求 profile、不推断独立性别认证。API 具体状态需在部署环境验证；未实站验收 |
| FC2Hub | `id{number}` permalink 及经搜索确认的当前详情路由；不猜新版 slug |
| FC2PPVDB / FC2CMADB | 匿名普通 GET `/articles/{video_id}` 的初始 Inertia `Articles/Show` 合同；核对页面路径和 video_id，不把 article.id 当产品 ID；不请求有访问争议的延迟演员接口 |
| GFriends | 官方 `Filetree.json`：演员名精确匹配、按质量顺序选图；只取公开索引 |

FC2PPVDB 起初因迁站和现行响应合同不明而暂缓；随后找到[当前 MDCx 合同](https://github.com/z291173301/MDCx/blob/05ca8a8675cd40259823acc482309edee60501b4/mdcx/crawlers/fc2ppvdb.py#L168-L196)及[独立匿名合成 fixture](https://github.com/peach-mitao/peach/blob/489653d0edff03104c091a814679ca4b4ebdcc24/tests/test_metadata_fc2.py#L115-L140)，已实现保守适配。这并不证明线上匿名访问始终可用，也不意味着所有内容必须登录。带连字符的 `fc2ppv-db.com` 是另一服务，本插件没有静默切换到它。

## 插件独立手动整理

管理员可从插件侧栏打开“Manko 整理”页，从本地文件系统选择源文件或目录、任意独立的既有目标目录，并从已启用来源中选择元数据源。路径浏览器默认按修改时间从旧到新排列；此排序设置共用于手动来源、目标及自动目录浏览，仍可切换为名称或文件大小排序。既有自动识别目录仅作为快捷参考，不限制手动选择。页面先展示每个文件的来源身份和原生目标预览；有效预览后可直接点击“确认并整理”，无需额外勾选。服务端保存媒体身份、源文件版本、选择范围、目标及 15 分钟有效期，客户端不能替换这些数据。启用“按女主演分目录”后，Manko 只使用本次原始 packet 中 `avdbs_actor is True` 且与当前演员精确匹配的安全姓名；JavBus 使用 API `stars` 中符合安全规则的女优目录候选。两者默认按来源顺序选择第一位，手动页面可改选可信候选并重新预览；没有匹配本片的来源证据时停止，不允许从普通 cast 选择。自动整理使用同一证据，执行前若默认首位变化或所选演员不再可信则停止。所有演员仍写入 NFO。

首版只允许硬链接、重命名、刮削和不覆盖既有目标；最多扫描 1000 个目录项、50 个视频。实际源/目标必须为独立的本地既有路径，不能选择文件系统根目录；符号链接不参与扫描，父目录可浏览。预览会先检查源和目标在同一卷，否则拒绝硬链接。提交状态可能为“宿主已受理”或“结果不确定”；状态页只有回读确认源文件版本未变、目标为同一 inode 的硬链接后才标记 `completed`，并注明 `completion_scope=file_transfer`。这只证明文件整理，不证明 NFO、图片或刮削已完成；请查看 MoviePilot 原生历史。未知结果不会自动重提。原文件始终保留。

## WebUI 设置

插件设置使用联邦 `Config` 页面，复用 MoviePilot 宿主的 Vuetify 组件和主题，并保留全部既有配置项。0.8.0 可直接编辑自动匹配方式、下载器、标签、识别目录及目标路径；保存完整配置时保留未编辑字段和自动发现开关状态。插件侧栏的“Manko 整理”和插件详情页使用联邦页面，提供文件选择、预览与提交。后台仍保留 `get_form()`、`get_page()` 合同，但启用 Vue 模式后，宿主不再渲染其 JSON 页面。

| 设置 | 默认 | 含义 |
| --- | --- | --- |
| enabled | false | 启用插件能力；初始化不联网 |
| sources | Manko | 多选元数据来源，选择顺序即自动查询顺序；JavBus 需配置 API 基址并显式保存启用 |
| javbus_api_url | 空 | JavBus API 基址，例如 `http://NAS地址:8922`；留空时 JavBus 查询不可用，不含路径查询参数或认证信息 |
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
| actress_subfolder | false | 在媒体库下按女主演姓名增加一级目录；Manko 使用当次 `avdbs_actor is True`，JavBus 使用安全且无身份冲突的来源女优目录列表；均默认选首位、手动可改选，无证据时停止 |
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

Manko 的同步/异步搜索与手动候选接口遵循相同规则：先证明完整搜索总数和精确番号，再按 API 顺序逐个检查精确命中的原生 ID，返回首个包含全部所需刮削数据的原始记录与该 ID 演员列表。标题可使用有效的完整多语言数组，或在数组缺失/为空时使用已有平面标题；简介数组的每行都须有有效语言和简介，日期须符合既有 ISO 日期规则，封面与至少一个不同的背景图须为安全 HTTPS 地址，演员列表须非空且有效。其它未知字段、未映射资源字段和 rating 等差异不改变完整度，也不用于择优；不同 ID 的数据不合并，演员标记与所选原始记录保持一致。所有候选均不完整时拒绝，并在诊断日志以 `title`、`descriptions`、`date`、`cover`、`background`、`actors` 标记缺项。唯一候选及按原生 ID 回查保持原行为；手动选定 ID 后的回查仍保持来源和身份校验。Manko 番号搜索使用 `complete-first-v1` 策略缓存键；旧号码键不再用于读取，本版不主动清理旧键。

V3.1.8 原生文件管理整理窗口选择“电影”后会显示媒体 ID 控件；空 ID 的原生窗口仍依赖既有 MoviePilot 热修才能按来源自动识别，手动填入已知 Manko ID 可按原生流程预览。插件侧栏的独立“Manko 整理”页直接从文件名/允许的父目录提取番号、按明确选择的来源识别并固定身份，不依赖该原生窗口热修。两条入口分开验收；独立页预览成功也不代表硬链接或 NFO/图片已写盘。工作流验收规格见 [Manko 原生识别与整理规格](.scratch/manko-native-workflow/spec.md)。

Manko 的 `cover_image` 映射为 poster。`backdrop_images` 按来源顺序最多检查 8 个通过 `image_hosts` 精确 HTTPS 主机校验、且不与封面重复的候选；插件匿名读取最多 256 KiB 图片头，选取首个宽高至少 320×180、横向且边长不超过 8192、像素数不超过 4000 万的背景图。没有合格背景图时只返回 poster，不会把封面重复用作背景图。该设置默认空，空值不会提供图像地址。JavBus 封面也必须由用户将实际封面 URL 的精确主机加入 `image_hosts` 才会交给宿主；空值时无海报。URL 通过插件校验只表示允许交给宿主，不保证 MoviePilot 下载或落盘成功。有效正整数 `video_duration` 映射为分钟。对于精确 `ADN-数字` 番号，插件还会请求固定的 ATTACKERS 官方详情页，仅在品番字段完整匹配时补入缺失导演、ATTACKERS 片商标记和官方分钟时长；页面不可用或结构不匹配不会影响 Manko 身份识别。官方时长只补缺失字段，不覆盖 Manko 数据，也不改写原文件已有 NFO；`tags`、`imdb_score` 和 `rating` 尚未映射。

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
- `no_candidates`：格式和总数通过校验的搜索返回零候选；`no_exact_match`：取得候选详情后没有精确匹配；`ambiguous_match`：保留给其它仍按唯一 ID 规则处理的查询；`metadata_incomplete`：Manko 多 ID 查询找不到完整 packet，`missing` 随后列出固定字段标签
- `identity_mismatch` / `number_evidence_invalid`：详情身份或番号证据不一致；`actors_invalid` 只表示可选演员列表无效
- `cache_hit` / `cache_miss`、`accepted` / `empty_result` / `query_exception`：插件查询整体结果；其它来源目前只提供这一层诊断，不据空结果猜测其 HTTP 状态或具体解析原因

示例：`Xiaojiejie-Scraping diagnostic source=manko operation=search reason=http_error http_status=403`。每行仅包含固定词表和有限整数，不包含番号、原生 ID、搜索词、完整 URL/参数、代理地址、请求头、Cookie、响应正文、异常文本、标题或文件路径。SDK 未提供状态时省略该字段；诊断不增加请求或自动重试、不改变匹配与访问边界，也不绕过访问门槛。并发查询应结合日志时间和阶段阅读，不能把其它查询的相邻行当成同一个请求的证据。

## 测试、版本与许可

0.8.14 新增独立 `category` 自动匹配模式及 `torrent_category` 配置。qBittorrent 列表投影分类和 fresh raw completion row 均须精确匹配配置值；分类模式将 `tag=None` 传给文件候选筛选，并用已映射任务路径建立安全 task root。tag-only 仍使用 MoviePilot 映射任务路径，不从 raw `save_path` 拼接候选。完整公开文件清单继续用于 ownership 验证；当 SDK 投影中的所有文件 ID 都缺失或为 `None` 时，仅以清单位置作内部连续性检查，名称、大小、路径和完成状态校验不变；缺失 ID 与显式 ID 混合时仍拒绝。该内部序号不代表下载器提供的真实文件 ID。同文件系统约束、原生监控重叠、source identity/女优校验及 `auto_new_only` 仍由既有路径负责。每次筛选、入队和执行回调都会重新读取 raw row 并复核分类。旧配置和原四种匹配语义不自动转换；只有 category 模式把 `torrent_category` 纳入 scope signature，切换到分类模式或修改分类值会重新建立新任务边界。边界重置前已完成的任务须单独有界处理，不应为重试而关闭 `auto_new_only`。本地实现不代表已部署或真实整理链验收。

0.8.13 为 JavBus 的搜索/详情 `MediaInfo` 图片字段增加签名代理：`samples[].src` 按来源顺序筛选为最多 8 个不同 HTTPS 候选，封面相同项不作为背景；缺失或损坏样图不影响其它元数据或女优证据。图片路由要求当前启用 JavBus、当前 allowlist 保留精确图片主机、签名未过期且与配置代际一致；只接受受限路径，使用固定根 Referer、禁止重定向、8 秒请求超时和 8 MiB 上限，并校验 MIME、真实解码格式和尺寸。搜索及详情展示字段使用同源限时 URL，签名不含 MoviePilot API token。缓存使用独立 `artwork-v1` JavBus 命名空间，旧缓存保留但不作为新版本样图命中。`metadata_img` 和 NFO 仍使用原始投影及现有 `image_hosts` 规则，因此本次代理不保证 MoviePilot 原生图片下载，也不构成完整刮削或整理验收。

0.8.12 按来源证据选择女主演：JavBus API 的 `stars` 列表按来源顺序生成目录候选，仅安全有效 star ID 与安全姓名参与；单个不安全项不影响元数据，ID/姓名冲突会清空候选证据但保留完整演员数据。插件将经校验的证据绑定到本轮生成的 `MediaInfo` 对象、来源、媒体 ID、番号及配置代际；缓存识别重新生成对象级绑定，缺失、错配或过时代际均失败关闭。Manko 的原始 `avdbs_actor is True` 路径不变；未给 JavBus 演员添加该标记。此处依据 JavBus 来源级女优目录列表语义，不代表逐人页面实时核验或独立性别认证。

0.8.11 将 JavBus 查询改为调用 `GET {javbus_api_url}/api/movies/{number}`，配置值只保存在用户插件设置中，提交默认留空。请求不经系统代理、不自动跟随重定向；JSON 大小、媒体类型、请求身份、标题、日期及演员结构均有限校验，失败仅写固定诊断，不记录响应内容或完整 URL。可选 director、producer、publisher 映射至导演/制片商字段，演员顺序保留但不产生可信女优标记；封面 URL 仍受 `image_hosts` 精确主机白名单约束。API 取数与 MoviePilot 完整整理链的验收分开记录，部署状态以环境验证记录为准；API 端点可达不代表宿主完整刮削链已验收。

0.8.10 历史实现为 JavBus 匿名 HTML 来源，默认来源保持 Manko；该 HTML 适配现已被 0.8.11 的用户配置 API 适配取代。JavBus 需要配置 API 基址并显式保存到现有 `sources` 后才参与手动和自动识别。演员不产生可信女优证据；封面须将实际图片 URL 的精确主机加入 `image_hosts`，默认空值无海报。FANZA 仍搁置。

0.8.9 多位可信女主演时，自动整理与手动预览默认按原始来源顺序选择第一位。手动页面允许改选可信候选，变更选择会使旧预览和确认失效，必须重新预览；执行前再次核验默认候选仍为首位，手动候选仍有本次原始 `avdbs_actor is True` 标记且匹配当前 cast。无可信证据、非法选择或证据变化均失败关闭；NFO 继续写入完整演员 cast。官方 MoviePilot V3 SDK 全组为 1552 passed、44 failed（7 subtests）；44 个失败均位于未修改的 `test_javrecognize_provider.py`，与 0.8.8 基线失败集合完全一致，且该 provider 与其测试文件未变。Vue 组件测试 45/45 通过，isolated production build 成功；runtime dist 增加 4 个带 hash 资产并更新 index 与 remoteEntry，保留旧 hash 资产。

2026-10-24 Chrome 现场 preview：SONE-288 默认选角为葵司，结果 `1 success / 0 failed`，目标为 `/media/Link/AV/葵司/SONE-288 (2024)/SONE-288(2024).mp4`；手动改选坂道美琉后页面提示重新预览且禁用确认，重新 preview 同样 `1 success / 0 failed`，目标为 `/media/Link/AV/坂道美琉/SONE-288 (2024)/SONE-288(2024).mp4`。未确认或执行，未发生 transfer/scrape；源文件 stat 前后精确相同、`nlink=1`，且未创建目标目录。0.8.9 已部署，77 个 runtime 文件；部署审计保护项通过。preview 后审计中 core、public、config、binding、media 保持不变，ledger/cache 变化符合预览与 GET 的预期。前两次来源请求返回 `transport_unavailable`，随后相同配置连通，GET 和两次预览成功；具体网络原因未定位。现场材料：[默认预览](../outputs/manko-default-first-actress-20261024/sone288-default-first.jpg)、[手动改选预览](../outputs/manko-default-first-actress-20261024/sone288-manual-second.jpg)、[结果证明](../outputs/manko-default-first-actress-20261024/sone288-preview-result.jpg)、[preview 后审计](../outputs/manko-default-first-actress-20261024/previewed-audit.log)。真实新下载自动全流程仍未验收。

0.8.7 按本次识别的 Manko packet `actors[*].avdbs_actor is True` 选择女主演；数值 1、字符串 `true`、false 或缺失均不满足。可信候选仅用于目录选择，NFO 保留完整演员列表。执行时自动选择须仍为唯一候选；手动选择须仍匹配且保持 true 标记，不相关候选变化不会使其失效。验证：官方 MoviePilot V3 SDK 定向测试 `372 passed, 7 subtests`；前端测试 `44/44`，production build 成功。候选 1 的 6 个失败是 SDK `MediaInfo.to_dict()` 暴露动态私有属性；候选 2 改用插件内弱引用证据映射后全组通过。新版页面现场 ABF-382 自动选角预览为 1 success/0 failed，未执行整理；ADN-765 预览在既有来源身份候选歧义处停止，未进入女主演规则判定。部署审计摘要及证据链接见上文。

0.8.8 将同番号多 ID 选择改为 API 顺序下的完整数据包选择，不按 ID 最小值、译文数量或未映射字段差异择优，也不拼接记录。正式 MoviePilot V3 SDK 定向回归 `503 passed, 7 subtests`。已部署生产并审计 73 runtime 文件；部署清单中 core 有 6 个文件、public 有 1167 个文件；配置与 binding revision 9、3 个下载源及对应媒体、3 份 ledger 和两份 metadata 缓存均保持，自动整理 off、unarmed、queue 0。现场 `GET /media/search` 的 ADN-765 与 ADN-775 各返回 1 项且标题、简介、图像、日期及时长字段完整；前后状态与备份基线均保持。证据见[GET smoke](../outputs/manko-scrape-data-selection-20261024/live-search-smoke.log)和[最终审计](../outputs/manko-scrape-data-selection-20261024/final-audit.jsonl)。无前端改动，因此未新增前端测试或 build；本次未做 Chrome 新 UI 验收、preview、transfer 或 scrape。

0.8.6 在独立目录浏览器中支持名称升/降序、修改时间新/旧排序及文件大小大/小排序，目录始终优先；同名和主键相同时以名称/路径稳定排序，无效或缺失的数值元数据置于同组末尾。浏览项目的 `modify_time`（Unix 秒）与 `size`（字节）是可空返回字段，旧响应仍兼容；前端排序只处理当前目录列表，不额外请求接口或改动手动选择路径。

0.8.5 调整自动匹配规则的本地网格布局，为浮动标签留出说明后的空间；窄屏下字段改为单列，来源路径可随内容增高，目标完整路径可通过提示查看。自动规则字段、浏览动作和保存行为保持原合同。

0.8.3 当时在完整等价检查中合并互补的 Manko 标题与简介译文，保留最小原生 ID；同语言冲突、重复语言项、无效译文结构、无共同语言锚点或其它字段不一致时仍拒绝自动合并。只含平面标题的旧记录仍可通过插件识别和手动搜索；该历史策略已由 0.8.8 的 API 顺序完整数据包策略取代。

0.8.4 增加可选女主演目录：目录名只来自完整 ATTACKERS 官方“女優”行与本片演员的精确匹配；信息不足时要求手动选择，自动整理明确停止。预览/执行持续复核演员选择和子目录安全性，默认保持关闭。2026-10-23 验证通过：官方 V3 SDK 隔离合成回归 `1552 passed, 7 subtests`（含 synthetic real-native 执行和完整刮削），Vue 组件测试 `36/36`，dist build 成功。该日部署阶段，ADN-765 与 ADN-775 在无 ID、无人工选择时的 NAS preview 均成功；当日仅做 preview，没有执行整理。2026-10-24 随后通过 MoviePilot 原生 reorganize 接口完成两部影片整理，硬链接、源 SHA、NFO 和图像审计通过，详见[真实整理记录](../records/manko-actress-reorganize-2026-10-24.md)。

0.8.3 使用官方 MoviePilot V3 SDK 的离线合成测试通过 `1544 tests` 和 `7 subtests`。2026-10-23 现场核验中，ADN-775 与 ADN-765 的 Manko 搜索各返回一个规范 ID；ADN-775 在未手填 ID 的原生手动预览中自动识别，且只执行一次。宿主状态确认文件整理完成，源与目标为同一 inode、源 SHA-256 不变；新增 NFO 的身份及标题、年份、简介、演员、导演、片商、时长字段齐全。poster (800×538) 与横向 fanart/backdrop (800×534) 均已生成；背景图与 poster 哈希不同，fanart 与 backdrop 哈希一致。此现场结果覆盖这两个查询样本及 ADN-775 单次整理，不代表真实 qB 新下载自动流程已验收。

0.8.2 增加匿名、有界的背景图图片头探测，跳过竖版缩略图并选用首个合格横图；宿主配置重载会清空探测 memo，探测期间若来源配置撤销则不返回过期图片地址。0.8.1 改进 Manko 的 poster/fanart 分离、有效时长及 NFO 导演/片商字段；ADN 编号使用受限的 ATTACKERS 官方详情补充缺失信息。元数据缓存升级为 `metadata-v2.json`，旧缓存保留但不再读取。官方详情解析以合成 HTML 测试，不访问真人站点。

原有离线测试基线使用官方 MoviePilot v3.1.1（commit `6ff7dd86a14c403b0e408f778aa3174e13e15ef1`）。0.5 全量回归使用热修前官方基线 `05967cdc0f10c93b55a7a01acd34c57d5f15df67`、真实 V3 SDK 和 Python 3.14.7，`1427 passed in 37.90s`；没有叠加核心补丁、联网或挂载生产媒体/配置。原生执行集成用真实模块 dispatcher、本地存储、SQLite、事件处理器及刮削链；只有远程元数据 provider 使用合成数据。NFO 的标题、年份、简介、演员、来源原生 ID 和番号已核验；图片下载未覆盖。联邦页面另有 12 项 Vue 组件测试通过，最终构建通过。

0.6 自动触发使用相同的热修前真实 V3 SDK，完整离线回归 `1456 passed in 38.46s`，另有 15 项 Vue 组件测试及最终构建通过。该回归禁止真实 DNS/socket，不连接生产下载器或真实媒体站点、不挂载生产媒体/配置；这些测试不代表真实新下载已经完成自动整理。

0.8.0 的断网真实 SDK 后端全量回归为 `1491 passed in 42.54s`；真实 Vuetify 组件测试为 `30/30`，独立前端构建通过。测试使用合成数据并阻断真实网络；原生硬链接、NFO 和图片落盘仅在隔离合成文件上验证。它们不证明生产媒体处理或真实新下载的自动整理已验收。

0.8.0 增加自动匹配方式选择和手动自选本地源/目标；旧自动配置保留 tag_and_path 语义。tag-only 以 MoviePilot 映射的任务路径限制候选，并在发现/交接边界检查原生监控重叠。模块 `plugins.v3/xiaojiejiescraping`，类 `XiaojiejieScraping`，索引 `package.v3.json`。页面开关读取宿主插件配置 API 的 `{success,message,data}` 响应，要求成功且 `data` 为包含布尔型 `enabled`、`discovery_enabled` 的完整对象后，才只更新 `discovery_enabled` 并 PUT；状态 API 回读确认切换结果。旧版直接返回原始配置对象时，仅在上述必需字段有效时兼容。页面会按 `auto_new_only` 显示启用边界。此前版本 `0.2` 在版本比较中低于历史开发版 `2.0.0`，既有安装不一定出现自动升级提示。宿主支持的 PEP 440 范围为 `>=3.1.1,<4`，不硬锁持续更新的 V3；未来 V3 兼容性尚未验证。

### 部署后核验

2026-10-23 0.8.4 女主演目录现场核验：部署清单为 57 个运行文件；core6/public1167、apply 时配置和三个 ledger 均不变，binding revision 为 9。随后配置恢复通过单次 PUT 200，仅启用 `actress_subfolder`；自动流程关闭、queue 为 0。页面开关已核验为勾选（截图见 [`outputs/manko-actress-layout-20261023/ui-actress-enabled.png`](../outputs/manko-actress-layout-20261023/ui-actress-enabled.png)），默认目标仍为 `/media/Link/电影`，AV 目标由用户自由选择。ADN-765/775 均在无 ID、无人工选角时成功返回各自女主演目录下的预期 native preview 目标。审计确认 manifest/source 匹配、手动 ledger integrity OK、handoff/auto ledger 不变、源 stat 与 nlink=2 保持，既有平铺目录、sidecars 和 AV root 不变。**本次现场没有执行新的整理或迁移，也没有创建 NAS 新目录**；因此只有部署和预览证据，没有真实执行的硬链接、改名或刮削落盘证据。

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
