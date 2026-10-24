Status: resolved
Type: task
Blocked by:

# JavBus 匿名来源接入

将已注册的 JavBus 离线适配器补足为可选匿名来源，保留现有来源选择与女优可信证据规则。FANZA 暂时弃用。

## 当前证据与门禁

- 当前基线为 `main@f0ddf803dd7df8f8574f425d951ddf3bb4419e42`，工作树干净。
- JavBus 首页/详情字段：容器 `h3` 标题、`bigImage img` 封面、信息栏编号和日期；演员存在 `avatar-box` 与 `star-name a[title]` 两种结构。
- ROOT 2026-10-25 实站只读探测只获得 driver-verify 重定向或 HTTP 429；尚无正常详情页解析证据。
- 用户确认 JavBus 无需账号登录；未授权 Cookie/认证/浏览器方案。本轮不做任何认证变化。

## 实施范围

- JavBus 核心字段解析、精确身份校验、异常/受限状态安全诊断及现有 API/预览可读反馈。
- `sources` 缺省仍只有 Manko；显式来源选择、包括空列表，原样保留；用户保存包含 JavBus 的来源多选后，该源才出现在手动菜单并参与自动识别，仍使用单一 `sources` 设置。
- 既有手动来源与预览路径验证 JavBus `source`/native ID；不得为 JavBus 演员添加可信女优标记。自动女优选择/分目录只接受 Manko 明确证据。
- 同步 README 与 `__init__`/`package.v3.json` 版本至 0.8.10。

## 验收

- 隔离 SDK 离线覆盖：两类演员 DOM、核心字段、可缺导演/片商、错误番号、验证重定向、429、真正登录页、404、timeout 的同步与异步原因；诊断不泄露正文、敏感 URL/query、Cookie、代理或异常文本。
- 来源配置、手动选项、预览及女优证据约束由既有公开插件接口验证。
- ROOT 单独执行 network-none SDK candidate；旧 `test_javrecognize_provider` 44 条基线失败须独立比较，不冒充全绿。
- 实站门禁未过前只表述为“匿名接入代码已就绪/待实站验收”，不宣称实站成功；不部署、不启用生产自动整理、不联网、不提交。

## Comments

- 初始实现门禁：只做 JavBus；FANZA 不接入缺省或本轮变化。



## Answer

实现票已完成。JavBus 通过现有 `sources` 配置作为显式可选匿名来源；默认仍只启用 Manko，已有显式列表（包括空列表）不被追加或替换。适配器核验来源编号、兼容 `avatar-box` 与 `star-name` 演员结构，不给 JavBus 演员添加可信女优证据。状态分类仅限 JavBus，诊断不记录正文、URL/query 或异常文本；单次手动预览通过并发隔离的诊断捕获显示固定安全提示。JavBus 封面须由用户将实际主机精确加入 `image_hosts`；默认空值无海报。版本与 README 已同步为 0.8.10。

验证：network-none 定向 SDK `504 passed in 34.03s`；完整 22 个测试文件 `1558 passed, 44 failed, 8 warnings, 7 subtests passed in 46.57s`。44 个失败 nodeid 与 0.8.9 基线集合一致（SHA-256 `48bbe2a2b6dde3b87fe1067a3378374813917b391ed310e5325c23e6e9514b51`），无新增或缺失；8 warnings 为既有多线程 fork 弃用警告。候选 g3 与完整测试候选 full-g2 的 SHA-256 均为 `2e6b631282e14de95f3c35fb8757e48160c9e9e7ec0abad0731bb0f7b408cab5`。

真实站点门禁仍未通过：2026-10-25 只读请求遇到 driver-verify 重定向或 HTTP 429，没有取得正常元数据。本票完成不表示实站成功或部署验收；本轮未部署、未启用生产自动整理、未配置账号/Cookie，FANZA 暂缓。
