# 25 - Maps GUI Transport Overlay Residual Migration

> **版本:** 1.0 | **日期:** 2026-05-02
> **状态:** 已完成
> **范围:** Phase 5 Maps GUI transport / overlay residual 收口

---

## 1. 背景与目标

承接 `24-maps-widget-interaction-session-migration.md` 的下一步交接。本轮不再继续
扩 `MapRuntimePort`、`MapInteractionServicePort` 或新的 session surface，而是回到
GUI residual：`PhotoMapView` 与 `InfoLocationMapView` 仍各自维护一套 map widget
`event_target()` 绑定、post-render painter / QWidget overlay fallback、以及
shutdown 时的 transport 清理逻辑。

迁移前的主要问题是：

- full map / mini-map 分别自己维护 event filter 注册、解绑和 application-level
  fallback filter，重复逻辑容易回流。
- overlay / pin 的 post-render painter attach-detach 与 QWidget fallback 分支散落在
  各自 widget 内，rebuild / shutdown 清理点不统一。
- `PhotoMapView` 仍在 view 内自行拼 marker pointer-hit 链路，而不是把点击位置先
  交回 `MarkerController`。

目标是：

- 新增共享 GUI helper，统一管理 map widget event surface 与 overlay attachment。
- 让 `PhotoMapView` / `InfoLocationMapView` 只保留各自的表现层语义，不再重复写
  transport glue。
- 把 marker pointer-hit 入口收口到 `MarkerController`，view 只负责转发点击位置和
  兼容 signal。

## 2. 变更摘要

### 2.1 Shared GUI helper

- 新增 `gui/ui/widgets/map_widget_support.py`
  - `MapEventSurfaceBridge`
    - 统一解析 map widget 本体与 `event_target()`。
    - 统一安装/移除 owner event filter。
    - 按需安装/移除 application-level fallback filter。
  - `MapOverlayAttachment`
    - 统一探测 `supports_post_render_painter()`。
    - 统一 attach/detach post-render painter。
    - 统一启用 QWidget overlay fallback 并同步 geometry。

### 2.2 PhotoMapView residual 收口

- `gui/ui/widgets/photo_map_view.py`
  - 新增 `MapEventSurfaceBridge` / `MapOverlayAttachment` 成员。
  - `_build_map_widget()` 不再直接安装 event filter 或手写 painter fallback 分支，
    改由共享 helper 承接。
  - `eventFilter()` 中的 marker pointer press 现在优先调用
    `MarkerController.handle_pointer_press(position)`。
  - `closeEvent()` / `_teardown_map_widget()` 不再自己拼 teardown 细节，改为
    helper 统一清理 event filter 与 post-render painter。

- `gui/ui/widgets/marker_controller.py`
  - 新增 `handle_pointer_press(position)`，内部完成 hit testing +
    `markerActivated` 发射。
  - `handle_marker_click(cluster)` 继续保留 raw assets 发射语义，兼容现有
    `PhotoMapView._on_marker_activated()` 路由。

### 2.3 InfoLocationMapView residual 收口

- `gui/ui/widgets/info_location_map.py`
  - `_install_map_event_filters()` / `_remove_map_event_filters()` 改为共享 bridge
    的薄包装。
  - pin painter attach/detach 改走 `MapOverlayAttachment`。
  - mini-map drag cursor 仍由 widget 自己决定何时启停，但 cursor targets 现在由
    bridge 提供，不再自己维护 event target 收集逻辑。

## 3. 行为说明

- full map 的 marker 点击外部行为不变：单资产仍走 detail，cluster 仍走 gallery。
- full map / mini-map 的 widget backend 选择、runtime diagnostics、native fallback
  与 session-bound `map_runtime` / `map_interaction_service` 行为不变。
- mini-map 的 rounded mask、square sizing、pin 投影、drag cursor 行为不变。
- 本轮没有新增 application-level public API，也没有新增 architecture guardrail。
- overlay/pin 绘制与 drag cursor 策略仍然是 GUI 职责；本轮只是把重复 transport glue
  抽到共享 helper，并不把这些表现层能力下沉到 application/runtime。

## 4. 验证

在项目 `.venv` 下执行：

- `.venv/bin/python -m pytest tests/test_photo_map_view.py tests/test_info_panel.py tests/test_map_drag_cursor.py tests/test_marker_controller_place_labels.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/architecture/test_layer_boundaries.py -q`
- `.venv/bin/python tools/check_architecture.py`

结果：

- focused map/widget regressions 通过（`104 passed`）。
- `tools/check_architecture.py` 通过。
- 仍有既有的 pytest `Unknown config option: env` warning。
- 仍有既有的 legacy model shim / pairing deprecation warnings。

## 5. 下一步交接

1. 优先补 `temp library` 端到端回归，覆盖 import / move / delete / restore /
   rescan 后用户状态保护。
2. 若后续再回到 Maps，只处理新暴露问题；当前不再主动扩新的
   session/runtime boundary。
