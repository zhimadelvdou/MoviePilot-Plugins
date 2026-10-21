# Manko 自动触发

- [规格](spec.md)
- [实施票](issues/01-automatic-trigger.md)

## Decisions-so-far

- 复用官方插件定时任务和既有 qBittorrent 接入，保持手动入口与已有热修。
- 新完成边界必须持久、可验证，缺失或损坏时停止。

## Notes

- 0.6 完整隔离回归1456项、Vue15项与构建通过，已备份并部署到NAS。
- 真实SDK检查到100项有效完成时间；原生监控规则与AV根目录无重叠。已开启Qb/AV的新完成任务自动硬链接整理和刮削。
- [实施票](issues/01-automatic-trigger.md) 已完成；线上证据位于工作空间 outputs/manko-automatic-trigger-20261022，当前事实见 records/manko-automatic-trigger-2026-10-22.md。

## Fog

- 后续真实新下载自动整理及图片落盘，须等实际新任务核验；不伪造生产下载来替代验收。
