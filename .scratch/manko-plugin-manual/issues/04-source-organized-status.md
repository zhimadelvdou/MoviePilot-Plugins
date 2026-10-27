Status: resolved
Type: task

# 手动来源浏览整理状态

在手动整理页的来源目录浏览中，允许按需查看已有账本能够证明的文件整理状态。仅将回读核对当前源文件版本与目标硬链接相符的完成记录标为“已整理”；目录扫描或历史不完整时显示未知或已核实数量的下界。该状态说明文件传输，不表示刮削、NFO 或图片已完成。目标浏览和自动规则浏览不显示状态。

## Progress

- UI worktree 从 main 快进后，在 `xiaojiejie/feature/source-organized-status` 实现；范围限于插件账本、手动浏览 API、插件页面、相关测试及 dist 资源。
- 增加只读账本查询；浏览不初始化账本、不写状态。手动和自动完成记录均需重新检查源与目标硬链接；自动复制记录不标记完成。
- 来源目录对候选记录路径做有界扫描；预算、账本读取、目录扫描或记录字段异常时按未知/不完整处理。
- Vitest 48/48 通过；Vite production build 成功。SDK candidate 首轮失败于两个被注入预算/扫描错误的测试 fixture；实现未因此放宽安全门禁，修正 fixture 后等待重新运行。
- Official SDK 隔离测试通过：389 passed，31.93s；Vitest 48/48，Vite production build 成功。

## Answer

已完成实现与离线验收。没有执行部署或真实媒体整理；整理状态只证明被核实的硬链接文件传输。
