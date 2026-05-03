# 05 - 当前进度

> **版本:** 1.0 | **日期:** 2026-05-03
> **状态:** 进行中
> **范围:** vNext 重构进度与交接记录

---

## 1. 摘要

本文件记录当前 vNext 重构的真实落点。项目还没有完成全部六个阶段，
但运行时主路径已经建立起可执行的 session boundary、repository/state
boundary 和 architecture guardrail。

本轮新增完成的是 `28-performance-baseline.md`：Phase 6 的 scan、gallery
pagination、thumbnail cache baseline 已补齐，历史不可运行 benchmark 入口已清理。

这一步有四个关键落点：

- 删除 `tools/benchmarks/benchmark_refactor.py`，不再保留旧 benchmark 兼容入口。
- `tools/testbase/` 已加入 `.gitignore`，真实素材集只作为本地可选输入。
- 新增 `tests/performance/test_refactor_performance_baseline.py`，覆盖 scan merge、
  gallery cursor pagination 与 thumbnail cache hit baseline。
- Phase 6 清单中的三项性能 baseline 已改为可执行回归。

## 2. 本轮完成：Performance Baseline

- 清理历史 benchmark。
  - `tools/benchmarks/benchmark_refactor.py` 已删除。
  - 当前不再把 `tools/benchmarks/` 作为 Phase 6 性能入口。
- 本地测试集隔离。
  - `tools/testbase/` 已加入 `.gitignore`。
  - CI 和普通 pytest 不依赖真实 HEIC/MOV 素材存在。
- 新增 performance regression sanity checks。
  - scan baseline 通过 `LibraryScanService`、synthetic scanner 与
    `global_index.db` merge path 覆盖 scan orchestration。
  - gallery baseline 覆盖 `get_assets_page()` cursor pagination。
  - thumbnail baseline 覆盖 L2 hit 回填 L1 后的 cache-hit 路径，确认不触发
    generator。

## 3. 历史已完成切片摘要

以下切片已经完成，详细过程性交接分别见 `06` 到 `27`：

- 基础边界与 session 基础：
  已引入 `application/ports/*`、`LibrarySession`、state repository adapter、
  `RuntimeContext` session binding、Assign Location adapter、thumbnail/core
  边界清理，以及可执行 architecture checks。
- 扫描与生命周期主线：
  已引入 `LibraryScanService`、`ScanLibraryUseCase`、session-owned scan entry、
  move/delete/restore lifecycle service、Recently Deleted cleanup、watcher scan
  refresh、显式 stale-row reconciliation。
- 查询与 source-of-truth：
  已引入 `LibraryAssetQueryService`，GUI gallery/grid/export/map/album dashboard
  读取改走 session query surface；`global_index.db` 明确为当前 runtime
  asset source of truth。
- People session 化：
  已引入 `PeopleIndexPort` 与 library-scoped People service，GUI 关键
  People 流程改为优先走 session-bound service。
- People GUI residual 收口：
  已引入 `people_service_resolver`，GUI runtime 的 pinned/query/cover/
  snapshot follow-up 入口不再直接重建 bootstrap People factory。
- Maps runtime session 化：
  已引入 session-bound `map_runtime` surface，地图 backend availability、
  native fallback 与 Assign Location capability 不再散落在多个 GUI 入口各自探测。
- Edit sidecar session 化：
  已引入 `LibraryEditService`、filesystem `EditSidecarPort` adapter、
  session-bound `edit_service` surface，以及统一的视频 edit/render state
  判定入口。
- Maps Location query session 化：
  已引入 `LibraryLocationService`、`LocationAssetServicePort` 与
  session-bound `location_service` surface，地理资产查询和 trash cleanup
  不再以 GUI transport/legacy manager 方法作为主业务入口。
- Maps widget/interaction session 化：
  已引入 `MapInteractionServicePort`、`LibraryMapInteractionService` 与
  session-bound `map_interaction_service` surface；marker routing 不再由
  `MarkerController` 直接决定，full map / mini map widget backend 选择收口到
  共享 GUI factory。
- Maps GUI transport/overlay residual 收口：
  已引入共享 `MapEventSurfaceBridge` / `MapOverlayAttachment` helper，
  `PhotoMapView` / `InfoLocationMapView` 不再重复维护 event target、
  post-render painter 与 QWidget overlay fallback，marker pointer-hit 入口
  也不再由 view 本身拼装。
- Album metadata session 化：
  已引入 `LibraryAlbumMetadataService` 与 album manifest repository port，
  album cover / featured / import-mark-featured durable 规则已从 GUI service
  下沉到 session-owned command surface。
- GUI 文件操作 command 化：
  move/delete/restore 的 durable planning 已迁入
  `LibraryAssetOperationService`，GUI service 只负责 prompt、worker、
  signal 和用户提示。
- Temp-library 端到端回归：
  `LibrarySession + workers` 主链路现在已有真实临时库级别回归，覆盖 import /
  move / delete / restore / rescan，以及当前已稳定的 `favorite + trash`
  用户状态保护。
- Durable user state residual 收口：
  People hidden / person order / group order 已通过 session-level 回归锁定；
  pinned sidebar 状态规则已迁入 application service，GUI 只保留 Qt transport。
- Performance baseline：
  历史不可运行 benchmark 入口已清理；scan、gallery pagination、thumbnail cache
  已有 `tests/performance` 小数据 baseline，真实素材集只作为本地可选输入。

## 4. 当前阶段状态

- Phase 0：部分完成。
  vNext 文档与 guardrail 已落地，当前 guardrail 已覆盖 application/concrete
  imports、lower-layer GUI imports、GUI concrete index-store imports、legacy
  model shim imports、legacy domain use-case imports、GUI runtime `iPhoto.app`
  imports 等回归方向；仍保留少量明确 allowlist。
- Phase 1：部分完成。
  `LibrarySession` 已建立并接入 `RuntimeContext`，scan/query/state/
  album-metadata/lifecycle/operation/People/Maps/Edit/Location surface 已挂到
  active session；GUI startup 仍有进一步收口空间。
- Phase 2：已完成当前 residual。
  `global_index.db` 已成为 runtime asset source of truth，favorite 写入和
  gallery 查询已走 state/query boundary；trash lifecycle 已走 session lifecycle
  surface；People hidden/order 由 `FaceStateRepository` 持久化并有 session-level
  回归；pinned sidebar 规则已通过 application-level state service 收口。
- Phase 3：已完成。
  GUI、CLI、watcher、`app.py` compatibility wrapper、restore/import follow-up、
  以及本轮的 GUI `open/rescan/pair` 入口都已收口到
  `LibraryScanService` / `ScanLibraryUseCase` 主线；普通 scan 中的 delete/prune
  也已分离为显式 lifecycle reconciliation。
- Phase 4：部分完成。
  `AppFacade` 的 open/rescan/pair routing、move/delete/restore planning、
  gallery query reads、media-load-failure 修复、album metadata durable
  mutation 都已迁到 session service；但 `gui/services/*` 和
  `BackgroundTaskManager` 仍未完全瘦身成纯 presentation transport。
  本轮补齐 `LibraryUpdate` + `Location/Trash` GUI residual：
  `LibraryUpdateService` 的 scan worker ownership 迁入 GUI 任务运行器，
  durable scan finalize 迁到 runtime/library surface；GUI scan update flows
  通过 runtime scan finalize hook 处理 snapshot 持久化、Recently Deleted
  保留字段、stale-row reconciliation 与 Live Photo pairing follow-up；
  `NavigationCoordinator` 不再负责 Recently Deleted cleanup throttle 或后台
  线程调度，`GalleryViewModel` 不再直接准备 deleted roots 或读取
  geotagged assets，统一改走 Location/Trash transport adapter；
  本轮继续补齐 People residual：`MainCoordinator`、`NavigationCoordinator`、
  `GalleryViewModel`、`AlbumTreeModel`、`ContextMenuController` 不再在
  GUI runtime 中重建 bootstrap People factory，而是统一优先依赖
  `LibraryManager.people_service`；`PlaybackCoordinator` 也已移除 GUI runtime
  的 bootstrap People factory import；仍沿用 `LibraryManager` + bootstrap
  runtime surfaces 作为事实边界，未强制引入新的 `LibrarySession` /
  `RuntimeContext` 术语层。本轮继续将 `PinnedItemsService` 瘦身为 Qt transport
  wrapper，pinned 状态规则不再由 GUI service 直接拥有。
- Phase 5：部分完成。
  Edit 已完成；People、Thumbnail 与 Assign Location 边界已有明显收口；Maps
  方面，`MapRuntimePort` 已经具备 session-bound capability surface，
  availability 查询、native fallback、Location geotagged query、trash
  cleanup、marker interaction surface 与 map widget factory 测试也已补齐；
  本轮继续把 `PhotoMapView` / `InfoLocationMapView` 里重复的 event target、
  overlay attachment 与 painter teardown 提炼到共享 GUI helper，marker
  pointer-hit 入口也已收回 controller seam；`LocationTrashNavigationService`
  仍保留为 Qt transport seam，overlay/pin 绘制与 drag cursor 策略仍在 GUI 层。
- Phase 6：部分完成。
  architecture tests、targeted application/infrastructure tests 与
  temp-library end-to-end 已存在；scan、gallery pagination、thumbnail cache
  baseline 已补齐。性能 baseline 是 regression sanity check，不代表跨机器
  绝对性能认证。

## 5. 已知迁移例外

- `app.py`、`appctx.py`、`gui.facade.py`、`library.manager.py` 仍是兼容入口，
  但不应继续承载新的 durable business logic。
- `application/services/album_service.py` 及旧
  `application/use_cases/*` 仍保留给 compatibility/test 路径，不应再被新的
  runtime 代码导入。
- `io/scanner_adapter.py` 仍是扫描迁移过程中的 allowlisted bridge，
  当前继续复用 legacy `FileDiscoveryThread`。
- `SQLiteAssetRepository` 仍保留给 legacy/domain 测试适配器；当前 library
  scoped runtime repository 已不再依赖它。
- user state 目前仍物理存放在 `global_index.db`；`LibraryStateRepositoryPort`
  提供的是 API boundary，而不是独立 `library_state.db`。
- `global_index.db` 兼容 schema 可能缺失 `metadata` 列；state adapter 保持
  best-effort 行为。
- Maps 现在已有 session-bound capability、Location query 与 marker
  interaction surface；concrete widget 构造选择已集中到 GUI factory，event
  target / overlay attachment 也已集中到共享 GUI helper，但 overlay/pin 绘制
  与 drag cursor 策略仍在 GUI 层。
- Edit sidecar 迁移后仍保留两个显式 path-level 例外：
  `move_worker` 继续为了伴随物一起移动使用 sidecar path helper，
  `thumbnail_job` 继续为了 cache stamp 读取 sidecar mtime。
- `PeopleDashboardWidget.set_library_root()` 仍保留 asset-aware compatibility
  factory，以避免 group summary 失去 common-photo cover 而直接退化成拼图；
  `PlaybackCoordinator.set_people_library_root()`、`ManualFaceAddWorker` 与
  `PinnedItemsService` standalone 清理路径仍保留 compatibility fallback，
  但 `PlaybackCoordinator` 已不再以 bootstrap People factory 作为运行时主路径。
- pinned sidebar 状态物理存储仍在 settings payload；`PinnedStateRepositoryPort`
  提供的是 application boundary，不代表迁移到独立数据库。
- `tools/testbase/` 是本地真实素材集，已被 `.gitignore` 忽略；CI baseline 使用
  合成数据，不依赖该目录。

## 6. 最新验证

本轮在项目 `.venv` 下执行：

- `.venv/bin/python -m pytest tests/application/test_temp_library_end_to_end.py tests/application/test_library_scan_service.py tests/application/test_library_asset_lifecycle_service.py tests/services/test_asset_move_service.py tests/services/test_restoration_service.py tests/ui/tasks/test_import_worker.py -q`
- `.venv/bin/python -m pytest tests/application/test_pinned_state_service.py tests/application/test_library_people_service.py tests/test_settings_manager.py -q`
- `.venv/bin/python -m pytest tests/application/test_temp_library_end_to_end.py tests/application/test_library_people_service.py tests/test_people_repository.py tests/test_settings_manager.py tests/gui/widgets/test_people_dashboard_widget.py tests/test_album_sidebar.py tests/test_album_tree_model.py tests/ui/test_albums_dashboard.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py -q`
- `.venv/bin/python -m pytest tests/performance -q`
- `.venv/bin/python tools/check_architecture.py`

结果：

- 上述 focused regressions 通过（`47 passed`）。
- 新增 durable user state focused regressions 通过（`17 passed`）。
- 计划内聚合 focused regressions 通过（`112 passed`）。
- 新增 performance baseline tests 通过（`3 passed`）。
- `tools/check_architecture.py` 通过。
- 仍有既有的 pytest `Unknown config option: env` warning。
- 仍有既有的 legacy model shim / pairing deprecation warnings。
- 本轮新增验证意图：scan merge、gallery cursor pagination、thumbnail cache hit
  路径具备可执行 baseline。

之前各个切片的针对性验证命令和结果，继续以 `06` 到 `27` 交接文档为准；
本文件只保留当前整体验证结论和最新增量验证。

## 7. 下一步交接

1. 若需要更严格的真实素材性能追踪，新增当前架构下可运行的 benchmark CLI，
   不恢复旧 `tools/benchmarks/benchmark_refactor.py`。
2. 若后续再回到 Maps，只处理新暴露问题；当前不再主动扩新的
   session/runtime boundary。
