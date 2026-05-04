# 05 - 当前进度

> **版本:** 1.3 | **日期:** 2026-05-04
> **状态:** 全仓清理收口完成：legacy 强制隔离
> **范围:** vNext 重构进度、收口结论与交接记录

---

## 1. 结论

本轮按“强制 legacy 隔离”口径完成全仓清理收口。production runtime 现在只允许依赖
`RuntimeContext -> LibrarySession`、application ports/services、bootstrap session
surfaces 和 infrastructure adapters；旧 compatibility/domain-repository/standalone
shim 已迁入 `src/iPhoto/legacy/`。

关键结论：

- production `src/iPhoto/**` 不再 import `iPhoto.legacy`。
- production `src/iPhoto/**` 不再 import `iPhoto.models.*`。
- GUI services 不再静默创建 `create_compat_*` / `create_standalone_*` fallback；
  缺少 active session 时显式报错或安全 no-op。
- `LibraryAssetRuntime.repository` 不再暴露 legacy `IAssetRepository` adapter，而是返回
  runtime asset port。
- `LibraryManager` 已被 production `LibraryRuntimeController` 替换；旧 manager 已迁入
  `src/iPhoto/legacy/library/manager.py`。
- legacy 隔离区只供历史行为测试显式 import，并计划在下一个 major release 删除。

## 2. 本轮完成

- 迁入 root compat：
  `legacy/app.py`、`legacy/appctx.py`。
- 迁入 compat bootstrap：
  `legacy/bootstrap/container.py`、`legacy/bootstrap/service_factories.py`、
  `legacy/bootstrap/standalone_album_services.py`。
- 迁入旧 domain-repository application service：
  `legacy/application/services/album_service.py`、
  `legacy/application/services/asset_service.py`、
  `legacy/application/services/library_service.py`、
  `legacy/application/services/parallel_scanner.py`、
  `legacy/application/services/paginated_loader.py`。
- 迁入旧 use case graph：
  `legacy/application/use_cases/` 中除 production `scan_library.py` 以外的旧
  domain-repository use case。
- 迁入旧 repository/model graph：
  `legacy/domain/repositories.py`、
  `legacy/infrastructure/repositories/sqlite_album_repository.py`、
  `legacy/infrastructure/repositories/sqlite_asset_repository.py`、
  `legacy/infrastructure/repositories/index_store_asset_repository.py`、
  `legacy/models/album.py`、`legacy/models/types.py`。
- 新增 production `AlbumManifestService`，补齐 `LiveGroup` domain type，移除
  production 对 `iPhoto.models.*` 的依赖。
- 新增 production `LibraryRuntimeController`，RuntimeContext 和 GUI 改依赖新
  controller。
- `io/scanner_adapter.py` 内聚文件发现线程，不再 import legacy `ScanAlbumUseCase`。
- GUI album metadata/import/move/update/query/trash navigation 服务改为 session-only。
- 新增 `src/iPhoto/legacy/README.md`，明确 no production import、no new
  functionality、next-major deletion。

## 3. 当前阶段状态

- Phase 0：架构门禁已升级并通过。
- Phase 1：`RuntimeContext -> LibrarySession` 是 production runtime 的 active
  library 入口。
- Phase 2：runtime repository port 与 legacy domain repository graph 已隔离。
- Phase 3：production 扫描入口保留 `ScanLibraryUseCase`；旧 scan/use-case graph
  已迁入 legacy。
- Phase 4：GUI services/coordinators/viewmodels 主路径 session-only。
- Phase 5：People/Maps/Thumbnail/Edit 的 runtime surface 保持 application/session
  边界。
- Phase 6：架构检查、targeted regression 和 legacy 行为测试已通过。

## 4. Legacy 隔离规则

`src/iPhoto/legacy/` 是临时 quarantine：

- production runtime 不得 import `iPhoto.legacy`。
- production runtime 不得 import `iPhoto.models.*`。
- 不在 legacy 内新增功能。
- 旧行为测试必须显式 import `iPhoto.legacy.*`。
- 整个 legacy 隔离区计划在 **下一个 major release** 删除。

## 5. 最新验证

本轮在 macOS 本地 `.venv` 下执行并通过：

- `.venv/bin/python -m compileall -q src/iPhoto tests`
- `.venv/bin/python tools/check_architecture.py`
- `.venv/bin/python -m pytest tests/architecture -q`：`20 passed`
- `.venv/bin/python -m pytest tests/architecture tests/infrastructure/test_library_asset_runtime.py -q`：`22 passed`
- `.venv/bin/python -m pytest tests/application/test_runtime_context.py tests/application/test_library_session.py tests/application/test_scan_library_use_case.py -q`：`8 passed`
- `.venv/bin/python -m pytest tests/services/test_library_update_service_global_db.py tests/gui/viewmodels/test_gallery_viewmodel.py -q`：`41 passed`
- `.venv/bin/python -m pytest tests/application/test_temp_library_end_to_end.py tests/application/test_library_asset_lifecycle_service.py tests/services/test_asset_move_service.py tests/services/test_restoration_service.py -q`：`39 passed`
- `.venv/bin/python -m pytest -q`：`1992 passed, 8 skipped`
- legacy 行为测试：
  `tests/test_app_open_album_lazy.py`、
  `tests/application/test_app_rescan_atomicity.py`、
  `tests/test_pairing_live.py`、
  `tests/test_app_live_sync.py`、
  `tests/application/test_phase2_use_cases.py`、
  `tests/application/test_phase2_new_use_cases.py`、
  `tests/application/test_scan_use_case.py`、
  `tests/infrastructure/test_sqlite_repo.py`、
  `tests/test_batch_insert.py`、
  `tests/infrastructure/test_index_store_asset_repository_adapter.py`：`68 passed`

静态确认：

- production source 搜索 `iPhoto.legacy`、`iPhoto.models`、`create_compat_`、
  `create_standalone_`、root `iPhoto.app/appctx`、旧 domain repository/use case/service
  import 均无命中。

当前仍保留既有 warning：

- pytest `Unknown config option: env`
- legacy behavior tests 中的既有 deprecation warning

## 6. Release 前验收

以下是产品验收项，不影响本轮架构收口结论：

- 启动 Qt GUI smoke test。
- 手动打开一个已有 library，确认资产加载、扫描、移动、删除、恢复流程符合预期。
