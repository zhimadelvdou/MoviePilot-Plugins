Status: resolved
Type: task

# 手动整理来源路径排序

为手动目录浏览增加名称、修改时间和文件大小的升降序选择，目录始终优先。后端只为既有可选目录项返回可空修改时间与字节数；保持路径范围、安全过滤、文件选择和原生整理行为不变。

## Progress

- 已确认浏览接口与现有源/目标过滤由插件自己的 `ManualTransferWorkspace.browse` 实现。
- 已补充可空修改时间和文件大小元数据，并在前端实现六种本地排序；文件夹始终优先，选择与导航保留原路径。
- 前端回归 43/43 通过；官方 SDK 隔离测试 `tests/test_plugin_manual.py` 为 32 passed；production build 成功。
- Chrome 实页检查 91 条目及六种排序：使用 API 返回值独立核验顺序，检查同组名称升/降序互逆、目录优先和路径集合一致；排序后进入 ADN-765 子目录、返回、刷新均保留大小升序，选中 ADN-775 源路径正确。仅选择路径，未预览或执行媒体操作。
- 桌面 1920px 与手机 390px 检查无横向溢出，排序工具栏上边距 16px；浮动标签间距 7px。检查后 reload 恢复空 source，viewport 已重置。实页证据记录于 `D:\Codex\15-处理nas相关\outputs\manko-path-sorting-20261024\visual-verification.json`。

## Answer

已完成并验收。排序只作用于当前浏览列表，目录始终优先；缺失的数值元数据不阻断浏览或名称排序。前后端回归、production build 和实页检查均通过，未执行媒体整理操作。
