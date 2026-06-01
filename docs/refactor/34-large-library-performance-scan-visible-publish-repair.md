# 34 - Large Library Performance Scan Visible Publish Repair

> **版本:** 1.0 | **日期:** 2026-06-02
> **状态:** 已完成
> **范围:** restore in-scan UI updates through `ScanBatchCommitted`

## 1. 背景

自 `4ce6c0bd1c406fc30909610b65fd91810b808ed1` 引入 large-library performance
重构文档后，扫描架构逐步从旧 `scanChunkReady` 迁移到
`ScanBatchCommitted`。在完全下线 `scanChunkReady` 后，扫描过程中 UI
不再稳定地实时感知新增媒体，违反了
`docs/requirements/large_library_performance` 中的要求：

- UI publish 每 100-250ms 或 50-200 ready rows 合并。
- `ScanBatchCommitted` 只发布 ready rows。
- 扫描过程中用户应能实时或接近实时看到媒体内容更新。

本轮修复坚持新扫描架构，不恢复旧 `scanChunkReady` 兼容 transport。

## 2. 排查结论

- 扫描发布链已经切到 `ScanBatchCommitted`，但 `ScanLibraryUseCase` 仍主要按
  大 merge chunk 提交，默认 500 rows；慢 thumbnail 或不足 500 rows 的扫描会让 UI
  感觉扫描中没有增量变化。
- Gallery 初始为空时，`GalleryCollectionStore` 收到 batch 后如果还没有
  visible range，会清掉 pending scan refresh，导致空库首屏扫描时看不到过程性更新。
- 默认 `CollectionQuery` 曾允许 pending/failed/stale/old-style no-key rows 进入普通
  collection 语义；这与当前“visible row 必须 ready 且带 full cache key”的约束冲突，
  也会削弱 batch 刷新后的窗口一致性。

## 3. 变更摘要

- `ScanLibraryRequest` 增加 `visible_publish_size=100`。
- `ScanLibraryUseCase` 保持默认 500-row DB merge chunk；每次 merge 后再把
  ready/full-cache-key rows 拆成 100-row `ScanBatchCommitted` UI batches。
- `ScanBatchCommitted.rows` 仍在 merge 后二次过滤，只包含
  `thumbnail_state='ready'` 且非空 `thumb_cache_key` 的 rows。
- `GalleryCollectionStore.flush_pending_scan_refresh()` 在没有 visible range 时会加载
  初始窗口，而不是丢弃 pending refresh。
- `CollectionQuery` 默认 `min_thumbnail_state='ready'`，`AssetQuery` 转
  `CollectionQuery` 也显式使用 ready-only，普通 collection 不显示 stale/failed/pending
  或缺 full cache key 的 old rows。
- 旧 `scanChunkReady` 仍只存在于
  `src/iPhoto/legacy/library/scan_chunk_ready_transport.py` 的 reference-only archive 中，
  生产路径不调用。

## 4. 新增/调整测试

- `test_scan_library_use_case_splits_ready_ui_batches_after_large_db_chunk`
  覆盖 DB commit 保持 500-row chunk，UI publish 拆成 100 ready-row batches。
- `test_scan_album_visible_publish_batches_are_small_enough`
  覆盖 `LibraryScanService.scan_album()` 默认以 100 ready rows 分批发布。
- `test_handle_scan_batch_refreshes_empty_initial_window`
  覆盖空 collection 首屏收到 batch 后立刻刷新初始窗口。
- 更新 collection query 测试，明确普通 gallery collection 只显示 ready/full-key rows，
  old-style no-key rows 仅作为 thumbnail backfill candidates。

## 5. 验证记录

已运行：

```bash
.venv/bin/pytest tests/application/test_library_scan_service.py tests/application/test_scan_library_use_case.py tests/application/test_library_asset_query_service.py tests/cache/test_index_store_features.py tests/library/test_scanner_worker.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/gui/viewmodels/test_gallery_viewmodel.py tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/test_people_service.py::test_scanner_worker_does_not_emit_batch_for_failed_persist tests/performance/test_refactor_performance_baseline.py tests/architecture/test_layer_boundaries.py -q
python3 -m compileall -q src/iPhoto
git diff --check
rg -n "scanChunkReady|handle_location_scan_chunk|visible_chunk_callback|chunk_callback" src/iPhoto tests
```

结果：

- 173 passed, 2 skipped。
- compileall passed。
- diff whitespace check passed。
- `rg` 只命中 legacy reference archive；生产扫描路径没有恢复旧 transport。
- pytest 仍有既有警告：unknown config option `env`。

## 6. 后续注意事项

- 不要为了过程性 UI 更新恢复 `scanChunkReady`；新增消费者应订阅
  `ScanBatchCommitted`。
- visible publish 阈值必须保持在 requirements 的 50-200 ready rows 范围内。
- 如果后续引入 100-250ms 时间合并，应保持 ready/full-cache-key 过滤不变。
- 不要在启动路径做全库同步 repair；old-style no-key rows 继续通过 stale/backfill
  路径恢复。
