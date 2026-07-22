# 启动链路合并前复审修复交接

更新日期：2026-07-22

## 结论与版本边界

- 合并基线：`edit-base` / `6ff592f72a6a4fd8575d5bd392e035dd2a95a12a`
- 复审起点：`06a39362289de311368170f6271a0dc4f4de7b15`
- 当前实现状态：`review_remediation_complete`
- 正式跨平台证据：`pending_manual_validation`

本轮复审发现并修复了启动 terminal 边界、后台 import 资源所有权、
Recognition 启动期回退和 packaged A/B 同构校验四类问题。用户确认的产品策略为：

1. People/Pets dashboard 只在首次进入 People 时异步预热。
2. Windows/Linux pre-show Detail 创建失败时显示基础窗口和非模态恢复面板，允许新 generation 重试。

## 已完成的修复

### 生命周期与资源所有权

- settings 和 shell 构造失败会在 generation 内写入唯一 `startup.failed` 并安全返回非零。
- pre-show feature 异常转换为 `startup.degraded`；当前 generation 的 job、probe、input guard 和 import 发布均被取消。
- 模块预载由 generation-aware owner 管理，完成信号直接唤醒 GUI job queue，不再保留 10 ms 轮询 timer。
- 旧 generation 的迟到 import 结果不会发布；应用退出会关闭 job queue 并等待预载线程结束。
- 已存在 `DesktopCoordinatorRuntime` 的重试不会重复预载模块或构造 coordinator。

### Recognition 懒加载

- `startup.idle_jobs` 不再调用 People dashboard warmup。
- `RecognitionCoordinator` 只在 People feature 实际创建时构造；随后先启动缓存快照读取，再绑定页面。
- AI 扫描仍需 People 页面可见且首 viewport ready，不因 dashboard 快照预热提前启动。

### Packaged A/B 证据

- `tools/build_manifest.py` 生成 schema v1 构建清单，分离 source revision、artifact SHA 和环境指纹。
- 环境指纹覆盖 Python/PySide6/Qt/Nuitka、依赖快照、build driver、有效 flags、native runtime 和资源集合。
- macOS、Windows、Linux standalone 和 AppImage 构建脚本生成对应清单。
- packaged `startup_benchmark.py collect` 强制要求 `--build-manifest`，并校验 revision 和实际启动 executable SHA。
- `compare` 只有在 baseline/candidate 环境指纹一致、manifest revision 对应且 artifact SHA 存在并不同时才可能 PASS。

## 验收与已知边界

定向验收覆盖 terminal 唯一性、取消后的迟到 import、Recognition 首用预热、构建清单和 A/B 拒绝路径。完整平台性能数字仍按 `STARTUP_BENCHMARK_RUNBOOK.md` 采集。

用户已报告在多平台完成手工 smoke，未发现明显 bug。由于没有提供逐平台 artifact、build fingerprint、场景和原始报告，该信息记录为 `user_reported_multi_platform_smoke_pass`，不转换为 `cross_platform_validated`。

后续接手者不得重新把 Recognition、地图、Edit 或 People/Pets 扫描放回 startup generation。若需恢复后台预热，必须在 `startup.completed` 之后使用独立、可取消且不影响 terminal 的任务所有者，并以目标平台 profiler 证据说明收益。

本目录继续保留在 `docs/requirements/startup-chain-optimization`。只有正式人工矩阵和匹配 build manifest 的 packaged A/B 证据闭环后，才移动到 `docs/finished/requirements`。
