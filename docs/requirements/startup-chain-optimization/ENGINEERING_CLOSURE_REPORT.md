# 启动链路工程收口报告

更新日期：2026-07-27

## 当前状态

- 实现状态：`automated_core_pass / release_artifact_and_manual_validation_pending`
- 本机环境：macOS Apple Silicon，Nuitka packaged Metal/OpenGL
- 本机 packaged 状态：`local_packaged_offscreen_pass / cocoa_manual_run_required`
- Windows、Linux、macOS Intel：`pending_manual_validation`
- 全平台状态：`pending_manual_validation`
- 需求归档：保留在 `docs/requirements/startup-chain-optimization`，不得提前移动到 `docs/finished/requirements`

`engineering_complete` 只在最终 v4 Cocoa/Metal/OpenGL 本机验证和固定 baseline
`6ff592f7` 的同构 30 次 A/B 证据通过后写入。其他平台未做实机测试，因此本报告不输出“全平台 PASS”。

2026-07-22 的启动链复审修复仍保留；2026-07-27 的 Pets 二次审查确认 runtime/state
提交边界、durable identity 恢复、跨类型 annotation 操作和 stacked PR CI 仍有阻断，
因此之前记录的 Pets 自动化完成结论已经撤回。对 Pets 模型契约、大图库
复杂度、跨库一致性、升级回填、打包模型目录和身份语义的复审已完成本机自动化
核心修复。完整问题、裁决和验收要求见 `PETS_REVIEW_REMEDIATION_LEDGER.md`。项目固定
[`pet-models-v1`](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/tag/pet-models-v1)
TorchScript Release 已发布，并由 [run 32801774572](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/actions/runs/32801774572)
在 Ubuntu、macOS、Windows 验证同一资产。只读 packaged 安装、
真实升级库、网络失败和跨平台 50k 报告仍为
`manual_validation_pending`，因此不得恢复 `engineering_complete`。

用户报告已在多平台手工 smoke 且未发现明显 bug。因未附逐平台 artifact、
build fingerprint 与报告，该记录标记为 `user_reported_multi_platform_smoke_pass`，
不替代下述正式人工矩阵。

Windows 实机复测表明前次 WIC fresh-decode 修复会被已持久化的错误 Detail surface
绕过。neutral-surface 解码语义契约现已独立提升，旧错误 surface 会 miss 并重新解码；
文件格式 schema、Live Motion 视频投影和其他 EXIF 规则均未扩大修改。真实 Windows
原问题样例已由用户复测确认通过；正式 packaged artifact、build manifest 和日志证据
仍为 `pending_manual_validation`。

## 已收口的工程链路

### 生命周期与 GUI 调度

- 每个 generation 由独立 `StartupAttempt` 管理 job queue、probe、watchdog、warmup timer、hang diagnostics 与取消资源。
- completed、degraded、failed、cancelled 进入统一清理，terminal 事件按 generation 唯一。
- probe 使用一个长期 controller 连接并按 request/generation 路由，旧结果不能回写新会话。
- 启动装配已拆为带名称、幂等、逐 event-loop tick 执行的 job；异常只终止当前 generation。
- `DesktopCoordinatorRuntime` 是唯一桌面组合根，Recognition、Location/Info、Edit 与地图能力均延迟到首次使用；People dashboard 快照只在 People feature 首次创建时预热。
- settings/shell 同步初始化和 Windows/Linux pre-show Detail 异常也进入唯一 terminal 协议；pre-show Detail 使用可重试降级窗口。
- 模块预载使用 generation-aware owner 和完成信号，不再依赖持续轮询 timer；退出时等待预载线程收口。
- People/Pets 在主 metadata scan 成功后通过 1500 ms 交互空闲门控自动启动；worker 使用最低优先级，切库、取消和 shutdown generation 阻止迟到启动。

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
- packaged benchmark 强制 build manifest；A/B 会校验同构环境指纹、manifest revision 和不同 executable SHA。

## 已取得的证据

- 当前分支全量自动化：`2764 passed, 14 skipped`。
- Windows Live Photo 静态 HEIC 仅在 WIC frame 已精确匹配应用 EXIF 后的展示尺寸时跳过二次换轴旋转；原始尺寸 JPEG/WIC 分支仍应用 EXIF。前一视频投影修复 `4e522e60` 已完整反向恢复。
- Detail neutral-surface 解码契约从 1 提升为 2；旧 WIC 错误 surface 不再绕过修复后的 fresh-decode 路径。旧缓存迁移回归、完整 Detail 门禁及全量测试通过。
- Pets 真实模型契约：`1 passed`；固定 YOLOX/dog fixture SHA、CPU provider、
  raw-BGR、类别、bbox、置信度和去重通过。
- Pets 规模契约：`1 passed in 65.08s`；空库按 batch 16 增长到 1k/10k/50k 与
  50k+2 通过时间、RSS、WAL、增量写入和结构门禁。
- Pets 修复覆盖 generation/key migration、stale 结果、old/new event diff、SQL
  query budget、超 bind-limit reset、缩略图回滚、journal 恢复、跨类型 annotation、
  backfill 和切库迟到 worker。
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
