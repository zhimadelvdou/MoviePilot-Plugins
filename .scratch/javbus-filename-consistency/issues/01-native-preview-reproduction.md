Status: resolved
Type: task

# 复现 JavBus 与 Manko 手动整理文件名差异

用户报告 JavBus 整理出的文件名与 Manko 不同。Root 的只读证据显示两来源目标名称形态有差异；现有代码会把来源投影标题交给使用 `title` 的原生格式化模板。

## 范围

按当前产品决定，使用同一合成源文件、目标目录和 MoviePilot 原生 rename preview，使 JavBus 使用与 Manko 相同的番号/年份命名格式。只在 formatter 的隔离 `rename_dict` 调整 `title`，保留 `MediaInfo` 标题与 NFO 原标题；执行、刮削和部署不在本票范围。

## 结论与验证

- 默认使用现有 Manko 规则处理 JavBus 整理命名：目标名使用已核验番号，保留宿主模板中的年份与版本字段；不将此记录表述为用户另行明确确认。
- 正式 SDK repro2 红测：`outputs/javbus-filename-consistency-20261027/javbus-filename-repro2.log`，1 failed、6 passed，8.24s；失败点为两来源原生预览目标一致断言。
- 新增 `tests/test_plugin_naming.py` 覆盖命名数据范围、媒体身份、生命周期和缺失宿主 Hook 回退；原生预览测试验证 JavBus 标题与 `metadata_nfo` 保持完整。
- 正式 SDK Green2：`outputs/javbus-filename-consistency-20261027/javbus-filename-green2.log`，430 passed、8 warnings，38.32s，exit 0。真实原生两来源目标一致断言通过，NFO 标题保留断言通过。

实现只改隔离 formatter `rename_dict["title"]`，不修改原始 `MediaInfo` 或 NFO 标题。未执行生产部署或真实媒体整理。
