# 20 - People GUI Session Residual Migration

> **版本:** 1.0 | **日期:** 2026-05-01  
> **状态:** 已完成  
> **范围:** Phase 4 GUI residual cleanup（People pinned/query/cover runtime entry）

---

## 1. 背景与目标

继续承接 `19-gui-update-navigation-session-migration.md`。本轮不再扩展
People domain 本身，而是清掉 GUI runtime 里剩余的 People bootstrap
factory 依赖。

迁移前的问题是：

- `MainCoordinator`、`NavigationCoordinator`、`GalleryViewModel`、
  `AlbumTreeModel`、`ContextMenuController` 仍可能直接或间接按 root
  重建 `create_people_service(...)`。
- 这些入口虽然“能工作”，但会让 GUI 自己装配 runtime，重新把 Phase 4
  想收口的 library/session boundary 打散。
- 同时又不能粗暴把所有 compatibility fallback 降成 plain
  `PeopleService(root)`，因为 `PeopleDashboardWidget` 的 group common-photo
  cover 仍需要 asset-aware People service。

目标是：

- GUI runtime 主路径统一优先依赖 `LibraryManager.people_service`。
- pinned/query/cover/snapshot follow-up 不再在 runtime 入口中重建 bootstrap
  factory。
- 保留必要 compatibility fallback，但把它们压缩到非主路径，并明确哪些地方
  仍需 asset-aware factory。

## 2. 变更摘要

### 2.1 新增 People service resolver

- 新增 `gui/services/people_service_resolver.py`。
- 统一负责从 `LibraryManager.people_service` 解析当前 active 的 People
  service，并按 root 做匹配。
- runtime GUI 入口默认不再自己 fallback 到 bootstrap factory。

### 2.2 收口 GUI runtime People 入口

- `MainCoordinator`
  - 初始化与 `treeUpdated` 后的 rebinding 优先下发 bound session
    `people_service`。
  - 仅在无 bound service 时，才让下游兼容接口自行决定 fallback。
- `NavigationCoordinator`
  - pinned person/group 打开改为依赖 active `people_service`。
  - 无 bound service 时保守返回，不再自己重建 People runtime。
- `GalleryViewModel`
  - People snapshot commit 后的 cluster/group query 重建改走 active
    `people_service`。
- `AlbumTreeModel`
  - pinned person/group 的实时标题解析只在有 bound `people_service` 时才读
    summaries；否则保留 persisted label。
- `ContextMenuController`
  - person/group “Set as Cover” 判断与执行改走 active `people_service`。
  - 无 bound service 时，该动作会隐藏或失败可控。

### 2.3 收窄 compatibility fallback

- `PinnedItemsService`
  - 新增可注入的 People service getter。
  - active runtime 下，stale people/group pin 清理优先走 bound session
    service。
  - standalone/settings 路径仍允许 compatibility fallback。
- `PeopleDashboardWidget`
  - `set_library_root()` 继续保留 asset-aware `create_people_service(root)`
    fallback。
  - 保留该例外是为了维持 group common-photo cover；若降成 plain
    `PeopleService(root)`，group 会直接退化成拼图 fallback。
- `PlaybackCoordinator.set_people_library_root()`、
  `ManualFaceAddWorker`
  - 仅保留最小兼容 fallback，不再 import bootstrap factory 作为运行时主路径。

### 2.4 Guardrail

- `tools/check_layer_boundaries.py` / `tests/architecture/test_layer_boundaries.py`
  新增检查：
  - `gui/coordinators/`
  - `gui/services/`
  - `gui/ui/controllers/`
  - `gui/ui/models/`
  - `gui/viewmodels/`
  这些 runtime 入口不得再 import
  `iPhoto.bootstrap.library_people_service`。
- `gui/ui/widgets/people_dashboard_widget.py` 当前仍是明确的 compatibility
  例外，不在本轮 guardrail 禁止范围内。

## 3. 行为说明

- runtime 主路径下，People pinned/query/cover/snapshot follow-up 统一通过
  `LibraryManager.people_service` 收口。
- People sidebar pinned item 在没有 active People service 时不会再偷偷重建
  runtime；它会保守不执行，等待 library/session 正常绑定。
- People dashboard 在 compatibility 打开路径中仍能为 group 选择 common
  photo 作为 cover，不会因为本轮重构直接退化成拼图。
- 本轮不处理 Maps、Edit、temp-library E2E，也不改变 `PeopleIndexPort` /
  `PeopleService` 的 domain 行为。

## 4. 验证

在项目 `.venv` 下执行：

- `.venv/bin/python -m pytest tests/test_navigation_coordinator_cluster_gallery.py tests/gui/viewmodels/test_gallery_viewmodel.py tests/ui/controllers/test_context_menu_cover.py tests/test_album_tree_model.py tests/test_settings_manager.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/gui/widgets/test_people_dashboard_widget.py tests/architecture/test_layer_boundaries.py -q`
- `.venv/bin/python tools/check_architecture.py`

结果：

- 上述回归全部通过（`115 passed`）。
- `tools/check_architecture.py` 通过。
- 仍有既有的 pytest `Unknown config option: env` warning。
- 仍有既有的 legacy model shim / pairing deprecation warnings。

## 5. 下一步交接

1. 推进 Maps runtime availability / fallback，继续沿用
   `LocationTrashNavigationService` 作为临时 GUI seam，但不要把它视为最终
   `MapRuntimePort` 边界。
2. 推进 Edit sidecar：`.ipo` 读写、save/reset/export use case 与
   `EditSidecarPort`。
3. 补 `temp library` 端到端回归，覆盖 import / move / delete / restore /
   rescan 后用户状态保护。
