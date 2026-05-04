# 19 - GUI Update + Navigation Session Migration

> **版本:** 1.0 | **日期:** 2026-05-01  
> **状态:** 已完成  
> **范围:** Phase 4 GUI residual orchestration cleanup（LibraryUpdate + Location/Trash）

---

## 1. 背景与目标

继续清理 GUI 残留编排，不对 Maps / Edit 做大范围切入。本轮关注两个剩余缝隙：

- `LibraryUpdateService` 仍持有 scan / pair / finalize / restore-refresh 编排细节。
- Location / Recently Deleted 仍从 GUI 直接触达库/运行时行为。

目标是让 GUI 只保留 presentation coordination、Qt transport 与路由职责，继续复用当前 runtime/library 边界（`LibraryManager` + bootstrap services/mixins），不强推新的 session 抽象。

## 2. 变更摘要

### 2.1 LibraryUpdate

- 在 `LibraryScanService` 上补充更高层 scan 入口：同步 rescan、scan finalize hook、restore-refresh rescan 入口。
- finalize hook 统一处理：Recently Deleted 保留字段、snapshot persistence、link rebuild、stale-row reconciliation、可选 Live Photo pairing follow-up。
- `RescanWorker` 改为通过 runtime scan surface 刷新恢复相册。
- `LibraryUpdateService` 不再直接 import `ScannerWorker` / `RescanWorker`。
- worker ownership 移入专用 GUI task runner；`LibraryUpdateService` 保持 presentation adapter，负责：
  - 启动/取消任务
  - 转发 progress/chunk 信号
  - 发出 `indexUpdated`、`linksUpdated`、`assetReloadRequested`
  - 维持 facade-facing API 兼容

### 2.2 Location / Trash

- 新增 `LocationTrashNavigationService`，负责：
  - Recently Deleted 目录准备
  - trash cleanup 节流与后台调度
  - geotagged assets 后台加载
  - Location reload 的 request-serial 管理
- `NavigationCoordinator` 去除 trash cleanup 线程逻辑，保持路由绑定。
- `GalleryViewModel` 不再直接调用 `ensure_deleted_directory()` 或 `get_geotagged_assets()`。
- `GalleryViewModel` 只保留 UI 状态：静态选择、路由切换、cluster gallery、location snapshot cache。

### 2.3 Guardrails

- 架构检查扩展：`gui/services/library_update_service.py` 禁止 import `library.workers.*`。
- 相关 GUI regressions 调整为验证新的 boundary 形态。

## 3. 行为说明

- `AppFacade` 公共 API 形态保持不变，变化仅在内部转发路径。
- 当前边界仍以 `LibraryManager` + bootstrap runtime services 为主，本轮不强制新的 `LibrarySession` / `RuntimeContext` 术语层。
- Maps runtime extraction 仍未完成；Location/Trash adapter 是后续 Maps 工作的临时 GUI seam。
- People residual fallback 仍留待后续切片。

## 4. 审查结论

核对现有实现后，本步迁移的主方向与代码一致，但原始结论写得过满，需要以下修正：

- `LibraryUpdateService` 确实通过 `LibraryUpdateTaskRunner` 持有 worker 生命周期；服务本体无直接 `library.workers` import，而具体 worker import 位于 task runner 中。这一边界由 `tools/check_layer_boundaries.py` 与 `tests/architecture/test_layer_boundaries.py` 约束。
- `LibraryScanService.finalize_scan_result()` 已承担 scan finalize 责任，包括 Recently Deleted 保留字段合并、snapshot/links 持久化、stale-row reconciliation 与可选 Live Photo pairing。
- `LocationTrashNavigationService` 已承接 Recently Deleted 准备、trash cleanup 节流与 geotagged assets 后台加载；`GalleryViewModel` 通过 adapter 触发这些流程，不再直接调用 `ensure_deleted_directory()` 或 `get_geotagged_assets()`。
- 但 `AppFacade.open_album()` -> `LibraryUpdateService.prepare_album_open()` 是本轮迁移后的关键兼容入口，这一点需要在文档中明确记录。该路径依赖 `_scan_dependencies()` 的返回契约，曾出现过一次返回值解包回归，导致 album-open 主流程抛出异常；问题已修复，但说明本步并非“无需更正”的完全收口状态。

结论：第 19 步迁移目标已基本落地，但文档应保留关键入口契约、验证锚点与已修复回归，而不应使用“完全一致、无需更正”的强结论。

## 5. 验证

建议将本步验证固定为以下可复现锚点，而不是只保留目标描述：

- `tests/services/test_library_update_service_global_db.py`
  - 覆盖 `LibraryUpdateService.prepare_album_open()` 对 session scan service 的转发。
  - 覆盖空 scope 时 `should_rescan_async` 的行为。
  - 覆盖 `rescan_album()` / `rescan_album_async()` 的 runtime forwarding 形态。
- `tests/test_app_facade_session_open.py`
  - 覆盖 `AppFacade.open_album()` 通过 `prepare_album_open()` 进入 session scan surface。
  - 覆盖 library bound / unbound 时 `sync_manifest_favorites` 的分支。
  - 覆盖 `rescan_current_async()` 的 facade-facing forwarding 兼容形态。
- `tests/gui/viewmodels/test_gallery_viewmodel.py`
  - 覆盖 Recently Deleted 通过 `LocationTrashNavigationService.prepare_recently_deleted()` 打开。
  - 覆盖 Location 视图通过 `request_location_assets()` 异步加载，且不再直接调用 `library.get_geotagged_assets()`。
- `tests/test_navigation_coordinator_refresh.py`
  - 覆盖 `NavigationCoordinator` 不直接触发 trash cleanup。
- `tests/architecture/test_layer_boundaries.py`
  - 覆盖 `gui/services/library_update_service.py` 禁止直接 import `iPhoto.library.workers.*`。

本轮已执行的重点回归：

- `pytest -q tests/services/test_library_update_service_global_db.py tests/test_app_facade_session_open.py`
  - 结果：`14 passed`
  - 用于确认 `prepare_album_open()` / `AppFacade.open_album()` 路径与 async rescan forwarding 已恢复一致。

说明：

- 上述结果足以支撑本步文档结论，但不等于全量 GUI E2E 已完成。
- 若后续继续修改 `LibraryUpdateService`、`AppFacade.open_album()` 或 `_scan_dependencies()` 契约，应优先重跑上述用例。

## 5.1 已修复回归

- `prepare_album_open()` 曾按旧契约将 `_scan_dependencies()` 解包为 3 个返回值，而 `_scan_dependencies()` 当前仅返回 `(library_root, scan_service)`。
- 该不一致会在 `AppFacade.open_album()` 主路径上触发 `ValueError`，影响 bound 与 standalone 两类 album-open 流程。
- 现已修复为按 2 值解包；这类入口契约回归说明本步仍需以 focused regression tests 作为发布前检查项。

## 6. 下一步交接

- 继续清理 People fallback/coordinator residuals（Phase 4）。
- Maps 侧回归时，以 `LocationTrashNavigationService` 作为临时 GUI seam，避免直接回引 `LibraryManager`。
- Edit sidecar、完整 Maps fallback、temp-library E2E 仍保持 out of scope。
