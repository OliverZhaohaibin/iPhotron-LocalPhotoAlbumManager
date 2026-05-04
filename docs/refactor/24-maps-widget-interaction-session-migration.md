# 24 - Maps Widget Interaction Session Migration

> **版本:** 1.0 | **日期:** 2026-05-02
> **状态:** 已完成
> **范围:** Phase 5 Maps widget factory / marker interaction session boundary

---

## 1. 背景与目标

承接 `23-maps-location-session-residual-migration.md` 的下一步交接。本轮继续处理
Maps residual，但不重写 native widget、tile renderer 或 Location/Trash transport；
目标是先把两个容易回流业务逻辑的点收住：

- full map 与 info-panel mini-map 各自直接选择 native / Python / legacy widget。
- `MarkerController` 在 GUI 层直接决定单资产 marker 打开 detail、cluster marker
  打开 gallery。

目标是：

- 新增 session-owned map interaction surface，让 marker routing 决策进入
  application/session boundary。
- 让 `MarkerController` 只保留 clustering、hit testing、thumbnail/city annotation
  与 raw payload 发射。
- 把 full map / mini map 的 concrete widget 构造选择集中到共享 GUI factory。
- 增加 guardrail，防止 `PhotoMapView` / `InfoLocationMapView` 重新直接导入
  concrete map widget modules。

## 2. 变更摘要

### 2.1 Session-bound map interaction surface

- `application/dtos.py`
  - 新增 `MapMarkerActivation`。
- `application/ports/runtime.py`
  - 新增 `MapInteractionServicePort`。
- 新增 `application/services/map_interaction_service.py`
  - `LibraryMapInteractionService.activate_marker_assets()` 统一处理 empty /
    single-asset / cluster marker payload。
- `LibrarySession` / `RuntimeContext` / `LibraryManager`
  - 新增 `map_interactions` / `map_interaction_service` bind-unbind 链路。

### 2.2 GUI marker routing 收口

- `MarkerController`
  - `handle_marker_click()` 现在只发出 raw marker assets。
  - 不再直接决定 `assetActivated` 或 `clusterActivated`。
- `PhotoMapView`
  - 新增 `map_interaction_service` 注入与 `set_map_interaction_service()`。
  - raw marker assets 先交给 session interaction surface，再继续发出既有
    `assetActivated` / `clusterActivated` signal，保持 `MainCoordinator` 兼容。
- `Ui_MainWindow` / `MainCoordinator`
  - 下发 `map_runtime` 时同步下发 `map_interaction_service`。

### 2.3 Shared map widget factory

- 新增 `gui/ui/widgets/map_widget_factory.py`
  - 集中处理 `MapRuntimeCapabilities`、package root、native/Python/legacy backend
    fallback、OpenGL probe 与 diagnostics。
- `PhotoMapView`
  - 改为通过 `create_map_widget()` 构造底层 map widget。
- `InfoLocationMapView`
  - 复用同一 factory primitive，并保留 mini-map 侧测试可 patch 的 wrapper。
- `tools/check_layer_boundaries.py`
  - 新增检查：`photo_map_view.py` / `info_location_map.py` 不得直接导入
    `maps.map_widget.map_gl_widget`、`map_widget`、`native_osmand_widget`、
    `qt_location_map_widget`。

## 3. 行为说明

- 用户点击 Location marker 的外部行为不变：单资产 marker 仍打开 detail，
  cluster marker 仍打开 cluster gallery。
- `.ipo`、scan、People、Location query、native OsmAnd runtime 与 tile backend
  没有 schema 或二进制层面的变更。
- `PhotoMapView` / `InfoLocationMapView` 仍负责 Qt event filter、overlay/pin
  绘制、drag cursor、tooltip 和 widget lifecycle；本轮只收口 widget 选择与
  marker routing 决策。
- 本轮不需要重新编译 OsmAnd helper。

## 4. 验证

在项目 `.venv` 下执行：

- `.venv/bin/python -m pytest tests/application/test_map_interaction_service.py tests/application/test_runtime_context.py tests/test_marker_controller_place_labels.py tests/test_photo_map_view.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/architecture/test_layer_boundaries.py -q`
- `.venv/bin/python -m pytest tests/test_info_panel.py -k "location_map or set_location_capability or map_runtime" -q`
- `.venv/bin/python tools/check_architecture.py`

结果：

- focused map/session regressions 通过（`61 passed`）。
- info-panel map-related regressions 通过（`15 passed`）。
- `tools/check_architecture.py` 通过。
- 仍有既有的 pytest `Unknown config option: env` warning。
- 仍有既有的 legacy model shim / pairing deprecation warnings。

## 5. 下一步交接

1. 若继续推进 Maps，应优先处理 Qt widget event filtering、overlay/pin 绘制、
   drag cursor 与 marker hit testing 仍留在 GUI 层的问题。
2. 继续补 `temp library` 端到端回归，覆盖 import / move / delete / restore /
   rescan 后用户状态保护。
