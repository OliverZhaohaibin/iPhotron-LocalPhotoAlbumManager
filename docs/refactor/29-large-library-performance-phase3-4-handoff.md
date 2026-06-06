# 29 - Large Library Performance Phase 3/4 Handoff

> **版本:** 1.0 | **日期:** 2026-05-31
> **状态:** 已完成
> **范围:** Phase 3 thumbnail-ready visible rows + Phase 4 scan job/event baseline

---

## 1. 背景与目标

本轮承接 `large_library_performance_refactor_log.md` 中 Phase 0-2 的 SQL-first
collection 和 Gallery 去全量化基础，继续处理扫描与滚动体验中的两个高风险点：

- 普通媒体 grid 仍可能消费缺少缩略图 payload 的 `ready` row，从而出现空白/黑格。
- Gallery 可见窗口刷新仍存在先 count 再 fetch 的重复查询路径，快速滚动时容易放大 DB 压力。

目标是先完成一条可运行的主线，而不是一次性重写完整扫描系统：

- `thumbnail_state='ready'` 必须代表“可以显示”。
- 扫描发布到 GUI 的 chunk 只包含 ready rows。
- Gallery 优先使用一次性 `WindowResult` 加载窗口。
- scan job/event schema 先落库，为后续可观察、可取消、可恢复扫描打底。

## 2. 变更摘要

- Thumbnail-ready 契约
  - 新增 `ThumbnailState`、`ThumbnailReadyResult`。
  - `CollectionQuery(min_thumbnail_state='ready')` 现在同时要求
    `micro_thumbnail` 或 `thumb_cache_key`。
  - 旧库中缺 payload 的 ready row 会被迁移/写入为 `stale`。
  - 扫描缩略图失败会写 `failed + thumb_error`，不会作为普通 grid row 发布。

- Scan job/event baseline
  - 新增 `scan_jobs`、`scan_events` 表。
  - repository 增加 `create_scan_job()`、`update_scan_job_stage()`、
    `append_scan_event()`。
  - `LibraryScanService.scan_album()` 记录基础阶段、完成状态和 ready/failed 计数。
  - `ScanLibraryUseCase` 仍 merge 全量 batch，但 `chunk_callback` 只收到 ready rows。
  - `ScannerWorker.SCAN_CHUNK_SIZE` 从 10 调整为 500。

- Gallery 滚动路径
  - `LibraryAssetQueryService` 新增 `read_query_asset_window(root, query, first, limit)`。
  - `GalleryCollectionStore` 优先使用 window API，一次获得 rows、total_count、
    collection_revision。
  - `GalleryListModelAdapter.prioritize_rows()` 增加 16ms 合并，快速滚动时合并可见范围。
  - `scan_row_to_dto()` 将 row 内 `micro_thumbnail` bytes 解码成 `QImage`，供 delegate
    在 512 缩略图未就绪时 fallback 绘制。
  - `DecorationRole` 始终通过 `ThumbnailCacheService.get_thumbnail()` 获取或调度
    512x512 缩略图；micro thumbnail 不再短路全尺寸缩略图生成。

## 3. 关键行为说明

- 普通 collection 不再展示 `pending`、`failed`、`stale` row。
- `ready` 但没有缩略图 payload 的 row 会被视为 `stale`。
- Scanner 可以持久化 failed row；这些 row 用于后续诊断/重试，不进入普通 Gallery。
- GUI 的旧 `scanChunkReady(Path, list)` 和 `scanFinished(Path, bool)` 仍保留，避免本轮扩大 Qt transport 迁移范围。
- Mid-scroll 收到扫描完成后，Gallery 会保持当前可见窗口稳定，只更新 count/revision；不会强制跳回顶部新资产。
- Gallery 缩略图显示分两层：`DecorationRole` 负责 512x512 `QPixmap` 命中/调度，
  `Roles.MICRO_THUMBNAIL` 只作为等待期间的低清 fallback。

## 4. 验证记录

已运行：

```bash
.venv/bin/pytest tests/cache/test_index_store_features.py tests/test_scanner_adapter.py tests/application/test_library_scan_service.py
.venv/bin/pytest tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/test_asset_grid_scroll.py
.venv/bin/pytest tests/performance/test_refactor_performance_baseline.py tests/test_thumbnail_cache_service.py tests/application/test_library_asset_query_service.py tests/library/test_scanner_worker.py
python3 -m compileall -q src/iPhoto
git diff --check
```

结果：

- 33 passed for cache/scanner/scan-service group
- 39 passed for Gallery/scroll group
- 21 passed for performance, thumbnail cache, query-service, and scanner-worker group
- compileall passed
- diff whitespace check passed
- pytest 仍有既有警告：unknown config option `env`

## 5. 下一步交接

1. 做 stale row backfill。
   - 优先 backfill 当前 visible window。
   - idle 时再处理 lookahead 和全库 stale rows。
   - backfill 成功后更新为 `ready` 并触发局部 window/dataChanged。

2. 完善 thumbnail queue。
   - 增加 priority、cancellation、bounded concurrency、failure cooldown。
   - 区分 scan-time micro thumbnail 与浏览期大图 thumbnail。
   - 将 L2 cache-key-only ready row 生产路径补完整。

3. 升级 scan event transport。
   - 将 `ScanBatchCommitted` 从内部 DTO/event payload 升级为 application/Qt 明确 transport。
   - GUI 消费 batch 时继续避免全量 reset，只做 count/revision 或可见窗口局部刷新。
   - 取消扫描后丢弃 late batch。

4. 增加更严格性能门禁。
   - 对 ready-thumbnail collection 查询加 `EXPLAIN QUERY PLAN` 回归。
   - 增加 opt-in 100k/1M synthetic rows benchmark。
   - 增加 scan visible publish latency 统计。

## 6. 注意事项

- 不要恢复 `docs/requirements/INITIAL_SCAN_LARGE_LIBRARY_STABILITY.md`；它在本轮前已删除，本轮继续保留该状态。
- 不要把 `failed` row 直接暴露给普通媒体 grid；诊断/失败列表应使用单独 query surface。
- 后续若改 `WindowResult` 为 typed DTO，需要同步更新 Gallery、Detail/Playback 和测试 fake query surface。
