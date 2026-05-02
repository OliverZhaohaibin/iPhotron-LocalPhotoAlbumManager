# 05 - 当前进度

> **版本:** 1.0 | **日期:** 2026-05-02
> **状态:** 进行中
> **范围:** vNext 重构进度与交接记录

---

## 1. 摘要

本文件记录当前 vNext 重构的真实落点。项目还没有完成全部六个阶段，
但运行时主路径已经建立起可执行的 session boundary、repository/state
boundary 和 architecture guardrail。

本轮新增完成的是 `23-maps-location-session-residual-migration.md`：Location
地理资产查询与 Recently Deleted cleanup 已从 GUI/`LibraryManager` 临时入口
进一步收口到 active session surface。`LibrarySession.locations` /
`LibraryManager.location_service` 现在承接地图页 geotagged assets 读取、
scan-row 增量转换、缓存失效与 legacy `get_geotagged_assets()` 兼容代理。

这一步有三个关键落点：

- 新增 application-level `GeotaggedAsset` DTO、`LocationAssetServicePort`
  与 `LibraryLocationService`，地理资产聚合不再由 GUI 或 legacy
  `GeoAggregatorMixin` 作为主入口。
- `LocationTrashNavigationService` 只保留 Qt background transport、request
  serial、signals 与 cleanup throttle；地理资产加载优先走
  `location_service.list_geotagged_assets()`，trash cleanup 优先走
  `asset_lifecycle_service.cleanup_deleted_index(deleted_root)`。
- 新增 architecture guardrail，阻止 GUI/runtime 业务入口重新导入
  `iPhoto.library.geo_aggregator`。

## 2. 本轮完成：Maps Location Query + Session 绑定

- 新增 session-owned location surface。
  - `application/dtos.py` 现在定义 application-level `GeotaggedAsset`。
  - `application/ports/runtime.py` 现在定义 `LocationAssetServicePort`。
  - 新增 `bootstrap/library_location_service.py`，统一处理 geotagged rows
    读取、scan-row 转换、去重、排序与缓存失效。
- `LibrarySession` / `RuntimeContext` / `LibraryManager` 绑定链路补齐。
  - `LibrarySession.locations` 创建并持有当前 Location query surface。
  - `RuntimeContext.open_library()` / `close_library()` 负责 bind/unbind。
  - `LibraryManager` 暴露 `location_service` property，供 GUI transport 与
    legacy compatibility 入口消费。
- GUI/runtime 的 Location/Trash 入口进一步收口。
  - `GalleryViewModel` 的 scan chunk 增量转换不再导入
    `iPhoto.library.geo_aggregator`，优先走 bound `location_service`。
  - `LocationTrashNavigationService` 不再作为业务查询边界，只负责 Qt
    background task transport 与 request token。
  - `GeoAggregatorMixin.get_geotagged_assets()` 保留兼容 API，但 active
    session 下委托给 `LibraryLocationService`。

## 3. 历史已完成切片摘要

以下切片已经完成，详细过程性交接分别见 `06` 到 `22`：

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
- Album metadata session 化：
  已引入 `LibraryAlbumMetadataService` 与 album manifest repository port，
  album cover / featured / import-mark-featured durable 规则已从 GUI service
  下沉到 session-owned command surface。
- GUI 文件操作 command 化：
  move/delete/restore 的 durable planning 已迁入
  `LibraryAssetOperationService`，GUI service 只负责 prompt、worker、
  signal 和用户提示。

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
- Phase 2：部分完成。
  `global_index.db` 已成为 runtime asset source of truth，favorite 写入和
  gallery 查询已走 state/query boundary；更多 durable user state 仍待继续
  收口。
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
  `RuntimeContext` 术语层。
- Phase 5：部分完成。
  Edit 已完成；People、Thumbnail 与 Assign Location 边界已有明显收口；Maps
  方面，`MapRuntimePort` 已经具备 session-bound capability surface，
  availability 查询、native fallback、Location geotagged query 与 trash
  cleanup 测试也已补齐；`LocationTrashNavigationService` 仍保留为 Qt
  transport seam，widget 构造与 marker/event routing 仍未完全下沉。
- Phase 6：部分完成。
  architecture tests、targeted application/infrastructure tests 已存在；
  temp-library end-to-end 与性能 baseline 仍未完成。

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
- Maps 现在已有 session-bound capability 与 Location query surface，但 widget
  构造、marker interaction 与最终 event routing 仍在 GUI 层。
- Edit sidecar 迁移后仍保留两个显式 path-level 例外：
  `move_worker` 继续为了伴随物一起移动使用 sidecar path helper，
  `thumbnail_job` 继续为了 cache stamp 读取 sidecar mtime。
- `PeopleDashboardWidget.set_library_root()` 仍保留 asset-aware compatibility
  factory，以避免 group summary 失去 common-photo cover 而直接退化成拼图；
  `PlaybackCoordinator.set_people_library_root()`、`ManualFaceAddWorker` 与
  `PinnedItemsService` standalone 清理路径仍保留 compatibility fallback，
  但 `PlaybackCoordinator` 已不再以 bootstrap People factory 作为运行时主路径。
- temp-library 端到端仍保持 out of scope。

## 6. 最新验证

本轮在项目 `.venv` 下执行：

- `.venv/bin/python -m pytest tests/application/test_library_location_service.py tests/application/test_runtime_context.py tests/gui/viewmodels/test_gallery_viewmodel.py tests/gui/services/test_location_trash_navigation_service.py tests/test_navigation_coordinator_cluster_gallery.py tests/test_library_geotagged_assets.py tests/test_library_manager_cleanup.py tests/architecture/test_layer_boundaries.py -q`
- `.venv/bin/python tools/check_architecture.py`

结果：

- 上述 focused regressions 通过。
- `tools/check_architecture.py` 通过。
- 仍有既有的 pytest `Unknown config option: env` warning。
- 仍有既有的 legacy model shim / pairing deprecation warnings。
- 本轮新增验证意图：session-bound location binding、geotagged row conversion
  与缓存失效、Location/Trash GUI transport 优先走 session surface、
  legacy `get_geotagged_assets()` 兼容代理，以及 GUI
  `iPhoto.library.geo_aggregator` import guardrail。

之前各个切片的针对性验证命令和结果，继续以 `06` 到 `22` 交接文档为准；
本文件只保留当前整体验证结论和最新增量验证。

## 7. 下一步交接

1. 继续完善 Maps：若后续要继续下沉，应优先处理 widget 构造、marker
   interaction 与 event-routing 仍留在 GUI 层的问题。
2. 补 `temp library` 端到端回归：import / move / delete / restore，以及
   rescan 后用户状态保护。
