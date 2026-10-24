# 女主演目录

配置项“按女主演分目录”默认关闭。开启后，自动与手动整理都在用户所选媒体库目录下创建“女主演姓名 / 原生影片目录 / 视频”的布局；插件继续调用 MoviePilot 原生预览与整理入口，保留源下载。

女主演资格使用本次 Manko 原始识别 packet 的顶层 `actors`。仅 `actor.avdbs_actor is True` 的记录有效（严格布尔判断；数值 `1`、字符串 `true`、false、null 和缺字段均不有效）；姓名必须是安全单目录组件，并与当前识别 MediaInfo 的演员姓名精确对应。安全姓名去重后仅一人时自动选择；多人时只允许从这些可信候选中人工选择；零人时不提供 cast 选择，也不预览或执行，提示等待可信来源标记。其它来源沿用既有演员候选行为。

来源标记证据私有地绑定在本次 MediaInfo 对象的 Manko source、原生 media ID、番号和姓名集合上，不写入公开 MediaInfo、NFO 或新的缓存/账本字段。识别缓存命中从对应缓存 packet 重建私有证据；`cache=False` 必须从本次新 packet 建立证据，不得回退旧缓存。候选还必须与当前 MediaInfo cast 交集匹配。手动预览、执行前重校验与 qBittorrent 自动交接使用同一证据。执行时自动唯一候选必须仍唯一且为原选择；人工选择只要求所选姓名仍匹配并保持 `avdbs_actor is True`，不相关候选变化不使选择失效。所选姓名标记移除、变为非 true 或名字不再匹配时必须 fail closed。所有 cast 仍写入 NFO。

手动预览遇到多个可信候选时返回候选与选择状态，并且在选择完成、重新预览前不调用原生 transfer；零候选时说明等待标记，不展示普通 cast 供选择。执行必须基于新预览，并重新核验来源文件、媒体来源与 ID、选择项、选择依据、配置签名、目录范围和目标。自动选出的唯一可信姓名在执行时若不再唯一或不再是原选择，必须拒绝提交；人工选择只在所选姓名不再有 true 标记或不再匹配时拒绝，其他候选变化不影响有效选择。姓名必须是安全的单个目录组件；预览不创建目录，目标路径仍由原生预览返回。现存演员子目录若为符号链接或非目录必须拒绝；预览后被替换时执行也必须拒绝。

自动整理若无法唯一确定女主演，应以明确原因 `primary_actress_choice_required` 停止，留下待人工选择提示并可通过现有手动页处理；不得反复领取或标记完成。不得修改 MoviePilot 核心、metadata-v2 缓存路径或账本 schema。

旧配置默认关闭，公开 MediaInfo 结构不变。开关进入手动配置与自动 handoff 签名；开启时缓存中的旧版官方标记不能替代本次原始 packet 的 `avdbs_actor is True` 证据。版本为 0.8.7。

## 验证状态

- 2026-10-23：官方 MoviePilot V3 SDK 隔离合成回归 `1552 passed, 7 subtests`；合成 real-native 执行覆盖完整刮削。Vue 组件测试 `36/36`，dist build 成功。这些执行和刮削均发生于隔离合成环境。
- 2026-10-23：现场部署 `0.8.4`，核对 `57` 个运行文件；core6/public1167 保持，部署时配置未变、binding revision 为 9，三个 ledger 均未变。随后 config-resume-v3 单次 PUT 返回 200，仅 `actress_subfolder=true`，自动流程仍关闭且 queue 为 0。Chrome 已核验开关为勾选状态（截图见工作空间根目录 `outputs/manko-actress-layout-20261023/ui-actress-enabled.png`）；默认目标仍是 `/media/Link/电影`，AV 目标可由用户自由选择。
- 2026-10-23：现场 NAS 对 ADN-765 与 ADN-775 均在无原生 ID、无人工演员选择时成功 preview，分别返回 `/media/Link/AV/美園和花/ADN-765 (2026)/ADN-765(2026).mp4` 和 `/media/Link/AV/栗山莉緒/ADN-775 (2026)/ADN-775(2026).mp4`。审计通过：manifest/source 匹配、手动 ledger integrity OK、handoff/auto ledger 不变、源 stat 与 nlink=2 保持、现有平铺目录/sidecars/AV root 不变。
- 现场证据仅证明部署和两项真实 preview；没有执行 NAS 上的新整理或迁移，也没有创建新的 NAS 目录。目录存在与硬链接、改名、刮削/副产物生成仍未通过本次现场执行验收。
