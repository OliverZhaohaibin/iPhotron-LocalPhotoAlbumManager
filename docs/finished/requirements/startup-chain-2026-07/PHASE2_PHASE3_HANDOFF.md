# 启动链路第二、第三阶段交接文档

> **状态：2026-07 历史 handoff，已被取代。** 当前三平台 native surface
> hierarchy 与 first-paint lifecycle 以
> [`docs/architecture.md`](../../../architecture.md) 为准；下文只用于解释当时的
> 阶段决策。

更新日期：2026-07-13

> 本文保留为阶段性历史记录。`ENGINEERING_CLOSURE_REPORT.md` 是当时的
> closure；当前验收以
> [`STARTUP_MANUAL_VALIDATION_MATRIX.md`](../../../requirements/STARTUP_MANUAL_VALIDATION_MATRIX.md) 为准；
> 下文“剩余改造”中的多项工作已在 2026-07-14 完成。
> 2026-07-22 合并前复审与修复结果见历史文件 `NEXT_DEVELOPMENT_HANDOFF.md`。

## 1. 交接结论

第二、第三阶段都已经完成了核心代码改造，但尚未达到“所有平台与异常介质均完成生产验收”的关闭条件。

- 第二阶段当前状态：**核心链路、迁移恢复协议和 probe P0 加固已落地，慢存储策略和 packaged 验证待闭环**。
- 第三阶段当前状态：**主要重对象已懒加载，GUI job 时间片观测层和 100 ms 超预算诊断已落地，热点拆分与跨平台性能门禁待闭环**。
- 第一阶段状态机、首帧 watchdog、输入释放、取消与异常降级已经完成，可作为后续工作的稳定基线。

因此，后续不应重新设计启动架构；应沿用现有接口，根据 job 观测结果拆分真实热点并补齐平台证据。

## 2. 已完成的基础能力

### 2.1 第一阶段基线

- `StartupOrchestrator` 已提供显式启动阶段、generation、首帧/watchdog 竞争消歧、取消、降级和完成信号。
- 首帧信号缺失时，约 1.8 秒 watchdog 仍会释放输入并继续最小初始化。
- 启动回调异常不会再直接逃逸到 Qt event loop；窗口关闭会取消 orchestrator 和正在进行的库探测。
- 启动完成后会取消 `faulthandler.dump_traceback_later()`。
- 主窗口已有非模态恢复面板，支持重试、暂不打开库和查看诊断。

主要入口：

- `src/iPhoto/bootstrap/startup_orchestrator.py`
- `src/iPhoto/gui/main.py`
- `src/iPhoto/gui/ui/main_window.py`

### 2.2 第二阶段已完成部分

- 已增加 `LibraryProbeController`，使用 `QProcess` 隔离路径解析、目录快照和 SQLite schema 准备。
- 默认探测超时为 3 秒；超时会终止 helper，GUI 不等待慢盘或离线 NAS。
- 请求和结果带 `request_id`，迟到结果不会覆盖当前会话。
- 已增加不可变 `PreparedLibrary`、`PreparedAlbum`、`LibraryProbeRequest` 和 `LibraryProbeFailure`。
- `RuntimeContext` 已拆出 `request_startup_library_probe()`、`commit_prepared_library()`、`open_initial_collection()`、`schedule_idle_startup_jobs()`。
- `LibrarySession` 可以从 `PreparedLibrary` 构造，主线程提交时不再重复遍历 album tree。
- SQLite 使用 `PRAGMA user_version`；历史全表修复只在版本升级时执行，当前版本重开不再重复执行全表 `UPDATE`。
- 无库状态使用 `UnboundAssetRepository`，不再创建 `~/.iPhoto/global_index.db`。
- helper 返回的两层 album 快照可直接发布；被判定为 slow/network 的库不会在启动提交阶段注册文件 watcher。

主要入口：

- `src/iPhoto/bootstrap/library_probe.py`
- `src/iPhoto/bootstrap/runtime_context.py`
- `src/iPhoto/bootstrap/library_session.py`
- `src/iPhoto/cache/index_store/migrations.py`
- `src/iPhoto/cache/index_store/repository.py`
- `src/iPhoto/infrastructure/services/library_asset_runtime.py`
- `src/iPhoto/library/runtime_controller.py`

### 2.3 第三阶段已完成部分

- QApplication 创建前使用轻量 bootstrap settings 读取；完整校验延迟到应用创建后。
- 损坏 settings 会备份为带时间戳的 `corrupt` 文件并使用默认值继续启动。
- shader cache 固定使用本机用户缓存路径，不再为启动缓存探测保存库。
- Linux 不再为可选地图强制全局 XCB；Wayland 下原生地图不兼容时降级。
- Preview、People/Recognition、InfoPanel/Location、EditCoordinator、地图能力探测、部分 facade 服务及图像/地理编码重依赖已经改为首次使用时创建或导入。
- 地图扩展提示已经改为非模态。
- 已增加 `GuiStartupJobQueue`：每个 event-loop tick 最多执行一个带名称、generation、前置条件和 100 ms 预算的 GUI 启动 job。
- Detail post-show 创建、`DesktopCoordinatorRuntime` 构造/启动、library probe、prepared commit、初始图库选择和 idle startup jobs 已进入统一队列；Windows/Linux 的 pre-show Detail 顺序保持不变并增加同步耗时观测。
- job 异常会取消当前 generation 并进入现有降级协议；关闭、失败、降级和重试会拒绝旧 generation 及 probe 迟到结果，重试不会重复构造 coordinator。
- startup JSONL 已增加 `startup.gui_job.started`、`startup.gui_job.finished` 和超预算 `startup.gui_stall`，字段不包含用户库路径。
- 当前本地冷导入测量中，`iPhoto.gui.main` 累计导入时间相对改造前约下降 74%。这些数字只用于定位趋势，不能替代 packaged P50/P95。

主要入口：

- `src/iPhoto/bootstrap/bootstrap_settings.py`
- `src/iPhoto/bootstrap/gui_startup_job_queue.py`
- `src/iPhoto/bootstrap/qt_shader_cache.py`
- `src/iPhoto/gui/coordinators/desktop_coordinator_runtime.py`
- `src/iPhoto/gui/facade.py`
- `src/iPhoto/gui/ui/ui_main_window.py`
- `src/iPhoto/infrastructure/services/map_runtime_service.py`
- `src/maps/map_sources.py`

### 2.4 Coordinator 领域拆分（已完成）

- 巨型 `MainCoordinator` 类型及兼容 alias 已删除；对外组合根为 `DesktopCoordinatorRuntime`，只暴露 lifecycle 和 `gallery`、`detail` 两个明确入口。
- `GalleryCoordinator` 承担启动模型、目录打开、选中路径解析和 Gallery library rebind；`DetailCoordinator` 实现 `DetailNavigationPort`、窗口沉浸接口，以及 Recognition/Location 使用的窄 Detail port。
- `RecognitionCoordinator` 和 `LocationInfoCoordinator` 按首次使用创建。核心 runtime 导入不会加载 manual-face worker、Info metadata worker、LocationSearchController、OsmAnd search、Shapely 或 mapbox vector tile。
- Recognition 创建时重新解析当前 `LibrarySession` 的 People/Pet 服务；Location/Info 创建时才创建 `LocationFileWriteQueue`，并注入地点搜索、assignment repository/service 和 metadata worker。
- `NavigationCoordinator` 只依赖 `DetailNavigationPort`；`MainWindow.bind_coordinators(lifecycle, gallery, detail)` 分别处理关闭、目录/选中项和沉浸 Detail；`FramelessWindowManager.set_detail_coordinator()` 不再接受通用 controller。
- library tree/commit 更新先通知 Gallery 和 Detail，只在 Recognition、Location/Info 已创建时通知其 `rebind_library()`；可选领域首次创建时直接读取最新 session。
- 关闭保持幂等：先 drain Location 写队列，再关闭 Playback/Edit/Info、Gallery UI worker、library/asset runtime、event bus，最后等待全局线程池。
- Window theme 首次同步移到 runtime `start()`；`WindowManager` 检测相同的全局 menu stylesheet，避免重复 `QApplication.setStyleSheet()` 导致整棵 widget tree repolish。

新增核心入口：

- `src/iPhoto/gui/coordinators/contracts.py`
- `src/iPhoto/gui/coordinators/gallery_coordinator.py`
- `src/iPhoto/gui/coordinators/detail_coordinator.py`
- `src/iPhoto/gui/coordinators/recognition_coordinator.py`
- `src/iPhoto/gui/coordinators/location_info_coordinator.py`
- `src/iPhoto/gui/coordinators/desktop_coordinator_runtime.py`

## 3. 第二阶段剩余改造

### P0：数据库迁移的可恢复事务协议（已完成）

已实现原子迁移状态、SQLite online backup、逐版本事务、遗留状态检测、完整性校验和已验证备份自动恢复。当前 schema 的常规重开不会执行全库完整性扫描。

实现内容：

1. 在 work 目录记录迁移状态，至少包含源版本、目标版本、开始时间和唯一迁移 ID。
2. 迁移前执行可恢复备份或 SQLite online backup；不要直接复制一个仍在 WAL 模式写入的数据库文件。
3. 每个迁移版本在单独事务内完成，成功提交后才提升 `user_version`。
4. helper 启动时检测遗留迁移状态，执行完整性检查并选择继续、回滚备份或返回可恢复失败。
5. 把 `busy/locked`、`corrupt/not a database`、只读和磁盘空间不足映射为稳定的错误码，恢复面板按错误码展示动作。

验收：迁移中 kill helper 后再次启动，不冻结、不丢失原库，可明确重试或从备份恢复。

### P0：probe 协议健壮性（已完成）

已完成协议版本、结构化失败、stdout/stderr/album 上限、脱敏诊断、独立进程错误分类、异步 terminate→kill、规范化 root 与数据库归属校验。

- 协议版本和应用 schema 版本兼容检查。
- stdout 最大长度与 album 数量上限，防止异常目录造成无界内存占用。
- stderr 截断和脱敏，诊断日志不得写入完整用户路径。
- helper `FailedToStart`、`Crashed`、启动超时、协议损坏和非零退出码的独立错误分类。
- 超时后先 `terminate`，短暂等待后再 `kill`；Windows packaged 下验证不会遗留子进程。
- 校验返回的规范化 root 与请求目标属于同一库，不能只依赖 `request_id`。

### P0：异常存储自动化测试（核心自动化已完成）

已增加迁移事务回滚、真实子进程中断后恢复、损坏主库/备份、future schema、busy/locked、只读和磁盘满映射、helper 超时/崩溃、错误 JSON、输出截断与超限、断开符号链接、迟到结果和真实 QProcess 往返测试。

仍需在 packaged 平台矩阵验证权限拒绝、被拔出的移动盘、真实离线 NAS、切库/关窗竞态和 Windows 子进程清理。

真实 NAS/移动盘无法完全由单元测试替代，应另保留 packaged 手工矩阵。

### P1：慢盘识别与刷新策略

当前 `storage_kind` 主要依据 Windows UNC 或单次探测耗时，仍比较粗糙。建议：

- Windows 使用 drive type，macOS/Linux 使用 mount 信息补充 removable/network 判断；平台 API 失败时回退到耗时启发式。
- watcher 注册也放入可取消的后台批次，不能假设“本地盘注册 watcher 永远很快”。
- slow/network 库增加低频后台轮询或明确的手动刷新状态；轮询必须带 generation，切库后立即失效。
- album snapshot 设置数量和时间预算；超过预算返回部分结果和 warning，图库仍可进入可交互状态。

### P1：数据库打开生命周期收口

目前 prepared-root 标记避免了常规 UI 提交路径重复迁移，但还应审计所有 repository 构造入口：

- 禁止未经过 prepare 的保存库在 GUI 线程隐式初始化 schema。
- prepared 凭证至少包含规范化 root、database path、schema version 和探测生成时间。
- commit 前检查凭证仍匹配文件身份；若数据库在 probe 后被替换，应重新 probe。
- 为 CLI、测试工具和非 GUI 调用保留显式同步 `prepare` API，不要恢复构造函数隐式 I/O。

## 4. 第三阶段剩余改造

### P0：GUI event-loop 时间片门禁（观测层已完成，性能闭环待完成）

统一调度和超预算观测已经完成：

- `GuiStartupJobQueue` 使用不可变 job 描述名称、generation、回调、前置条件和目标预算。
- 每次 event-loop tick 最多执行一个 job；当前 job 完成后才在下一 tick 调度后续 job。
- job 使用单调时钟计时；超过 100 ms 时记录唯一 `startup.gui_stall` 并输出 warning，生产运行不会因此强制失败。
- job 的 started/finished 事件统一记录 job、generation、duration、budget、over-budget、线程和结果状态。
- 窗口关闭、启动失败、进入降级状态或重试时会取消过期 generation；probe ready 必须同时匹配 request ID 和 generation 才能重新入队 commit。
- 回调异常只上报一次并停止当前 generation 的剩余 job，仍由 `StartupOrchestrator` 和非模态恢复面板负责用户可见降级。

自动化已覆盖单 tick 单 job、严格顺序、前置条件、100 ms 阈值、stall 唯一性、异常、关闭、旧 generation、回调中追加 job，以及主启动链的跨平台 Detail 顺序、probe ready→commit、gallery warmup 和 deferred scan 竞争。

Coordinator 装配已完成本轮闭环。后续工作不是继续增加 `singleShot(0, ...)`，而是采集 packaged 样本，并优先剖析当前剩余的 `feature.detail.post_show` 长任务、初始 collection 查询、首批 model publish 和缩略图请求提交。

### P0：packaged 性能基线与门禁

必须在打包产物上采集，而不是只测源码解释器导入：

- Windows：Defender 开启，冷启动/热启动，本地 SSD、离线移动盘、模拟高延迟共享盘。
- Linux：XCB、Wayland、无 XWayland，至少覆盖 AppImage 或 Flatpak 的实际交付格式。
- macOS：Intel/Apple Silicon，Metal 默认路径和 OpenGL 兼容路径。

每组至少记录：

- `process_start -> app_created`
- `show -> interactive`
- `interactive -> library_ready`
- `library_ready -> first_gallery_visible`
- `first_gallery_visible -> first_usable_thumbnail`
- GUI event-loop 最大停顿、probe 耗时和降级原因
- P50/P95，以及冷/热样本数

门禁：`show()` 后 2 秒内进入 interactive 或 degraded；interactive 后单次 GUI 停顿不超过 100 ms；本地已有索引的首批图库/缩略图 P50 相对旧 packaged baseline 改善至少 30%，P95 不退化。

### P1：Coordinator 领域拆分与首用成本（已完成）

`MainCoordinator` 已替换为领域组合根和明确 ports；Gallery/Detail 保持启动期创建，Recognition、Location/Info 和 Edit 延迟到首次使用。稳定 source-smoke 的 `coordinator.construct` 已低于 100 ms，因此本项不再是下一轮 P1。

仍需遵守的边界：Playback/Detail 为 Windows/Linux 原生宿主保留最小稳定对象；不得把 Recognition/Location 重依赖重新引入核心 import path；只有 profiler 证明处于关键路径时才继续拆 Gallery/Detail 内部对象。

下一真实热点转为 `feature.detail.post_show`。历史交接样本曾约为 365 ms；本轮最终 offscreen source-smoke 的两个连续稳定样本为 165.0 ms 和 162.2 ms，仍超过 100 ms，需继续拆 Detail QWidget 创建/样式与原生宿主准备。

### Source-smoke 对比（仅用于定位，不是 packaged 证据）

同一 source/offscreen 场景、相同 startup JSONL 协议下：

- 重构前 `coordinator.construct` 三次定位样本为 342.3 ms、303.6 ms、315.9 ms。
- 重构后，源码刚改写且 `.pyc`/文件缓存未稳定的首个样本为 164.7 ms；随后相同命令的连续样本为 63.9 ms、61.5 ms。
- 重构后 `coordinator.start` 对应为 23.6 ms、22.9 ms；Gallery 和 Detail/Playback 的领域构造均为低个位数毫秒，稳定样本中的 runtime shell 装配约 31 ms。
- `feature.detail.post_show` 最终连续样本仍为 165.0 ms、162.2 ms；另一个源码冷波动样本为 291.3 ms。

这些数字来自 source、offscreen、未受控缓存环境，只能说明热点转移和验证 100 ms 目标的开发趋势。packaged P50/P95、冷/热缓存控制和真实图形后端仍必须按 runbook 独立验收；原始 JSONL 继续留在忽略的 `benchmark-output/` 或临时目录，不提交机器/用户路径。

### P1：地图和可选能力的真实降级验证

- Wayland 且无 XWayland 时，主程序必须启动，地图入口应显示可恢复的不可用状态。
- XCB/GLX 可用时仍能启用原生地图；native helper 崩溃不得结束主进程。
- capability 探测只能在首次访问地图或首屏空闲后发生。
- 扩展提示不得抢焦点或阻塞输入 guard。

### P2：启动日志与隐私收口

现有阶段事件已写入 startup profile，但应补齐并统一字段：

- 阶段开始/结束和持续时间。
- 当前线程、Qt 平台后端、probe 错误码、storage kind、首次图库和首个可用缩略图时间。
- 终止事件必须恰好一个：`startup.completed`、`startup.degraded`、`startup.failed` 或 `startup.cancelled`。
- 路径只写稳定的脱敏 ID；不得在 JSONL 中记录完整库路径。

## 5. 推荐接手顺序

1. probe/SQLite 故障协议和中断恢复测试已完成，作为后续工作的稳定基线。
2. Coordinator 领域拆分和 `coordinator.construct < 100 ms` 的稳定 source-smoke 已完成；保持新增 import/port 门禁。
3. 下一步直接剖析并拆分 `feature.detail.post_show`，重点是 Detail QWidget 树、样式 repolish 和原生宿主准备，不再继续扩张 runtime 门面。
4. 建立 packaged 平台基线，逐平台修复 P95 和降级路径。
5. 最后收口日志字段、隐私检查和交付文档；所有门禁通过后再把本目录移动到 `docs/finished/requirements`。

Coordinator 大范围重构已结束；后续 Detail 热点优化应保持 library/probe 协议不变，避免重新扩大竞态定位面。

## 6. 测试与验证命令

当前改造完成时已验证：

- 全量测试：2612 passed，11 skipped。
- 架构/import 门禁：30 passed。
- GUI 启动队列与主链定向测试：36 passed。
- `python tools/check_architecture.py`：通过。
- `.venv/bin/python -m compileall -q src tests`：通过。
- `git diff --check`：通过。
- 使用临时 profile 输出验证：关键 job 均生成成对 started/finished JSONL 记录，新增 coordinator domain 记录不包含库路径；稳定 source-smoke 中 coordinator construct/start 均低于 100 ms。

接手后至少运行：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q tests/gui/test_gui_startup_job_queue.py tests/gui/test_main.py tests/gui/test_startup_orchestrator.py
.venv/bin/python -m pytest -q tests/application/test_library_probe.py tests/application/test_bootstrap_settings.py
.venv/bin/python -m pytest -q tests/architecture
.venv/bin/python tools/check_architecture.py
.venv/bin/python -m compileall -q src
git diff --check
```

新增平台性能工具时，应把原始 JSONL 和聚合报告放在构建产物目录或专用 benchmark 输出目录，不要提交包含用户真实路径的日志。

## 7. 完成定义

只有同时满足以下条件，第二、第三阶段才可标记完成：

- 所有 probe、迁移、关闭窗口和切库竞态都有自动化测试。
- 离线 NAS、拔出移动盘、SQLite lock/corrupt 下 GUI 可响应且约 3 秒进入可重试状态。
- 中断迁移可以明确恢复，不会静默使用半迁移数据库。
- Windows 只有一个稳定顶层窗口；Linux Wayland/XCB 均可启动；macOS 保持 Metal 默认路径。
- packaged P50/P95 达到性能门禁，且 interactive 后没有超过 100 ms 的未解释 GUI 长任务。
- 每次启动都有唯一终止诊断事件，输入 guard、spinner、helper 和 timer 均无泄漏。

在这些条件完成前，当前实现可以进入下一轮集成测试，但不应宣称第二、第三阶段已经完全关闭。
