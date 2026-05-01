# 05 - 当前进度

> **版本:** 1.0 | **日期:** 2026-05-01  
> **状态:** 进行中  
> **范围:** vNext 重构进度与交接记录

---

## 1. 摘要

本文件记录当前 vNext 重构的真实落点。项目还没有完成全部六个阶段，
但运行时主路径已经建立起可执行的 session boundary、repository/state
boundary 和 architecture guardrail。

本轮新增完成的是 `20-people-gui-session-residual-migration.md`：GUI runtime
People 入口不再在 coordinator/viewmodel/controller 中直接重建
`bootstrap.library_people_service` factory，而是统一优先走 active session
绑定的 `people_service`；兼容 fallback 收窄到非运行时路径，其中
`PeopleDashboardWidget` 继续保留 asset-aware factory 以维持 group
common-photo cover，`PlaybackCoordinator`、`ManualFaceAddWorker` 与
`PinnedItemsService` standalone 路径只保留最小兼容构造。

本轮同时补齐 People 侧的 architecture guardrail：coordinator/viewmodel/
controller/model/service 这些 GUI runtime 入口若重新 import
`iPhoto.bootstrap.library_people_service`，现在会被
`check_layer_boundaries` 拦截，避免 Phase 4 的 GUI residual 再次回流到
bootstrap factory。

## 2. 本轮完成：People GUI Residual 收口

- 新增 `gui/services/people_service_resolver.py`。
  - 统一从 `LibraryManager.people_service` 解析当前 active 的 session-bound
    People service，并按 root 做匹配。
  - GUI runtime 默认不再自己 fallback 到 bootstrap factory。
- 收口 People GUI runtime 调用点。
  - `MainCoordinator`、`NavigationCoordinator`、`GalleryViewModel`、
    `AlbumTreeModel`、`ContextMenuController` 现在统一优先走 bound
    `people_service`。
  - pinned person/group 打开、People snapshot 后 query 重建、sidebar pinned
    label 解析、cluster/group cover 操作，都不再从 GUI 直接装配 People
    runtime。
- `PinnedItemsService` 改为支持注入 People service getter。
  - 在 active runtime 下，stale people/group pin 清理优先走 bound session
    service。
  - 无 runtime getter 的 standalone/settings 路径，继续允许 plain
    `PeopleService(root)` 作为兼容读取。
- 保留但收窄 compatibility fallback。
  - `PeopleDashboardWidget.set_library_root()` 继续通过 asset-aware factory
    维持 group common-photo cover，不参与运行时主路径装配。
  - `PlaybackCoordinator.set_people_library_root()` 与
    `ManualFaceAddWorker` 仅保留最小兼容 fallback。
- 架构检查新增 guardrail。
  - coordinator/viewmodel/controller/model/service 这些 GUI runtime 入口不得再 import
    `iPhoto.bootstrap.library_people_service`。

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
  `LibraryManager.people_service`；仍沿用 `LibraryManager` + bootstrap
  runtime surfaces 作为事实边界，未强制引入新的 `LibrarySession` /
  `RuntimeContext` 术语层。
- Phase 5：部分完成。
  People、Thumbnail、Assign Location 边界已有明显收口；Maps runtime
  availability/fallback 与 Edit sidecar port 仍待深入迁移。本轮仅通过
  清理 GUI 侧 Location 入口为后续 Maps runtime 提取收窄边界，不应仅凭
  本轮将 `MapRuntimePort` 视为完成。
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
- Maps 与 Edit sidecar 仍缺少完整的 session/runtime boundary 收口。
- `PeopleDashboardWidget.set_library_root()` 仍保留 asset-aware compatibility
  factory，以避免 group summary 失去 common-photo cover 而直接退化成拼图；
  `PlaybackCoordinator.set_people_library_root()`、`ManualFaceAddWorker` 与
  `PinnedItemsService` standalone 清理路径仍保留 compatibility fallback，
  但不再是运行时主路径。
- temp-library 端到端仍保持 out of scope。

## 6. 最新验证

本轮在项目 `.venv` 下执行：

- `.venv/bin/python -m pytest tests/test_navigation_coordinator_cluster_gallery.py tests/gui/viewmodels/test_gallery_viewmodel.py tests/ui/controllers/test_context_menu_cover.py tests/test_album_tree_model.py tests/test_settings_manager.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/gui/widgets/test_people_dashboard_widget.py tests/architecture/test_layer_boundaries.py -q`
- `.venv/bin/python tools/check_architecture.py`

结果：

- 上述测试全部通过（`115 passed`）。
- `tools/check_architecture.py` 通过。
- 仍有既有的 pytest `Unknown config option: env` warning。
- 仍有既有的 legacy model shim / pairing deprecation warnings。
- 本轮新增验证意图：People resolver 入口、pinned People query、
  People snapshot retarget、People cover visibility，以及 GUI bootstrap
  People factory import guardrail。

之前各个切片的针对性验证命令和结果，继续以 `06` 到 `20` 交接文档为准；
本文件只保留当前整体验证结论和最新增量验证。

## 7. 下一步交接

1. 推进 Phase 5 的 Maps：地图可用性查询、native fallback 与可测试的
   `MapRuntimePort` runtime seam；继续沿用 `LocationTrashNavigationService`
   作为临时 GUI seam，但不把它视为最终边界。
2. 推进 Phase 5 的 Edit：`.ipo` sidecar 读写、save/reset/export use case
   与 `EditSidecarPort` 的 session/runtime 收口。
3. 补 `temp library` 端到端回归：import / move / delete / restore，以及
   rescan 后用户状态保护。
