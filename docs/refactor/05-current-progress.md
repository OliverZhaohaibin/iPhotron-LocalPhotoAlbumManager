# 05 - 当前进度

> **版本:** 1.1 | **日期:** 2026-05-04
> **状态:** 进行中（GUI/runtime 主路径已收口；legacy 隔离待下个 major release 移除）
> **范围:** vNext 重构进度、收口结论与交接记录

---

## 1. 结论

本轮可以确认：GUI/runtime 主路径已经按新架构收口到
`RuntimeContext -> LibrarySession`，不再依赖 GUI 运行期 compatibility
fallback。

这次收口结论基于四个事实：

- GUI Python 源码中已经没有 `create_compat_*` 调用，也没有直接调用
  `LibraryManager.start_scanning()` 的主路径入口。
- `RuntimeContext.open_library()` 现在优先通过
  `LibraryManager.bind_path_from_session()` 绑定，避免 GUI 主路径在
  `bind_path()` 中隐式创建 headless `LibrarySession`。
- `LibraryUpdateService`、`AssetDataLoader`、`ImportWorker`、
  `AlbumsDashboard`、`GalleryViewModel`、`DialogController` 等先前 residual
  点都已改为显式消费 bound session surface；缺少 active session 时改为显式报错
  或安全 no-op，不再静默创建 compat service。
- legacy-only 的 `AlbumViewModel` 已迁入
  `src/iPhoto/legacy/gui/viewmodels/album_viewmodel.py`，并已通过架构门禁禁止
  production runtime 再次引用 `iPhoto.legacy`。

需要区分的是：

- “GUI/runtime 主路径已经收口”是 **已完成**。
- “所有兼容入口都从仓库中物理删除”不是本轮目标；`app.py`、`appctx.py`、
  `bootstrap/service_factories.py`、`LibraryManager.bind_path()` 仍保留给
  CLI、headless 或 compatibility-only 路径使用。

## 2. 本轮完成

- 新增 `gui/services/session_service_resolver.py`，统一解析 bound
  album-metadata / query / lifecycle / operation / scan service。
- 清零 GUI runtime `create_compat_*` residual：
  `library_update_service.py`、`album_metadata_service.py`、
  `asset_move_service.py`、`restoration_service.py`、
  `asset_import_service.py`、`asset_data_loader.py`、
  `asset_loader_worker.py`、`asset_loader_utils.py`、
  `import_worker.py`、`move_worker.py`、`albums_dashboard.py`、
  `export_controller.py` 等都已切到 session-bound surface。
- 清零 GUI runtime 直接 `start_scanning()` residual：
  `RuntimeContext`、`DialogController`、`GalleryViewModel`、
  `LibraryUpdateService` 统一改走 facade/session scan surface。
- `LibraryManager` 新增 `bind_path_from_session()`；
  GUI 通过 session 打开的主路径不再触发 `bind_path()` 的 headless-session fallback。
- 将 `src/iPhoto/gui/viewmodels/album_viewmodel.py` 迁入
  `src/iPhoto/legacy/gui/viewmodels/`，并同步更新测试引用。
- 扩展架构门禁：
  - GUI runtime 禁止 import/call `bootstrap.service_factories.create_compat_*`
  - GUI runtime 禁止直接调用 `LibraryManager.start_scanning()`
  - non-legacy runtime 禁止 import `iPhoto.legacy`
- 补齐 `LibraryEditService` “sidecar 保存后重建 service 仍可恢复”的回归测试。

## 3. 当前阶段状态

- Phase 0：完成当前收口目标。
  guardrail 已覆盖 GUI compat factory、legacy scan entry 与 legacy quarantine
  import 回归。
- Phase 1：完成当前收口目标。
  GUI startup 已使用 session surface；`RuntimeContext -> LibrarySession`
  现在是 GUI 主路径的唯一 library-scoped 组装入口。
- Phase 2：保持完成状态。
  `global_index.db` 继续作为当前 runtime asset source of truth；
  用户状态边界不受本轮收口回退。
- Phase 3：保持完成状态。
  GUI startup/open/rescan/pair 与异步扫描刷新已统一走 session/facade
  scan surface。
- Phase 4：完成本轮主目标。
  GUI runtime compat fallback 已清零，扫描入口已统一，legacy-only 视图模型已隔离。
  兼容入口仍保留在非 GUI 主路径，不属于本阶段 blocker。
- Phase 5：本轮补齐 Edit 验收。
  sidecar 保存后“重启/重建 service 仍可恢复”的测试已经补齐并通过。
- Phase 6：回归与门禁更新完成本轮增量。
  architecture guard、targeted pytest 与 required regression 均已绿色。

## 4. Legacy 隔离

本轮建立了 `src/iPhoto/legacy/` 隔离区，当前已迁入：

- `src/iPhoto/legacy/gui/viewmodels/album_viewmodel.py`

约束如下：

- production runtime 不得 import `iPhoto.legacy`
- `src/iPhoto/legacy/` 明确标记为临时隔离区
- `src/iPhoto/legacy/` 计划在 **下一个 major release** 中移除

换句话说，legacy 目录现在的作用不是继续扩张，而是把已确认只服务旧路径的代码
隔离起来，避免它再次回流到主运行路径。

## 5. 已知例外

- `app.py`、`appctx.py`、`bootstrap/service_factories.py`、
  `library/manager.py` 仍保留 compatibility/headless 角色，但不再允许 GUI
  主路径依赖它们完成 library-scoped 业务组装。
- `bootstrap/service_factories.py` 仍然保留 `create_compat_*` 定义；
  这些 factory 现在只允许 explicit compatibility entry point 使用。
- `library/filesystem_watcher.py` 与 `library/scan_coordinator.py` 内仍存在
  `start_scanning()` plumbing；它们属于 library-internal 扫描管线，不再是 GUI
  runtime 直接入口。
- `src/iPhoto/legacy/` 中的隔离代码仍会被测试引用，以确保历史兼容行为可观察；
  但 production runtime 已禁止引用。

## 6. 最新验证

本轮在 `D:\python_code\iPhoto\.venv` 下执行：

- `D:\python_code\iPhoto\.venv\Scripts\python.exe tools\check_architecture.py`
- `D:\python_code\iPhoto\.venv\Scripts\python.exe -m pytest tests\architecture -q`
- `D:\python_code\iPhoto\.venv\Scripts\python.exe -m pytest tests\application\test_appctx_runtime_context.py tests\services\test_library_update_service_global_db.py -q`
- `D:\python_code\iPhoto\.venv\Scripts\python.exe -m pytest tests\application\test_runtime_context.py tests\application\test_dialog_controller_runtime_binding.py tests\test_app_facade_session_open.py tests\services\test_asset_import_service.py tests\services\test_asset_move_service.py tests\services\test_restoration_service.py tests\services\test_library_update_service_global_db.py tests\application\test_library_edit_service.py tests\gui\viewmodels\test_gallery_viewmodel.py -q`

结果：

- `tools/check_architecture.py` 通过。
- `tests/architecture -q`：`18 passed`。
- `tests/application/test_appctx_runtime_context.py` +
  `tests/services/test_library_update_service_global_db.py`：`11 passed`。
- session-only 收口相关 targeted suite：`76 passed`。
- 额外源码检索确认：
  - `src/iPhoto/gui/**/*.py` 中没有 `create_compat_*`
  - `src/iPhoto/gui/**/*.py` 中没有 `start_scanning(`
  - `src/iPhoto/**/*.py` production source 中没有 `iPhoto.legacy` import

当前仍保留既有 warning：

- pytest `Unknown config option: env`
- legacy model shim / pairing deprecation warnings

## 7. 下一步

1. 维持当前 guardrail，防止 GUI runtime 再次回流到 compat factory 或 legacy scan
   entry。
2. 如果后续确认没有外部调用方依赖 `src/iPhoto/legacy/`，在下一个 major release
   直接删除该隔离目录。
3. 对 remaining compatibility entry point 继续保持“可用但不扩张”的策略，不再把
   新业务规则写回 `app.py`、`appctx.py` 或 compat factory。
