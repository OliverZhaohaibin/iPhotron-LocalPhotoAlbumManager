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

核对现有实现后，变更描述与代码一致：

- `LibraryUpdateService` 通过 `LibraryUpdateTaskRunner` 持有 worker 生命周期，服务本体无直接 `library.workers` import；对应 guardrail 已在 `tools/check_layer_boundaries.py` 与 `tests/architecture/test_layer_boundaries.py` 覆盖。
- `LibraryScanService.finalize_scan_result()` 已包含 Recently Deleted 保留字段合并、snapshot/links 持久化、stale-row reconciliation 与可选 Live Photo pairing。
- `LocationTrashNavigationService` 负责 trash cleanup 节流与 geotagged assets 后台加载，`GalleryViewModel` 使用 adapter 获取数据。

结论：第 19 步文档描述与当前代码一致，无需更正。

## 5. 验证

目标回归覆盖：

- `LibraryUpdateService` runtime forwarding 与 task-runner delegation
- `GalleryViewModel` Recently Deleted / Location 流程通过新的 adapter
- `NavigationCoordinator` 保持无 direct trash cleanup 调用
- `AppFacade` 维持 async rescan forwarding 形态
- `LibraryUpdateService` worker import 的架构边界检查

环境说明：

- 本轮命令式验证曾因本地 Codex 权限限制部分阻断；如需完整结果请在具备命令权限时重跑。

## 6. 下一步交接

- 继续清理 People fallback/coordinator residuals（Phase 4）。
- Maps 侧回归时，以 `LocationTrashNavigationService` 作为临时 GUI seam，避免直接回引 `LibraryManager`。
- Edit sidecar、完整 Maps fallback、temp-library E2E 仍保持 out of scope。
