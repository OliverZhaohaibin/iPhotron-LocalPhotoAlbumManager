# 31 - Large Library Performance Scan/Scroll Events Handoff

> **版本:** 1.0 | **日期:** 2026-05-31
> **状态:** 已完成
> **范围:** scan batch coalescing, thumbnail backfill completion events, ready-query EXPLAIN gates

## 1. 背景与目标

本轮承接 `30-large-library-performance-scan-scroll-handoff.md` 的下一步清单，继续降低扫描发布和滚动浏览的 UI 刷新压力：

- 将 `ScanBatchCommitted` 的 Gallery 消费点从 store 直连提升到 Qt model adapter。
- 用 150ms 明确合并窗口减少快速 scan/backfill batch 触发的重复 visible-window reload。
- 将旧库 stale thumbnail backfill 完成通知从 500ms polling 改为事件发布。
- 给 ready-thumbnail collection query 增加可持续的 `EXPLAIN QUERY PLAN` 回归断言。

## 2. 变更摘要

- Scan batch coalescing
  - `MainCoordinator` 将 runtime/facade 的 `scanBatchCommitted` 连接到 `GalleryListModelAdapter.handle_scan_batch()`。
  - `GalleryListModelAdapter` 新增 150ms single-shot timer，先记录 batch，timer 到期后统一 flush 当前 visible window。
  - adapter 内部用 Qt queued signal 处理跨线程 batch，避免后台线程直接触碰 UI/store 状态。
  - `GalleryCollectionStore` 增加 `record_scan_chunk()`、`record_scan_batch()`、`flush_pending_scan_refresh()`；旧 `handle_scan_batch()` 仍保持立即刷新兼容行为。

- Thumbnail backfill completion
  - `LibraryAssetQueryService` 新增 lightweight `thumbnail_backfill_completed` callback signal。
  - 后台 stale backfill 成功更新 ready row 后发布 `ScanBatchCommitted(job_id="thumbnail-backfill:...")`。
  - failed thumbnail 仍写入 failed/error 状态，但不会进入 committed rows。
  - `GalleryListModelAdapter` rebind query service 时会断开旧 completion signal 并连接新 signal。
  - 旧 `_flush_pending_thumbnail_backfill()` 保留为 no-op 兼容方法，不再由 500ms polling 驱动刷新。

- Ready-query performance gate
  - `QueryBuilder.build_collection_where()` 改用可索引字段：`is_deleted = 0`、`thumbnail_state = ?`、`has_gps = ?`。
  - `row_mapper` 对 `Recently Deleted` 路径默认写入 `is_deleted=1`，普通 collection 不再需要路径前缀排除条件。
  - `tests/performance/test_refactor_performance_baseline.py` 覆盖 All Photos、album、favorites、videos、map ready-thumbnail 查询计划，断言使用 visible/gps indexes 且不出现 temp sort。

## 3. 验证记录

已运行：

```bash
.venv/bin/pytest tests/application/test_library_asset_query_service.py tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/performance/test_refactor_performance_baseline.py
.venv/bin/pytest tests/cache/test_index_store_features.py tests/test_scanner_adapter.py tests/application/test_library_scan_service.py tests/test_asset_grid_scroll.py tests/test_thumbnail_cache_service.py tests/library/test_scanner_worker.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py
```

当前结果：

- Focused scan/scroll/performance pytest：58 passed。
- Wider cache/scan/gallery/coordinator pytest：67 passed。
- pytest 仍有既有警告：unknown config option `env`。

## 4. 下一步交接

1. 给更多 GUI 消费方接入 `scanBatchCommitted`，确认 location、map、album tree 等路径不再依赖旧 chunk payload。
2. 评估旧 `scanChunkReady` 下线条件；下线前保持 ready-only 兼容信号。
3. 增加 opt-in 100k/1M synthetic scroll benchmark。
4. 增加 scan visible publish latency benchmark，覆盖从 thumbnail ready 到 Gallery 可见刷新。
5. 继续细化 scan job stage timing：discover、metadata、thumbnail、db commit、visible publish、derived jobs。

## 5. 注意事项

- backfill 仍只面向旧库 `stale` recovery，不应在启动路径做全库同步回填。
- `WindowResult` 和 `PageResult` 仍保持 dict rows，未做 typed DTO 迁移。
- `ScanBatchCommitted.rows` 必须保持 ready-only。
- ready collection query 现在依赖迁移/row mapping 规范化 `is_deleted`、`thumbnail_state`、`has_gps`。
- 不要恢复已删除的 `docs/requirements/INITIAL_SCAN_LARGE_LIBRARY_STABILITY.md`。
