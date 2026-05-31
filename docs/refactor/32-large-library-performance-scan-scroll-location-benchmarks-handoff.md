# 32 - Large Library Performance Scan/Scroll Location Batch + Benchmarks Handoff

> **版本:** 1.0 | **日期:** 2026-05-31
> **状态:** 已完成
> **范围:** Location/Map scan batch consumer, scan stage timing, scroll/publish benchmarks

## 1. 背景与目标

本轮承接 `31-large-library-performance-scan-scroll-events-handoff.md` 的下一步清单：

- 将 Location/Map 的扫描增量消费从旧 `scanChunkReady` 迁到首选
  `ScanBatchCommitted`。
- 保留旧 chunk transport，作为正式下线前的兼容 fallback。
- 给 scan batch/event 增加可用阶段耗时。
- 补齐默认小数据性能 sanity 和 opt-in 100k/1M synthetic scroll benchmark。

## 2. 变更摘要

- Location/Map batch transport
  - `GalleryViewModel` 新增 `handle_location_scan_batch(batch)`。
  - `MainCoordinator` 将 runtime/facade 的 `scanBatchCommitted` 同时连接到
    Gallery model adapter 和 Location/Map handler。
  - 旧 `scanChunkReady -> handle_location_scan_chunk()` 仍保留。
  - batch handler 复用原 location chunk 语义：map 模式增量刷新 map
    snapshot，cluster gallery 只更新缓存不刷新页面，inactive 模式只标记
    snapshot invalidated。

- Scan stage timing
  - `LibraryScanService.scan_album()` 记录 discover、stat-cache validation、
    metadata extraction、visible publish 的可用耗时。
  - `ScanLibraryUseCase` 在 batch merge 时继续记录 `db_commit`，并把已有
    stage timing 合并到 `ScanBatchCommitted.stage_elapsed_ms`。
  - `scan_events.batch_committed` payload 增加 `stage_elapsed_ms`。
  - persisted `stage_changed` event 增加 final `visible_publish` timing。

- Performance coverage
  - `tests/performance/test_refactor_performance_baseline.py` 增加 Gallery
    scroll window materialization bound。
  - 增加 scan visible-publish latency sanity，覆盖 store batch record 到
    flush 当前 pending scan refresh 的路径。
  - 增加 `IPHOTO_RUN_STRESS=1` opt-in 100k/1M synthetic scroll benchmark。
  - micro-thumbnail bytes 解码改为 Pillow 优先，Pillow 不可用时才 fallback
    到 Qt byte decoder，避免长测试进程中 Qt decoder 崩溃。

## 3. 验证记录

已运行：

```bash
.venv/bin/pytest tests/gui/viewmodels/test_gallery_viewmodel.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/gui/viewmodels/test_gallery_collection_store.py tests/application/test_library_scan_service.py tests/performance/test_refactor_performance_baseline.py tests/test_utils_image_loader.py -q
python3 -m compileall -q src/iPhoto
git diff --check
```

当前结果：

- Focused GUI/scan/performance pytest：115 passed, 2 skipped。
- compileall passed。
- diff whitespace check passed。
- pytest 仍有既有警告：unknown config option `env`。

## 4. 下一步交接

1. 继续审计剩余 `scanChunkReady` 消费方，确认下线条件。
2. 若 scanner 暴露更细粒度阶段边界，再把 thumbnail/derived-job timing 从
   scanner/use-case 内部真实上报，不要用估算值填充。
3. 给 opt-in benchmark 增加 JSON/CSV 输出，便于长期本地对比。
4. 若后续引入真实素材 benchmark，保持默认 skip，禁止修改用户真实库。

## 5. 注意事项

- `ScanBatchCommitted.rows` 必须保持 ready-only。
- Location/Map batch handling不得刷新 cluster gallery 当前页面。
- `scanChunkReady` 仍是兼容 fallback，本轮不删除。
- backfill 仍只面向旧库 visible-window stale recovery，不做启动期全库回填。
- 不要恢复已删除的 `docs/requirements/INITIAL_SCAN_LARGE_LIBRARY_STABILITY.md`。
