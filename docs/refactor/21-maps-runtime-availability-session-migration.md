# 21 - Maps Runtime Availability Session Migration

> **版本:** 1.0 | **日期:** 2026-05-01  
> **状态:** 已完成  
> **范围:** Phase 5 Maps runtime capability seam（session-bound availability / fallback）

---

## 1. 背景与目标

承接 `20-people-gui-session-residual-migration.md` 的下一步交接。本轮不碰
Edit sidecar，而是先把 Maps 这一侧长期悬空的 runtime seam 补齐。

迁移前的实际问题是：

- `MapRuntimePort` 只有协议名义存在，active session 并没有真正暴露 maps
  runtime surface。
- `PhotoMapView`、`InfoLocationMapView`、`PlaybackCoordinator` 分别自己探测
  backend / GL / search extension，可用性判断分叉。
- mac 的 GL 上下文探测与 native OsmAnd widget probe 没有在应用层面被明确
  固化，后续很容易被 Linux/Windows 的直觉改坏。
- `PlaybackCoordinator` 仍残留一个与本轮主线无关但真实存在的回归：
  直接 import `iPhoto.bootstrap.library_people_service`，导致
  `tools/check_architecture.py` 与文档结论不一致。

目标是：

- 让 Maps runtime capability 通过 `LibrarySession.maps` 进入 active session。
- 让地图显示 fallback 与 Assign Location capability 共用一份 runtime
  snapshot，而不是 GUI 各自探测。
- 明确保留 mac strict GL 规则，同时让 native widget probe 与 Python GL
  probe 解耦。
- 顺手把 `PlaybackCoordinator` 的 People bootstrap import regression 修掉，
  恢复 architecture gate。

## 2. 变更摘要

### 2.1 Session-bound maps runtime surface

- `application/ports/runtime.py`
  - 新增 `MapRuntimeCapabilities`。
  - `MapRuntimePort` 不再只有 `is_available()`，增加 `capabilities()`。
- 新增 `infrastructure/services/map_runtime_service.py`
  - 统一计算 `preferred_backend`、`python_gl_available`、
    `native_widget_available`、`osmand_extension_available`、
    `location_search_available` 与状态文案。
- `LibrarySession`
  - 新增 `maps` surface，默认由 `SessionMapRuntimeService` 提供。
- `RuntimeContext` / `LibraryManager`
  - 新增 `bind_map_runtime()` / `map_runtime`，让 active session 的 maps
    runtime 可被 GUI runtime 获取。

### 2.2 GUI runtime 改为消费 runtime snapshot

- `PlaybackCoordinator`
  - Assign Location capability 不再直接调用
    `has_usable_osmand_search_extension()`。
  - 改为读取当前 bound `map_runtime.capabilities().location_search_available`。
- `PhotoMapView`
  - 支持消费 injected `map_runtime`，并按 capability snapshot 选择
    native / osmand-python / legacy backend。
  - 未注入 runtime 时继续保留原有本地探测，避免兼容路径和既有测试直接回归。
- `InfoLocationMapView`
  - mini-map backend 选择与主地图页共享同一 capability seam。
- `InfoPanel` / `Ui_MainWindow` / `MainCoordinator`
  - 增加 maps runtime forwarding，把 session-owned runtime 下发到主地图页、
    mini-map 与 detail coordinator。

### 2.3 mac 特殊考虑

- mac 继续使用 strict GL probe：
  - `QOffscreenSurface.create()`
  - `QOpenGLContext.create()/isValid()`
  - `makeCurrent(surface)`
  - `glGetString(GL_VERSION)`
  任一步失败，Python GL 都视为 unavailable。
- native OsmAnd widget probe 与 Python GL probe 保持独立：
  - Python GL 失败不自动判死 native widget。
  - 只有显式禁用 OpenGL 或 native runtime probe 自己失败时，才禁用 native
    widget 路径。
- 无 `QGuiApplication` 的 headless/runtime-context 场景不再硬做 GL probe，
  统一返回保守 capability snapshot，避免测试和无 GUI 入口直接崩溃。

### 2.4 顺手修复的 architecture regression

- `PlaybackCoordinator.set_people_library_root()` 不再 import
  `create_people_service(...)`。
- active runtime 下优先复用 `LibraryManager.people_service`。
- 无 bound service 时仅保留 plain `PeopleService(root)` 级别的最小兼容 fallback。

## 3. 行为说明

- Maps capability 现在是 session surface 的一部分，但 `PhotoMapView` /
  `InfoLocationMapView` 仍保留本地 fallback 构造逻辑，因此旧的无注入 widget
  构造路径不会因本轮直接失效。
- `PlaybackCoordinator` 与 mini-map 现在都能消费同一份 maps runtime snapshot，
  但 `LocationTrashNavigationService` 仍只是临时 GUI transport seam，不是最终
  `MapRuntimePort` 边界。
- 本轮没有把 widget 构造、marker interaction 或 search result routing 全部
  下沉到 application；它只先收口 runtime availability / fallback / capability
  判断。
- 本轮不处理 Edit sidecar，也不补 temp-library E2E。

## 4. 验证

在项目 `.venv` 下执行：

- `.venv/bin/python -m pytest tests/test_map_runtime_service.py tests/application/test_runtime_context.py tests/gui/coordinators/test_playback_coordinator.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/test_photo_map_view.py tests/test_ui_main_window_map_stack.py -q`
- `.venv/bin/python -m pytest tests/test_info_panel.py -k "location_map or set_location_capability" -q`
- `.venv/bin/python tools/check_architecture.py`

结果：

- focused maps/runtime regressions 通过（`80 passed` + `10 passed`）。
- `tools/check_architecture.py` 通过。
- 仍有既有的 pytest `Unknown config option: env` warning。
- 仍有既有的 legacy model shim / pairing deprecation warnings。

额外说明：

- 整个 `tests/test_info_panel.py` 在 headless Qt 环境下仍存在与本轮改动无直接
  对应的 event-filter cleanup segfault 风险，因此本轮只把受影响的 map-related
  子集作为固定回归锚点。

## 5. 下一步交接

1. 推进 Edit sidecar：`.ipo` 读写、save/reset/export use case 与
   `EditSidecarPort` 的 session/runtime 收口。
2. 若继续推进 Maps，优先处理 widget 构造与 event/query routing 仍停留在 GUI
   层的问题，并逐步淡化 `LocationTrashNavigationService` 这个临时 seam。
3. 补 `temp library` 端到端回归，覆盖 import / move / delete / restore /
   rescan 后用户状态保护。
