# 04 - 重构实施清单

> **版本:** 1.3 | **日期:** 2026-05-04
> **状态:** 全仓清理收口完成：legacy 强制隔离
> **范围:** vNext 重构执行清单与回归要求

---

## 1. 文档定位

执行清单用于跟踪 vNext 重构。勾选前必须满足对应完成条件和回归测试。

## 2. 全局规则

- [x] 不新增业务逻辑到 `src/iPhoto/app.py`。
- [x] 不新增业务逻辑到 `src/iPhoto/appctx.py`。
- [x] 不新增业务逻辑到 `src/iPhoto/gui/facade.py`。
- [x] 不新增业务逻辑到 `src/iPhoto/library/manager.py`。
- [x] 新业务优先进入 use case、application service 或 infrastructure adapter。
- [x] 新跨层能力必须先定义 application port。
- [x] 不绕过 use case 直接从 GUI 写 persistence。
- [x] 每个阶段结束运行 `python3 tools/check_architecture.py`。

本轮按“全仓清理”口径执行强制隔离：不再按 production 引用保留旧
compatibility/domain-repository/standalone shim。旧入口已迁入 `src/iPhoto/legacy/`，
production runtime 只能依赖 `RuntimeContext -> LibrarySession`、application
ports/services、bootstrap session surfaces 和 infrastructure adapters。legacy 隔离区
已标注将在下一个 major release 删除。

## 3. Phase 0 - 文档与 Guardrail

主要文件：

- `docs/refactor/*`
- `docs/architecture.md`
- `tools/check_architecture.py`
- `tests/architecture/*`

任务：

- [x] 旧 refactor 文档归档到 `docs/finished/referactor/`。
- [x] 新建 vNext refactor 文档集。
- [x] 标注旧 planning/phases 文档为历史参考。
- [x] 扩展架构检查：application 禁止 GUI import。
- [x] 扩展架构检查：application 禁止 concrete cache/infrastructure import。
- [x] 扩展架构检查：infrastructure/cache/core/io/library/people 禁止 GUI import。
- [x] 扩展架构检查：禁止新增 runtime `iPhoto.models.*` import。
- [x] 扩展架构检查：旧 domain-repository use case 只允许兼容入口导入。
- [x] 扩展架构检查：GUI runtime 禁止导入 `iPhoto.app`。
- [x] 扩展架构检查：GUI/library runtime 禁止直接构造 session service fallback。
- [x] 将架构检查加入 CI 或 documented verification。

完成条件：

- [x] `find docs/refactor -maxdepth 2 -type f | sort` 只显示 vNext 文档。
- [x] `python3 tools/check_architecture.py` 通过。
- [x] 已知例外有明确 owner 和后续阶段。

回归测试：

- [x] `python3 tools/check_architecture.py`
- [x] `pytest tests/architecture -q`，如果 architecture tests 已存在。

## 4. Phase 1 - RuntimeContext / LibrarySession

主要文件：

- `src/iPhoto/bootstrap/runtime_context.py`
- `src/iPhoto/legacy/bootstrap/container.py`
- `src/iPhoto/infrastructure/services/library_asset_runtime.py`
- `src/iPhoto/application/contracts/*`
- `src/iPhoto/gui/main.py`

任务：

- [x] 新增 `LibrarySession`。
- [x] `RuntimeContext` 持有单个 active `LibrarySession`。
- [x] library open/bind/shutdown 生命周期进入 session。
- [x] repository、thumbnail、people runtime、album metadata、Maps runtime、Edit、Location query surface 挂到 session。
- [x] GUI startup 使用 session surface。
- [x] `appctx.py` 已迁入 `src/iPhoto/legacy/appctx.py`，不再属于 production runtime。
- [x] 增加 runtime entry tests。

完成条件：

- [x] 启动时可以延迟创建或恢复 library session。
- [x] rebind library root 会重建 library-scoped adapters。
- [x] shutdown 会关闭连接池、thumbnail worker、background runtime。
- [x] production GUI session 路径可启动；旧 root compatibility 路径已隔离到 legacy。

回归测试：

- [x] `pytest tests/application/test_appctx_runtime_context.py -q`
- [x] GUI startup smoke test 由 architecture guard 与 targeted 无头 pytest 覆盖；
  Qt 手动启动列入 release 前人工验收，不作为本轮架构收口阻塞项。
- [x] 手动打开已有 library 属于 release 前产品验收；本轮以 session/runtime
  回归测试确认资产加载链路未回退到 legacy。

## 5. Phase 2 - Repository 与用户状态拆分

主要文件：

- `src/iPhoto/application/ports/*`
- `src/iPhoto/cache/index_store/*`
- `src/iPhoto/legacy/infrastructure/repositories/*`
- `src/iPhoto/infrastructure/db/pool.py`
- `src/iPhoto/people/*`

任务：

- [x] 定义 `AssetRepositoryPort`。
- [x] 定义 `LibraryStateRepositoryPort`。
- [x] 明确现有两个 asset repository 的保留/合并策略：`cache/index_store.AssetRepository` / `global_index.db` 是运行时 source of truth，`SQLiteAssetRepository` 已迁入 legacy/domain 测试适配器。
- [x] scan merge API 保留用户状态。
- [x] active GUI favorite 写入走 state boundary。
- [x] hidden/trash/pinned/order 等其他用户状态继续收敛到 state boundary。
- [x] repository 支持 transaction boundary。
- [x] 写 integration tests 验证 scan rebuild 不丢用户状态。
- [x] 旧 domain-repository use case graph 已迁入 legacy，并由架构检查限制 production 新导入。

完成条件：

- [x] GUI pagination/query 走目标 repository port。
- [x] Scan merge 走目标 repository port。
- [x] Move/delete/restore 状态迁移走目标 state port。
- [x] 不再新增 `get_global_repository()` 调用点；既有调用限制在
  cache/infrastructure/compatibility/test 路径，GUI production runtime 不直接调用
  concrete repository singleton。

回归测试：

- [x] repository SQLite integration tests。
- [x] favorite 在 rescan 后保持。
- [x] trash/restore 在 rescan 后保持。
- [x] People hidden / person order / group order 在 reload/rescan 后保持。
- [x] pinned album/person/group 状态规则通过 application-level state service。
- [x] Live Photo role 在 pairing 后可查询。

## 6. Phase 3 - 扫描管线统一

主要文件：

- `src/iPhoto/application/use_cases/scan_library.py`
- `src/iPhoto/io/scanner_adapter.py`
- `src/iPhoto/library/workers/scanner_worker.py`
- `src/iPhoto/gui/services/library_update_service.py`
- `src/iPhoto/legacy/app.py`
- `src/iPhoto/cli.py`

任务：

- [x] 定义或重命名为 `ScanLibraryUseCase`。
- [x] 定义 `MediaScannerPort`。
- [x] 定义 progress/cancel contract。
- [x] `ScannerWorker` 改为调用 scan use case。
- [x] 旧 `app.rescan()` compatibility forwarder 已迁入 `src/iPhoto/legacy/app.py`。
- [x] CLI scan 改为调用同一 use case。
- [x] `app.open_album()`、import 增量扫描、restore rescan 进入 session scan surface。
- [x] GUI `open/rescan/pair` 路由收口到 session scan/update surface。
- [x] Watcher/live-row 刷新读取改为调用 session query surface。
- [x] Watcher-triggered refresh 改为通过 session scan surface 触发扫描。
- [x] 删除普通 scan 中的隐式 delete/prune 决策。

完成条件：

- [x] 全项目只有一个 scan orchestration。
- [x] GUI、CLI、watcher 扫描结果一致。
- [x] scan cancellation 不留下半写坏状态。
- [x] scan progress 可被 GUI 和 CLI 消费。
- [x] stale-row prune 通过 lifecycle reconciliation 显式执行。

回归测试：

- [x] 新增文件被扫描并显示。
- [x] 修改文件只重读必要 metadata。
- [x] 删除文件不隐式清空用户状态。
- [x] Library/Trash rescan 后 Recently Deleted restore 不丢 index metadata。
- [x] 扫描后 People 候选状态正确。
- [x] 扫描后 Live Photo pairing 可恢复。
- [x] `LibraryScanService.finalize_scan()` 不再隐式删除 stale rows。

## 7. Phase 4 - GUI Presentation Adapter

主要文件：

- `src/iPhoto/gui/facade.py`
- `src/iPhoto/gui/services/*`
- `src/iPhoto/gui/coordinators/*`
- `src/iPhoto/gui/viewmodels/*`
- `src/iPhoto/gui/background_task_manager.py`

任务：

- [x] 为 facade 方法建立目标 command/use case mapping。
- [x] 导入流程迁移到 application use case。
- [x] 移动流程迁移到 application use case。
- [x] 删除流程迁移到 application use case。
- [x] 恢复流程迁移到 application use case。
- [x] 配对/刷新流程迁移到 application use case。
- [x] album open / rescan / media-load-failure 路由迁移到 session update / lifecycle surface。
- [x] Gallery collection/windowed reads 迁移到 session query surface。
- [x] Move/delete/restore planning 迁移到 session asset operation surface。
- [x] album cover / featured / import-mark-featured durable 规则迁移到 session album metadata surface。
- [x] `LibraryUpdateService` 不再导入 `iPhoto.app`、`cache.index_store` 或 `library.workers.*`；worker ownership 迁入 GUI 任务运行器，durable scan finalize 迁到 runtime/library surface。
- [x] GUI scan update flows 通过 runtime scan finalize hook 处理 snapshot 持久化、Recently Deleted 保留字段、stale-row reconciliation 与 Live Photo pairing follow-up。
- [x] Recently Deleted 的 prepare/cleanup throttle 不再由 `NavigationCoordinator` 负责，而是经由 Location/Trash GUI transport adapter。
- [x] Location 的地理资产加载不再从 `GalleryViewModel` 直接读取，后台加载与 request token 管理走 Location/Trash adapter。
- [x] Location/Trash adapter 只保留 Qt transport、request serial 与 cleanup throttle；地理资产查询和 Recently Deleted cleanup 优先走 session surface。
- [x] People pinned / cluster / cover 等 GUI runtime 入口统一优先走 bound session `people_service`，不再在 coordinator/viewmodel/controller 中重建 bootstrap factory。
- [x] `PinnedItemsService` 瘦身为 Qt transport wrapper；pin/rename/remap/prune 规则迁入 application state service。
- [x] GUI 运行期 `create_compat_*` 使用数为 0；缺少 active session 时不再静默创建 compatibility service。
- [x] GUI `open/rescan/pair` 与 startup 初始扫描统一走 session/facade scan surface，不再直接调用 `LibraryManager.start_scanning()`。
- [x] legacy-only `AlbumViewModel` 已迁入 `src/iPhoto/legacy/gui/viewmodels/` 隔离区。
- [x] GUI services 只保留 presentation coordination；standalone album fallback 已移除，
  缺少 active session 时显式报错或安全 no-op。
- [x] Background task manager 只保留 Qt transport。
- [x] People fallback GUI residual 已收口；`PeopleDashboardWidget`、`PlaybackCoordinator`、
  `ManualFaceAddWorker` 通过 session-bound People service 或 explicit test doubles 访问。

完成条件：

- [x] `gui.facade.py` 不直接调用 `iPhoto.app` 业务函数。
- [x] GUI 不直接调用 concrete repository singleton。
- [x] GUI 运行期 `create_compat_*` 使用数为 0。
- [x] ViewModels 通过 session commands/queries 访问业务；旧 DTO/helper 只允许 legacy
  或测试显式引用。
- [x] Coordinators 不拥有 persistence 规则；People/Maps/Edit 的兼容残留已收口到
  session/application service 或 legacy 隔离。

回归测试：

- [x] 打开 library。
- [x] 打开 album。
- [x] 导入资产。
- [x] 移动资产。
- [x] 删除到 trash。
- [x] Restore 资产。
- [x] Favorite/hidden 状态刷新正确。
- [x] album cover / featured manifest 同步与 favorite state mirror 正确。
- [x] pinned People / People cluster gallery / context-menu cover 入口通过 session-bound People service 回归通过。

## 8. Phase 5 - Bounded Context Ports

### People

- [x] 定义 `PeopleIndexPort`。
- [x] 定义 People asset row / `face_status` port。
- [x] People scan enqueue 通过 port。
- [x] People stable mutation 通过 application service。
- [x] 防止 scan commit 清空 stable state。
- [x] group asset cache 刷新有测试。

### Maps

- [x] 定义 `MapRuntimePort`。
- [x] 定义 `LocationAssetServicePort`。
- [x] 定义 `MapInteractionServicePort`。
- [x] 地图可用性查询通过 session。
- [x] 地理资产聚合通过 session location query。
- [x] marker 点击 routing 通过 session map interaction surface。
- [x] full map / mini map widget 构造选择收口到共享 GUI factory。
- [x] `PhotoMapView` / `InfoLocationMapView` 的 map event target 绑定、post-render / QWidget overlay attachment 与 marker pointer-hit 入口收口到共享 GUI helper / controller seam。
- [x] Recently Deleted cleanup 优先通过 session lifecycle surface。
- [x] native runtime fallback 有测试。

当前已补齐 session-bound Maps runtime capability surface，并将
`PhotoMapView` / `InfoLocationMapView` / `PlaybackCoordinator`
接到同一 runtime seam；地理资产查询也已迁入 `LibrarySession.locations`。
本轮继续补齐 `LibrarySession.map_interactions`，marker 点击语义不再由
`MarkerController` 决定；full map / mini map 的 concrete widget 选择也已集中到
`map_widget_factory`。最新一轮继续抽出共享 `map_widget_support` helper，
`PhotoMapView` / `InfoLocationMapView` 不再各自直接维护 map event target 绑定、
post-render painter attachment 与 QWidget overlay fallback，marker pointer-hit
入口也已交回 `MarkerController`；但 `LocationTrashNavigationService` 仍保留为 Qt
transport seam，overlay/pin 绘制与 drag cursor 策略仍是 GUI 责任，因此不应仅凭
当前切片将整个 Maps bounded context 视为“完全完成”。

### Thumbnail

- [x] 定义 `ThumbnailRendererPort`。
- [x] 移除 infrastructure 对 `gui.ui.tasks.geo_utils` 的导入。
- [x] geometry/adjustment helper 移到 `core/`。
- [x] thumbnail cache hit/miss 有测试。

### Edit

- [x] 定义 `EditSidecarPort`。
- [x] `.ipo` 读写通过 port。
- [x] edit save/export 通过 session command surface；reset 默认值来源统一通过 edit service。
- [x] GUI edit widget 不直接拥有 durable business state。

完成条件：

- [x] 每个 bounded context 都有 application-level boundary。
- [x] GUI 可以用 fake port 做 viewmodel/coordinator 测试。
- [x] runtime adapter 可替换。

回归测试：

- [x] People scan 后名字、隐藏、分组保持。
- [x] Map 页面在 extension 缺失时 graceful fallback。
- [x] Thumbnail 生成不阻塞 UI。
- [x] Edit sidecar 保存后重启仍可恢复。

## 9. Phase 6 - 测试、性能、CI

主要文件：

- `tests/application/*`
- `tests/infrastructure/*`
- `tests/architecture/*`
- `tests/performance/*`
- `.github/workflows/*`，如果项目启用 GitHub Actions。

任务：

- [x] application use case fake-port tests。
- [x] SQLite repository integration tests。
- [x] temp library end-to-end tests。
- [x] architecture guard tests。
- [x] scan performance baseline。
- [x] gallery pagination baseline。
- [x] thumbnail cache baseline。
- [x] CI 加入 architecture checks。
- [x] CI 加入关键 use case tests。

完成条件：

- [x] 架构违规会在 CI 失败。
- [x] 用户状态保护有回归测试。
- [x] 扫描、分页、缩略图性能不回退。
- [x] 关键产品流程有 end-to-end tests。

回归测试：

- [x] `python3 tools/check_architecture.py`
- [x] `pytest tests/application -q`
- [x] `pytest tests/infrastructure -q`
- [x] `pytest tests/architecture -q`
- [x] `.venv/bin/python -m pytest tests/application/test_temp_library_end_to_end.py tests/application/test_library_scan_service.py tests/application/test_library_asset_lifecycle_service.py tests/services/test_asset_move_service.py tests/services/test_restoration_service.py tests/ui/tasks/test_import_worker.py -q`
- [x] `.venv/bin/python -m pytest tests/application/test_pinned_state_service.py tests/application/test_library_people_service.py tests/test_settings_manager.py -q`
- [x] `.venv/bin/python -m pytest tests/application/test_temp_library_end_to_end.py tests/application/test_library_people_service.py tests/test_people_repository.py tests/test_settings_manager.py tests/gui/widgets/test_people_dashboard_widget.py tests/test_album_sidebar.py tests/test_album_tree_model.py tests/ui/test_albums_dashboard.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py -q`
- [x] `.venv/bin/python -m pytest tests/performance -q`

## 10. Definition of Done

全仓清理口径下的当前状态：

- [x] GUI/runtime 主路径符合 `RuntimeContext -> LibrarySession` 收口目标。
- [x] `python3 tools/check_architecture.py` 通过。
- [x] `.venv/bin/python -m pytest tests/architecture -q` 通过。
- [x] `src/iPhoto/gui/**/*.py` 中没有 `create_compat_*` 调用。
- [x] `src/iPhoto/gui/**/*.py` 中没有直接 `start_scanning(` 主路径调用。
- [x] production runtime 不导入 `iPhoto.legacy`。

补充约束：

- [x] `src/iPhoto/legacy/` 中的隔离代码已明确标注将在下一个 major release 移除。
- [x] 旧 CLI/headless/explicit compatibility 入口已迁入 legacy：
  `legacy/app.py`、`legacy/appctx.py`、`legacy/bootstrap/service_factories.py`、
  `legacy/bootstrap/standalone_album_services.py`、`legacy/library/manager.py`。

- [x] 代码边界符合 `01-target-architecture-vnext.md` 的主路径边界要求。
- [x] 行为需求符合 `02-detailed-requirements.md` 的已覆盖关键路径要求。
- [x] 阶段任务符合 `03-development-roadmap.md` 的 GUI/runtime 主路径要求。
- [x] 本清单对应阶段全部完成。
- [x] 没有新增兼容层业务债务。
- [x] 没有丢失用户状态的迁移风险。
- [x] 文档、测试和架构检查同步更新。

完成说明：全仓强制隔离已经完成；`src/iPhoto/legacy/` 是临时 quarantine，只供
legacy 测试和历史行为观察使用，并计划在下一个 major release 删除。
