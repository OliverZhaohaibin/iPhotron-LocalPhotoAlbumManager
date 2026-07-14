# 启动链路工程收口报告

更新日期：2026-07-14

## 当前状态

- 实现状态：`engineering_implementation_complete`
- 本机环境：macOS Apple Silicon，Nuitka packaged Metal/OpenGL
- 本机 packaged 状态：`local_packaged_offscreen_pass / cocoa_manual_run_required`
- Windows、Linux、macOS Intel：`pending_manual_validation`
- 全平台状态：`pending_manual_validation`
- 需求归档：保留在 `docs/requirements/startup-chain-optimization`，不得提前移动到 `docs/finished/requirements`

`engineering_complete` 只在最终 v4 Cocoa/Metal/OpenGL 本机验证和固定 baseline
`6ff592f7` 的同构 30 次 A/B 证据通过后写入。其他平台未做实机测试，因此本报告不输出“全平台 PASS”。

## 已收口的工程链路

### 生命周期与 GUI 调度

- 每个 generation 由独立 `StartupAttempt` 管理 job queue、probe、watchdog、warmup timer、hang diagnostics 与取消资源。
- completed、degraded、failed、cancelled 进入统一清理，terminal 事件按 generation 唯一。
- probe 使用一个长期 controller 连接并按 request/generation 路由，旧结果不能回写新会话。
- 启动装配已拆为带名称、幂等、逐 event-loop tick 执行的 job；异常只终止当前 generation。
- `DesktopCoordinatorRuntime` 是唯一桌面组合根，Recognition、Location/Info、Edit 与地图能力均延迟到首次使用。
- People/Pets 的模型扫描不再在 startup completed 后自动启动；首次进入识别功能才构造服务与 worker，消除快速关窗的 QThread 竞争。

### Probe、数据库与慢存储

- packaged helper 在 Qt GUI 导入前分流，stdin 请求限制 256 KiB，stdout/stderr 和 album 数量均有上限。
- v2 协议包含 `StorageProfile`、带 root/database/schema/file identity/timestamp 的 `PreparedLibraryCredential`，提交前生成单次消费的 `ValidatedPreparedLibrary`。
- GUI repository 不再用 root 全局集合冒充 prepared；凭证变化或数据库替换会拒绝提交。
- album snapshot 同时受 750 ms 和数量预算约束，超预算返回部分结果和 warning。
- Windows drive type、Linux mountinfo、macOS mount 信息用于 local/removable/network 分类，API 失败才使用耗时回退。
- SQLite 迁移保持逐版本事务、online backup、恢复状态和稳定错误码；未改变 schema。
- 无库状态使用 unbound repository，不创建数据库或 thumbnail 磁盘缓存目录。

### Watcher、图库与 Detail

- `LibraryWatchService` 在专用线程完成 watcher 注册、轮询和目录快照，GUI 只接收不可变结果。
- local 库分批注册；network/removable 使用 generation-aware 轮询，切库废弃旧结果。
- Detail pre-show 只保留平台所需 surface；shell、header、player、filmstrip 分到 show 后 job，Edit sidebar、视频控制与识别 overlay 首用创建。
- 地图探测和扩展安装退出关键链；macOS packaged 地图以单个未压缩 tar 封装，首次地图使用时在后台安全解包到 Application Support。

### 打包与跨平台静态门禁

- 新增包外 `src/entrypoint.py`，避免 `iPhoto/io` 在 Nuitka bootstrap 阶段遮蔽标准库 `io`。
- PySide6 `Qt.labs.assetdownloader` 静态库/PRL 的 Nuitka 4.0.x 误解析通过可恢复临时隐藏规避。
- 排除 PyTorch C++ headers，避免 codesign 参数超过 macOS `ARG_MAX`；地图 45k 文件压成一个 runtime archive。
- packaged probe 使用 `QCoreApplication.applicationFilePath()` 启动当前真实二进制，支持最终可执行文件改名。
- Windows/Linux 的入口、路径规范化、helper 生命周期、storage contract、XCB/Wayland 与地图降级均由 platform monkeypatch、协议往返和打包结构测试覆盖。
- CI 不再默认排除启动测试；benchmark 强制独立 settings、独立图库和 `--confirm-dedicated-library`。

## 已取得的证据

- 全量自动化：`2624 passed, 11 skipped`。
- `tools/check_architecture.py`：通过。
- `compileall`、`git diff --check`、macOS/fast build shell 语法：通过。
- v4 packaged 低内存构建、严格 codesign、arm64 架构、资源裁剪、710 MB 地图 archive 和 protocol v2 helper 往返：通过；warm helper 0.12 秒。
- v4 packaged/offscreen 完整链 3/3 eligible，全部唯一 `startup.completed`、退出码 0，无 probe/AI/QThread 泄漏；`show -> interactive` P95 5.016 ms、probe P95 105.316 ms、最大 GUI job P95 45.024 ms。
- v3 有效 Metal 样本：`show -> interactive` P50 33.208 ms；probe P50 115.581 ms；library ready 后首图库 P50 16.295 ms；首缩略图 P50 37.836 ms。
- source 全新库扫描中快速关窗：退出码 0，无 `QThread: Destroyed`；该结果仅用于定位和关闭竞态验证，不替代 packaged 证据。

v3 三次 smoke 中首轮暴露 AI worker 快速关窗 abort，随后已修复并纳入 v4；因此 v3 只作为问题发现证据，不作为最终通过证据。Metal 样本曾观察到一次 `feature.detail.post_show=112.5 ms`，当前 2 个有效样本的 max GUI job P50 95.770 ms、P95 105.957 ms，最终 30 次门禁仍须以 v4 为准。当前 Codex GUI 沙箱在 `QApplication` 创建时拒绝 macOS pasteboard/HIService，且沙箱外执行权限额度被运行环境拒绝；这三次 `-6` 不属于应用证据，Cocoa/Metal/OpenGL 必须用 runbook 命令在普通终端手工运行。

## 尚未满足的关闭条件

- v4 Cocoa/Metal、OpenGL packaged 完整链和关窗清理必须在普通终端通过。
- candidate 与固定 baseline `6ff592f7` 必须使用相同平台、架构、依赖、Nuitka、native runtime、场景和 build flags 各采集 30 次热启动。
- 只有可证明的 OS cache eviction 才能标记正式 cold；本机无法证明时写 `non_formal`。
- Windows、Linux、macOS Intel 的人工实机矩阵仍为 `pending_manual_validation`。

在上述证据补齐前，准确结论是：架构和实现已完成工程收口，已知启动关键链技术债已最小化；性能统计和跨平台实机证据尚未完成，不能宣称 `engineering_complete` 或 `cross_platform_validated`。

## 技术债审计结论

当前没有理由继续扩大 facade/coordinator 重构。剩余风险均有明确边界：Detail packaged P95、固定 baseline 对比、真实离线介质、平台图形/打包环境。它们属于证据与定点优化工作，不是新的架构欠债。任何后续改动应由 profiler 或目标平台故障证据驱动。
