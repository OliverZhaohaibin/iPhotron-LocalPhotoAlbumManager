# 05 - 当前进度

> **版本:** 1.0 | **日期:** 2026-05-01  
> **状态:** 进行中  
> **范围:** vNext 重构进度与交接记录

---

## 1. 摘要

本文件记录当前 vNext 重构的真实落点。项目还没有完成全部六个阶段，
但运行时主路径已经建立起可执行的 session boundary、repository/state
boundary 和 architecture guardrail。

本轮新增完成的是 `21-maps-runtime-availability-session-migration.md`：
`MapRuntimePort` 不再只是协议名义存在，而是通过 `LibrarySession.maps`
绑定进 active session，并由 `RuntimeContext` / `LibraryManager` 暴露给 GUI。
`PlaybackCoordinator` 的 Assign Location capability、`PhotoMapView` 的地图
backend 选择、以及 `InfoLocationMapView` 的 mini-map backend 选择，现在都可以
消费同一份 maps runtime capability snapshot。

这一步有两个关键修正：

- mac 的 GL 上下文实现被明确保留为严格路径：
  `QOffscreenSurface` / `makeCurrent()` / `glGetString(GL_VERSION)` 任一步失败，
  Python GL 都视为 unavailable；但 native OsmAnd widget probe 与这条路径保持
  独立，不会被错误地一刀切禁用。
- 上一轮文档里“`tools/check_architecture.py` 已通过”的结论与代码现状一度不一致。
  本轮已移除 `PlaybackCoordinator` 对
  `iPhoto.bootstrap.library_people_service` 的直接 import，再次把 architecture
  gate 恢复到绿色，并同步修正文档表述。

## 2. 本轮完成：Maps Runtime Availability + Session 绑定

- 新增 session-owned maps runtime capability adapter。
  - `application/ports/runtime.py` 现在定义 `MapRuntimeCapabilities`，
    `MapRuntimePort` 不再只有 `is_available()`。
  - 新增 `infrastructure/services/map_runtime_service.py`，统一计算：
    `preferred_backend`、`python_gl_available`、`native_widget_available`、
    `osmand_extension_available`、`location_search_available` 与状态文案。
- `LibrarySession` / `RuntimeContext` / `LibraryManager` 绑定链路补齐。
  - `LibrarySession.maps` 创建并持有当前 maps runtime surface。
  - `RuntimeContext.open_library()` / `close_library()` 负责 bind/unbind。
  - `LibraryManager` 暴露 `map_runtime` property，供 GUI runtime 消费。
- GUI maps/runtime 入口收口到同一 capability snapshot。
  - `PlaybackCoordinator` 不再直接调用 `has_usable_osmand_search_extension()`；
    Assign Location capability 改为读取 bound `map_runtime`。
  - `PhotoMapView` 与 `InfoLocationMapView` 支持消费 injected `map_runtime`，
    同时保留原有本地探测作为 compatibility fallback，避免无 session 注入时
    直接改爆旧构造路径与既有 widget tests。
  - `InfoPanel` 与 `Ui_MainWindow` 增加 maps runtime forwarding，确保主地图页和
    mini-map 都能接入相同的 runtime snapshot。
- People residual 再补一个实际回归修复。
  - `PlaybackCoordinator.set_people_library_root()` 不再 import
    `create_people_service(...)` 作为 GUI runtime 主路径。
  - active runtime 下优先复用 `LibraryManager.people_service`；无 bound service
    时仅保留 plain `PeopleService(root)` 级别的最小兼容 fallback。

## 3. 历史已完成切片摘要

以下切片已经完成，详细过程性交接分别见 `06` 到 `20`：

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
  album-metadata/lifecycle/operation/People surface 已挂到 active session；
  GUI startup 仍有进一步收口空间。
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
  People、Thumbnail 与 Assign Location 边界已有明显收口；Maps 方面，
  `MapRuntimePort` 已经具备 session-bound capability surface，
  availability 查询与 native fallback 测试也已补齐，但
  `LocationTrashNavigationService` 仍是临时 GUI seam，widget 构造与最终
  event/query shape 仍未完全下沉。Edit sidecar port 仍待深入迁移。
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
- Maps 现在已有 session-bound capability surface，但 widget 构造仍在 GUI 层，
  `LocationTrashNavigationService` 也仍是临时 seam；Edit sidecar 仍缺少完整的
  session/runtime boundary 收口。
- `PeopleDashboardWidget.set_library_root()` 仍保留 asset-aware compatibility
  factory，以避免 group summary 失去 common-photo cover 而直接退化成拼图；
  `PlaybackCoordinator.set_people_library_root()`、`ManualFaceAddWorker` 与
  `PinnedItemsService` standalone 清理路径仍保留 compatibility fallback，
  但 `PlaybackCoordinator` 已不再以 bootstrap People factory 作为运行时主路径。
- temp-library 端到端仍保持 out of scope。

## 6. 最新验证

本轮在项目 `.venv` 下执行：

- `.venv/bin/python -m pytest tests/test_map_runtime_service.py tests/application/test_runtime_context.py tests/gui/coordinators/test_playback_coordinator.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/test_photo_map_view.py tests/test_ui_main_window_map_stack.py -q`
- `.venv/bin/python -m pytest tests/test_info_panel.py -k "location_map or set_location_capability" -q`
- `.venv/bin/python tools/check_architecture.py`

结果：

- 上述 focused regressions 全部通过（`80 passed` + `10 passed`）。
- `tools/check_architecture.py` 通过。
- 仍有既有的 pytest `Unknown config option: env` warning。
- 仍有既有的 legacy model shim / pairing deprecation warnings。
- 本轮新增验证意图：session-bound maps runtime binding、mac strict GL
  capability semantics、map widget native/python/legacy fallback、Assign
  Location capability routing、以及 `PlaybackCoordinator` People bootstrap
  import regression 的修复。

说明：

- `tests/test_info_panel.py` 的 map-related 子集已通过，但整文件在 headless Qt
  环境下仍存在与本轮改动无直接对应的 event-filter cleanup segfault 风险；
  因此本轮只把受影响的 mini-map / location capability 子集固定为验证锚点。

之前各个切片的针对性验证命令和结果，继续以 `06` 到 `20` 交接文档为准；
本文件只保留当前整体验证结论和最新增量验证。

## 7. 下一步交接

1. 推进 Phase 5 的 Edit：`.ipo` sidecar 读写、save/reset/export use case
   与 `EditSidecarPort` 的 session/runtime 收口。
2. 继续完善 Maps：若后续要继续下沉，应优先处理 widget 构造 / event-routing
   仍留在 GUI 层的问题，并逐步淡化 `LocationTrashNavigationService` 这个临时 seam。
3. 补 `temp library` 端到端回归：import / move / delete / restore，以及
   rescan 后用户状态保护。
