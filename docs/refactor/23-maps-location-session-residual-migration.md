# 23 - Maps Location Session Residual Migration

> **版本:** 1.0 | **日期:** 2026-05-02
> **状态:** 已完成
> **范围:** Phase 5 Maps Location query / Trash cleanup session boundary

---

## 1. 背景与目标

承接 `22-edit-sidecar-session-migration.md` 的下一步交接。本轮回到 Maps
residual，不处理 native widget 构造或 marker interaction，而是先把 Location
地理资产查询与 Recently Deleted cleanup 从 GUI/`LibraryManager` 临时入口继续
收口到 active session surface。

迁移前的主要问题是：

- `LocationTrashNavigationService` 虽然已经承接 Qt background transport，
  但 geotagged assets 仍通过 `library.get_geotagged_assets()` 读取。
- `GalleryViewModel.handle_location_scan_chunk()` 仍直接导入 legacy
  `iPhoto.library.geo_aggregator` helper。
- Recently Deleted cleanup 在 GUI transport 中仍以 `library.cleanup_deleted_index()`
  作为主调用形态，session lifecycle surface 只是间接 fallback。

目标是：

- 新增 session-owned Location query surface，作为 Maps 地理资产读取主入口。
- 让 Location/Trash GUI service 只保留 Qt task、request serial、signals 与
  cleanup throttle。
- 保留 legacy `GeoAggregatorMixin` / `LibraryManager.get_geotagged_assets()`
  兼容入口，但 active session 下委托到新 surface。
- 新增 guardrail，阻止 GUI runtime 重新导入 legacy location helper。

## 2. 变更摘要

### 2.1 Session-bound Location surface

- `application/dtos.py`
  - 新增 application-level `GeotaggedAsset` DTO。
- `application/ports/runtime.py`
  - 新增 `LocationAssetServicePort`，包含：
    `list_geotagged_assets()`、`asset_from_row()`、`invalidate_cache()`。
- 新增 `application/services/location_asset_service.py`
  - 提供纯 row-to-`GeotaggedAsset` 转换 helper。
- 新增 `bootstrap/library_location_service.py`
  - 统一处理 geotagged rows 读取、Live Photo hidden motion 过滤、
    absolute-path 去重、排序和缓存失效。

### 2.2 Active session 绑定链路

- `LibrarySession`
  - 新增 `locations` surface，默认由 `LibraryLocationService` 提供。
- `RuntimeContext`
  - `open_library()` / `close_library()` 负责 bind/unbind `locations`。
- `LibraryManager`
  - 新增 `bind_location_service()` / `location_service`。
  - `invalidate_geotagged_assets_cache()` 同步 invalidates bound location
    surface。

### 2.3 GUI transport 与 legacy compatibility

- `LocationTrashNavigationService`
  - 地理资产后台加载优先调用
    `library.location_service.list_geotagged_assets()`。
  - Recently Deleted cleanup 优先调用
    `library.asset_lifecycle_service.cleanup_deleted_index(deleted_root)`。
  - 无 session surface 时保留旧 `LibraryManager` compatibility fallback。
- `GalleryViewModel`
  - scan chunk 增量转换优先调用
    `library.location_service.asset_from_row(row)`。
  - fallback 改用 application 层纯 helper，不再导入
    `iPhoto.library.geo_aggregator`。
- `library.geo_aggregator`
  - 保留 `GeotaggedAsset` re-export 与 `get_geotagged_assets()` compatibility。
  - active session 下委托给 `LibraryLocationService`。

### 2.4 Guardrail

- `tools/check_layer_boundaries.py` / `tests/architecture/test_layer_boundaries.py`
  新增检查：
  - GUI coordinators/services/controllers/models/viewmodels 不得再 import
    `iPhoto.library.geo_aggregator`。

## 3. 行为说明

- 地图页打开 Location 时，GUI transport 仍异步加载并使用 request serial 防止
  旧请求覆盖新请求，但实际业务查询已由 active session surface 提供。
- scan chunk 到地图 snapshot 的增量更新仍保持原行为：有效 GPS row upsert，
  hidden Live Photo motion row 或无效 row remove。
- `LibraryManager.get_geotagged_assets()` 仍可被旧路径调用；在 active session
  下它只是委托到 `location_service`。
- 本轮不改变 map widget 构造、marker clustering、native helper 或 search
  result routing。

## 4. 验证

在项目 `.venv` 下执行：

- `.venv/bin/python -m pytest tests/application/test_library_location_service.py tests/application/test_runtime_context.py tests/gui/viewmodels/test_gallery_viewmodel.py tests/gui/services/test_location_trash_navigation_service.py tests/test_navigation_coordinator_cluster_gallery.py tests/test_library_geotagged_assets.py tests/test_library_manager_cleanup.py tests/architecture/test_layer_boundaries.py -q`
- `.venv/bin/python tools/check_architecture.py`

结果：

- focused location/runtime regressions 通过（`67 passed`）。
- `tools/check_architecture.py` 通过。
- 仍有既有的 pytest `Unknown config option: env` warning。
- 仍有既有的 legacy model shim / pairing deprecation warnings。

## 5. 下一步交接

1. 若继续推进 Maps，应优先处理 map widget 构造、marker interaction 与
   event/query routing 仍停留在 GUI 层的问题。
2. 继续补 `temp library` 端到端回归，覆盖 import / move / delete / restore /
   rescan 后用户状态保护。
