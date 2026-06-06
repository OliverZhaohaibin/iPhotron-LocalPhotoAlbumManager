# 33 - Large Library Performance Scan Thumbnail Cache-Key Handoff

> **版本:** 1.0 | **日期:** 2026-06-01
> **状态:** 已完成
> **范围:** scan-time 512px thumbnail cache keys, ready-row invariant tightening

## 1. 背景与目标

本轮承接 large-library scan/scroll 性能迁移中“可见 row 必须真实可显示”的约束，
继续收紧 thumbnail-ready 语义：

- `thumbnail_state='ready'` 不再接受 micro-only payload。
- scan/backfill/move/restore 发布 ready row 前必须有稳定 `thumb_cache_key`。
- scanner 与 `ThumbnailCacheService` 共享同一个 512px L2 cache key 与文件路径规则。

## 2. 变更摘要

- Shared thumbnail cache key
  - 新增 `thumbnail_cache_keys` helper，统一生成 512px cache key 和 disk file。
  - `ThumbnailCacheService` 改用共享 key helper，避免 scanner 与 GUI cache
    对同一文件生成不同 L2 key。

- Scan-time thumbnail readiness
  - `ensure_scan_thumbnail()` 现在写入 micro thumbnail 和 512px JPEG cache file。
  - `process_media_paths()`、`scan_album()`、cached-row refresh 都会传递
    thumbnail cache dir。
  - 已缓存但缺少 full thumbnail cache 的 scan row 会刷新 thumbnail 后再发布。

- Ready invariant tightening
  - ready collection SQL、row mapper、migration cleanup、repository ready update、
    scan batch filtering 都要求非空 `thumb_cache_key`。
  - 旧库中 `ready` 但无 `thumb_cache_key` 的 row 会迁移为 `stale`，等待 backfill。

- Backfill and lifecycle
  - stale thumbnail backfill 通过 session cache dir 写入 512px cache。
  - move/restore 复用 cached metadata 时会刷新目标路径的 thumbnail cache key。
  - sessionless move 使用 destination root 的 `.iPhoto/cache/thumbs`。

## 3. 验证记录

已运行：

```bash
.venv/bin/pytest tests/application/test_library_asset_lifecycle_service.py tests/application/test_library_asset_query_service.py tests/application/test_library_scan_service.py tests/cache/test_index_store_features.py tests/test_scanner_adapter.py tests/test_thumbnail_cache_service.py tests/library/test_scanner_worker.py tests/test_utils_image_loader.py -q
python3 -m compileall -q src/iPhoto
git diff --check
```

当前结果：

- Focused scan/cache/query/lifecycle/thumbnail pytest：86 passed。
- compileall passed。
- diff whitespace check passed。
- pytest 仍有既有警告：unknown config option `env`。

## 4. 下一步交接

1. 为旧库补一个可手动触发的 repair/backfill 命令，专门处理 micro-only ready rows。
2. 给 benchmark 输出增加 JSON/CSV，记录 L2 cache hit/miss 与 scan visible publish latency。
3. 继续评估 `scanChunkReady` 下线条件，确保所有 ready rows 都带 full cache key。

## 5. 注意事项

- `ScanBatchCommitted.rows` 仍必须 ready-only。
- ready row 必须有 `thumb_cache_key`；micro thumbnail 只是临时 fallback。
- 不要在启动路径做全库同步 thumbnail repair/backfill。
- 不要恢复已删除的 `docs/requirements/INITIAL_SCAN_LARGE_LIBRARY_STABILITY.md`。
