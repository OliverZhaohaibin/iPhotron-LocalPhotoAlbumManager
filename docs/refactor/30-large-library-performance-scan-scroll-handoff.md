# 30 - Large Library Performance Scan/Scroll Follow-up Handoff

> **版本:** 1.0 | **日期:** 2026-05-31
> **状态:** 已完成
> **范围:** scan batch transport, Gallery scan refresh/backfill, thumbnail queue throttling

## 1. 背景与目标

本轮承接 `29-large-library-performance-phase3-4-handoff.md` 的下一步清单，继续收敛扫描实时发布和滚动浏览时的压力点：

- 将 ready-only scan rows 从旧 `scanChunkReady(Path, list)` 兼容信号升级到显式 `ScanBatchCommitted` transport。
- 让 Gallery 直接消费 batch DTO，并在旧库 `stale` row 场景优先请求当前可见窗口附近的缩略图回填。
- 给 `ThumbnailCacheService` 增加服务内限流、优先级和失败冷却，避免滚动时无限制提交生成任务。
- 给 collection window 的 count/revision 增加小缓存，减少连续窗口读取中的重复 metadata 查询。

## 2. 变更摘要

- Scan transport
  - `ScanLibraryResult` 增加 `scan_job_id`。
  - `ScanLibraryUseCase` 在 merge chunk 后构造 `ScanBatchCommitted`，rows 只包含 ready 且带 thumbnail payload 的资产。
  - `scan_events.batch_committed` payload 记录 requested rows、merged rows、ready rows、commit elapsed ms 和 collection revision。
  - `ScannerSignals`、`LibraryRuntimeController`、`LibraryUpdateService`、`AppFacade` 新增 `scanBatchCommitted = Signal(object)`。
  - `MainCoordinator` 优先把 `scanBatchCommitted` 接入 `GalleryCollectionStore.handle_scan_batch()`；旧 `scanChunkReady` 继续保留。

- Gallery/scroll path
  - `GalleryCollectionStore.handle_scan_batch()` 会使用 batch revision，并复用已有 ready-only scan refresh 判断。
  - visible window 查询为空时，只调度 stale thumbnail backfill，不在 GUI 调用栈内生成缩略图。
  - `GalleryListModelAdapter` 通过 500ms timer 轮询后台 backfill 完成情况，再刷新当前窗口。
  - 每个 backfill window 只请求一次；被更大窗口覆盖的小窗口不会重复请求。
  - `DecorationRole` 以 `priority="visible"` 请求 512 缩略图。

- Repository/query
  - `read_collection_window()` 复用 per-query count/revision cache。
  - 写入、scan merge、favorite、thumbnail readiness 更新会统一清空 collection anchor/meta cache。
  - 新增 `read_thumbnail_backfill_candidates()` 和 `update_thumbnail_ready()`。
  - ready collection query 明确要求 `micro_thumbnail` 或 `thumb_cache_key`。

- Thumbnail queue
  - `LibraryAssetQueryService.request_thumbnail_backfill()` 现在只把 stale rows 放入单 worker 后台队列。
  - `LibrarySession.shutdown()` 会关闭 query service 的 backfill executor。
  - `ThumbnailCacheService.get_thumbnail()` 兼容旧调用，并新增 `priority` keyword。
  - miss 后进入服务内 priority queues：`visible > normal > low`。
  - 默认最多 2 个 active generation jobs。
  - pending key 去重；`cancel_pending_except()` 可丢弃不再相关的 queued work。
  - 生成失败会进入 60s 内存冷却；`invalidate()` 会清除对应冷却和 pending 状态。

## 3. 验证记录

已运行：

```bash
.venv/bin/pytest tests/application/test_library_scan_service.py tests/cache/test_index_store_features.py tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/test_thumbnail_cache_service.py tests/library/test_scanner_worker.py
.venv/bin/pytest tests/cache/test_index_store_features.py tests/test_scanner_adapter.py tests/application/test_library_scan_service.py tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/test_asset_grid_scroll.py tests/performance/test_refactor_performance_baseline.py tests/test_thumbnail_cache_service.py tests/application/test_library_asset_query_service.py tests/library/test_scanner_worker.py
.venv/bin/pytest tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/services/test_library_update_service_global_db.py
.venv/bin/pytest tests/cache/test_index_store_features.py tests/test_scanner_adapter.py tests/application/test_library_scan_service.py tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/test_asset_grid_scroll.py tests/performance/test_refactor_performance_baseline.py tests/test_thumbnail_cache_service.py tests/application/test_library_asset_query_service.py tests/library/test_scanner_worker.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/services/test_library_update_service_global_db.py
python3 -m compileall -q src/iPhoto
git diff --check
QT_QPA_PLATFORM=offscreen .venv/bin/python -m iPhoto.gui.main
```

当前结果：

- Phase 3/4 + 本轮 + GUI/service signal 边界 pytest：129 passed。
- `compileall` passed。
- `git diff --check` passed。
- offscreen GUI 启动通过启动绑定阶段，成功进入 saved library `/Users/haibinzhao/Documents/testbase` 的 All Photos selection 流程；随后手动结束测试进程。
- pytest 仍有既有警告：unknown config option `env`。

## 4. 下一步交接

1. 将 scan batch coalescing 从当前 store 级 pending refresh 进一步升级为 Qt timer 驱动的 100-250ms 明确合并窗口。
2. 将后台 thumbnail backfill 完成通知从 timer polling 升级为明确的 Qt/application event，并在完成后发布 `ScanBatchCommitted`。
3. 给 `scanBatchCommitted` 增加更多 GUI 消费方，最终再考虑下线旧 `scanChunkReady`。
4. 扩展性能门禁：
   - ready thumbnail collection query 的 `EXPLAIN QUERY PLAN` 断言。
   - opt-in 100k/1M synthetic scroll benchmark。
   - scan visible publish latency benchmark。

## 5. 注意事项

- 旧 `scanChunkReady` 当前仍保留并继续发 ready-only rows。
- `WindowResult` 和 `PageResult` 仍保持 dict rows，未做 typed DTO 迁移。
- backfill 只面向旧库 `stale` recovery，不应在启动路径做全库同步回填。
- 不要恢复已删除的 `docs/requirements/INITIAL_SCAN_LARGE_LIBRARY_STABILITY.md`。
