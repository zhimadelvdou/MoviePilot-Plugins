# JavBus 与 Manko 原生整理文件名差异

## 目标

以同一合成番号、源文件、目标目录和 MoviePilot 原生重命名模板，在正式 `ManualTransferWorkspace.preview` → `native_transfer(preview=True)` 路径复现两来源目标文件名差异。只观测原生预览目标和保留的标题 metadata，不执行硬链接或刮削。

## 当前边界

- JavBus 整理命名默认按现有 Manko 规则使用已核验番号及主机模板中的年份/版本字段；完整标题保留在搜索、预览和 NFO 中。
- 仅改变宿主重命名扩展点收到的 formatter `rename_dict["title"]`；不修改 `MediaInfo.title`、`original_title`、元数据投影或全局模板。
- Hook 只对当前启用的 JavBus source、电影类型、番号 native ID，以及 `_owned` 证明的同来源 packet 生效；旧宿主缺少扩展事件时保留宿主默认命名且插件导入不失败。
- 离线合成 fixture 必须保留原生 `TransferChain` 预览路径，禁止以字符串拼接或替身 formatter 作为命名验收。

## 验收

- SDK repro2：1 failed、6 passed，8.24s，原生预览目标一致断言失败。
- SDK Green2：430 passed、8 warnings，38.32s，exit 0；原生预览目标一致及 NFO 完整标题断言通过。日志见同级 issue 记录。
