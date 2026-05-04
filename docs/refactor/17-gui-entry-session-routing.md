# 17 - GUI 入口 Session 路由收口说明

> Date: 2026-05-01

## 目标

本轮继续承接 `16-gui-file-operation-command-migration.md`，目标不是再新增一层
抽象，而是把 GUI 剩余的 `open/rescan/pair` 入口和 media-load-failure
修复从兼容入口进一步收口到现有 session-owned service。

具体来说，本轮要解决两类残留问题：

- `AppFacade.open_album()` / `rescan_current_async()` 仍保留部分扫描装配与分支。
- `MainCoordinator._handle_media_load_failed()` 仍直接删 repository row，并直接
  调用 `app.pair()`，属于 coordinator 持有 persistence 规则的遗留越界。

## 本轮改动

- 新增 `LibraryUpdateService.prepare_album_open()`。
  - 统一通过 active session `scan_service.prepare_album_open(...)` 处理 GUI
    album open 前的索引准备。
  - 返回 `AlbumOpenRouting`，明确告知 GUI 调用方是否需要额外触发异步 rescan。
  - 保留 `autoscan=False`、`hydrate_index=False` 的 lazy open 行为。

- 收口 `AppFacade` 的 open/rescan 路由。
  - `AppFacade.open_album()` 现在只负责：
    - `Album.open(root)`
    - 更新 `current_album`
    - 发 `albumOpened`、`loadStarted`、`loadFinished`
    - 在 service 明确要求时触发异步 rescan
  - `AppFacade.rescan_current_async()` 不再自己判断
    `LibraryManager.start_scanning(...)`，统一转发给
    `LibraryUpdateService.rescan_album_async()`。

- 收口异步扫描入口。
  - `LibraryUpdateService.rescan_album_async()` 在有 active `LibraryManager`
    时，直接走 session-bound `start_scanning(...)`。
  - 仅在没有 active library manager 的兼容调用路径里，继续保留
    `ScannerWorker` fallback。

- 新增缺失媒体修复命令。
  - `LibraryAssetLifecycleService.repair_missing_asset(path)` 通过 repository
    port 做 best-effort 修复：
    - 判断坏行是否仍存在
    - 删除坏行
    - 尝试重建对应 scope 的 Live Photo pairing
  - `LibraryUpdateService.handle_media_load_failure(path)` 负责调用该命令并发出
    `indexUpdated` / `linksUpdated`。

- 移除 `MainCoordinator` 对 `iPhoto.app` 的运行时依赖。
  - `MainCoordinator._handle_media_load_failed()` 不再直接访问 repository，
    也不再直接调用 `app.pair()`。
  - 该方法现在只保留用户可见错误提示和 collection reload 这类
    presentation 行为，索引/链接修复交给 `facade.library_updates`。

- 新增架构 guardrail。
  - `tools/check_layer_boundaries.py` 现在会阻止 GUI runtime 导入 `iPhoto.app`。

## 行为说明

- `AppFacade`、`GalleryViewModel`、`DialogController` 的公开调用方式没有变化。
- `LibraryUpdateService` 继续充当 GUI scan/pair/refresh 的编排入口；
  本轮没有新增新的 GUI orchestration service。
- media-load-failure 修复仍是 best-effort：
  - 继续先向用户显示解码/文件缺失错误。
  - 只有索引里确实存在对应坏行时，才会做 row removal 与 pairing repair。
  - pairing repair 失败只记 warning，不额外弹第二个错误框。

## 验证

在项目 `.venv` 下执行：

- `.venv/bin/python -m pytest tests/test_app_facade_session_open.py tests/services/test_library_update_service_global_db.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/architecture/test_layer_boundaries.py -q`
- `.venv/bin/python tools/check_architecture.py`

结果：

- 相关新增/调整测试全部通过。
- architecture check 通过。
- 仍有既有的 pytest `Unknown config option: env` warning。
- 仍有既有的 legacy model shim / pairing deprecation warnings。

## 下一步交接

1. 继续把 `gui/services/*` 中残留的 durable orchestration 收口到 session-owned
   command/query surface，进一步压缩 `AppFacade` 与 coordinator 里的分支。
2. 增加 `temp library` 端到端回归，覆盖 import / move / delete / restore /
   rescan 后用户状态保护。
3. 继续推进 Phase 5：Maps availability/fallback 与 Edit sidecar port。
