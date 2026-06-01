# Large Library Performance Refactor Log

Date: 2026-05-30
Branch: `codex/large-library-performance`

## Scope

This branch implements the staged mainline for the large-library performance
requirements. It covers Phase 0-2 foundations and one scan batching baseline:

- opt-in performance events and full-scan query auditing
- SQL-first collection DTOs and repository/query-service APIs
- Gallery model/store changes that avoid full snapshot traversal and synchronous
  deep row fetches
- larger default scan merge chunks

The existing deletion of `docs/requirements/INITIAL_SCAN_LARGE_LIBRARY_STABILITY.md`
was preserved and not modified.

## Completed Changes

- Added `emit_perf_event()` and full-scan auditing under
  `iPhoto.infrastructure.services.performance_events`.
- Added `CollectionQuery`, `CollectionType`, `SortDirection`, `PageCursor`,
  `PageResult`, and `WindowResult` while keeping `AssetQuery`.
- Added collection query SQL builders and repository APIs:
  `count_collection()`, `read_collection_page()`, `read_collection_window()`,
  `find_row_by_path()`, and `find_live_partner()`.
- Added schema columns and indexes for large-library collection access:
  `sort_ts`, `is_deleted`, `has_gps`, `thumbnail_state`, thumbnail cache fields,
  and `index_revision`.
- Routed eligible `LibraryAssetQueryService` queries through collection SQL so
  GPS, date range, favorite false, media type, favorites, and album filters do
  not fall back to Python materialization.
- Changed Gallery behavior so `asset_at()` returns only cached rows, and
  `row_for_path()` uses query-service lookup instead of scanning every window.
- Replaced the model reset snapshot hash with a count/window/revision signature.
- Changed live motion lookup to prefer repository-backed partner lookup instead
  of scanning every row in the model.
- Increased the default scan merge chunk size from 50 to 500 rows.
- Fixed the deep-scroll placeholder stall for aggregate and physical-folder
  albums. `read_collection_window()` now uses an anchor/keyset fallback for
  windows beyond the shallow offset threshold instead of raising, and Gallery
  window fetch failures are contained so later visible-range requests can
  recover.
- Fixed the related Detail/Playback failure where deep-scroll placeholders could
  not be opened or operated on. The root cause was missing collection rows, not
  just missing thumbnail pixmaps: Gallery viewport range reporting could expand
  to the end of the album, and Detail/Playback relied on cached-only
  `asset_at()` reads. The branch now bounds viewport windows and adds an
  explicit `ensure_row_loaded()` path for user-initiated opens/navigation while
  keeping `asset_at()` cache-only.

## Verification

Final commands run:

```bash
.venv/bin/pytest tests/ui/test_media_selection_session.py tests/gui/viewmodels/test_detail_viewmodel.py tests/gui/viewmodels/test_gallery_collection_store.py tests/test_asset_grid_scroll.py
```

Result:

- 42 passed
- 1 warning from pytest config: unknown `env` option

```bash
.venv/bin/pytest tests/application/test_library_asset_query_service.py tests/cache/test_index_store_features.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/application/test_library_scan_service.py tests/performance/test_refactor_performance_baseline.py
```

Result:

- 50 passed
- 1 warning from pytest config: unknown `env` option

```bash
python3 -m compileall -q src/iPhoto
```

Result: passed.

## Handoff

Next recommended work:

- Phase 3: make visible rows require `thumbnail_state='ready'` plus
  `micro_thumbnail` or `thumb_cache_key`; migrate old rows to `stale` and
  backfill visible windows first.
- Phase 4: add `scan_jobs` and `scan_events`, then publish explicit
  `ScanBatchCommitted` batches containing only thumbnail-ready rows.
- Phase 5: split startup into first paint, session bind, first collection, and
  idle jobs so automatic scanning and People/Maps work cannot block startup.
- Phase 6: add opt-in 100k/1M synthetic benchmarks and stricter query-plan
  assertions.

Known constraints:

- The new collection APIs return dict rows for compatibility; a later pass can
  introduce typed DTO rows once GUI callers are fully migrated.
- Thumbnail cache priority, cancellation, failure cooldown, and scan-time
  thumbnail guarantees are still future work.
- Phase 3 will still improve placeholder quality by enforcing thumbnail-ready
  visible rows, but it is no longer expected to fix deep-scroll loading stalls;
  that bug is handled in this branch.

---

# Phase 3/4 Follow-up Log

Date: 2026-05-31
Branch: `codex/large-library-performance`

## Scope

This follow-up continues the large-library performance migration after the
Phase 0-2 foundation. It implements the first production-ready slice of Phase 3
and a minimal Phase 4 scan-job baseline:

- visible collection rows now require a ready thumbnail payload
- scan rows are classified as ready/failed/stale instead of treating missing
  thumbnails as normal gallery media
- scan chunk publication is ready-only while preserving the existing Qt
  `scanChunkReady(Path, list)` transport
- Gallery gets a single window-result path so visible-window reloads do not
  need a separate count query before fetching rows
- scan jobs/events are persisted for future observable scan pipeline work

The existing deletion of `docs/requirements/INITIAL_SCAN_LARGE_LIBRARY_STABILITY.md`
was preserved and not modified.

## Completed Changes

- Added `ThumbnailState` and `ThumbnailReadyResult` to the query model surface.
- Added `ScanStage`, `ScanJob`, and `ScanBatchCommitted` DTOs under
  `iPhoto.domain.models.scan`.
- Added `scan_jobs` and `scan_events` tables plus repository APIs:
  `create_scan_job()`, `update_scan_job_stage()`, and `append_scan_event()`.
- Changed SQLite migration and row mapping so missing/blank thumbnail state
  becomes `stale` unless a row already has `micro_thumbnail` or
  `thumb_cache_key`; old `ready` rows without a payload are also demoted to
  `stale`.
- Changed collection SQL so `min_thumbnail_state='ready'` also requires
  `micro_thumbnail IS NOT NULL OR thumb_cache_key` before a row can enter a
  normal media grid.
- Added scan-time thumbnail classification in `scanner_adapter`:
  successful thumbnail generation writes `thumbnail_state='ready'` and
  `micro_thumbnail`; failures write `thumbnail_state='failed'` and
  `thumb_error`.
- Changed scan chunk publication so GUI callbacks only receive ready rows; DB
  merge still persists the full batch, including failed rows for diagnostics.
- Added scan job stage bookkeeping to `LibraryScanService.scan_album()`.
- Increased `ScannerWorker.SCAN_CHUNK_SIZE` from 10 to 500.
- Added `LibraryAssetQueryService.read_query_asset_window()` to return scoped
  `WindowResult` rows, total count, and collection revision in one call.
- Changed `GalleryCollectionStore` to prefer the window-result API and keep a
  fallback for legacy/fake query surfaces.
- Added 16ms `prioritize_rows()` coalescing in `GalleryListModelAdapter`.
- Fixed Gallery thumbnail display after the thumbnail-ready migration:
  scan-row `micro_thumbnail` bytes are decoded to `QImage` for fallback drawing,
  while `DecorationRole` still goes through `ThumbnailCacheService.get_thumbnail()`
  so 512x512 thumbnails are scheduled, cached, and repainted when ready.

## Verification

Commands run:

```bash
.venv/bin/pytest tests/cache/test_index_store_features.py tests/test_scanner_adapter.py tests/application/test_library_scan_service.py
```

Result:

- 33 passed
- 1 warning from pytest config: unknown `env` option

```bash
.venv/bin/pytest tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/test_asset_grid_scroll.py
```

Result:

- 39 passed
- 1 warning from pytest config: unknown `env` option

```bash
.venv/bin/pytest tests/performance/test_refactor_performance_baseline.py
```

Result:

- 3 passed
- 1 warning from pytest config: unknown `env` option

Additional safety checks:

```bash
.venv/bin/pytest tests/application/test_library_asset_query_service.py tests/library/test_scanner_worker.py
python3 -m compileall -q src/iPhoto
git diff --check
```

Result:

- 16 passed for the extra pytest command
- compileall passed
- diff whitespace check passed

Final thumbnail display regression checks:

```bash
.venv/bin/pytest tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/test_asset_grid_scroll.py
.venv/bin/pytest tests/performance/test_refactor_performance_baseline.py tests/test_thumbnail_cache_service.py tests/application/test_library_asset_query_service.py tests/library/test_scanner_worker.py
python3 -m compileall -q src/iPhoto
git diff --check
```

Result:

- 39 passed for Gallery/scroll tests
- 21 passed for performance, thumbnail cache, query-service, and scanner-worker tests
- compileall passed
- diff whitespace check passed

## Handoff

Current behavior to preserve:

- Normal gallery collections should never show `pending`, `failed`, or `stale`
  rows.
- A `ready` row must have `micro_thumbnail` or `thumb_cache_key`.
- Scanner callbacks are ready-only, but repository merge still records failed
  rows for retry/diagnostics.
- Mid-scroll scan refresh updates count/revision and keeps the active visible
  window stable instead of jumping to newly inserted top rows.
- Gallery uses micro thumbnails only as a temporary fallback; `DecorationRole`
  must continue to request 512x512 thumbnails from `ThumbnailCacheService`.
- Existing `scanChunkReady` and `scanFinished` signals remain the GUI transport
  for now.

Next recommended work:

- Add visible-window-first backfill for `stale` rows so old libraries recover
  thumbnails without blocking first paint.
- Move scan thumbnail generation to a cancellable/priority-aware queue with
  bounded concurrency and failure cooldown.
- Promote `ScanBatchCommitted` from internal DTO/event payload to an explicit
  Qt/application transport once all GUI consumers are ready.
- Expand scan job stages beyond the current coarse baseline so discover,
  metadata, thumbnail, commit, publish, and derived jobs have accurate timings.
- Add stricter query-plan assertions for ready-thumbnail collection queries and
  larger synthetic 100k/1M opt-in benchmarks.

Known constraints:

- `PageResult` and `WindowResult` still carry dict rows for compatibility.
- Scan-time thumbnail readiness currently stores micro thumbnails first; L2
  cache-key-only ready rows are supported by query semantics but need a fuller
  cache-key production path.
- The Gallery window API fallback remains necessary for tests and legacy query
  surfaces that do not yet implement `read_query_asset_window()`.

---

# Scan/Scroll Event Coalescing Log

Date: 2026-05-31
Branch: `codex/large-library-performance`
Status: completed

## Scope

This follow-up implements the next scan/scroll performance slice from
`30-large-library-performance-scan-scroll-handoff.md`:

- explicit Qt-timer coalescing for `ScanBatchCommitted` GUI refreshes
- event-driven stale thumbnail backfill completion instead of polling
- ready-thumbnail collection query-plan regression coverage

The existing deletion of `docs/requirements/INITIAL_SCAN_LARGE_LIBRARY_STABILITY.md`
must remain preserved.

## Completed Changes

- Routed GUI `scanBatchCommitted` signals through `GalleryListModelAdapter`,
  where a 150ms Qt timer coalesces rapid scan/backfill batches before refreshing
  the visible window.
- Split `GalleryCollectionStore` scan handling into record and flush steps so
  legacy direct calls still refresh immediately while adapter-driven batches can
  coalesce.
- Added `LibraryAssetQueryService.thumbnail_backfill_completed`, a lightweight
  callback signal that publishes `ScanBatchCommitted` after stale thumbnail
  backfill succeeds.
- Removed active reliance on the 500ms Gallery backfill polling timer; retained
  compatibility no-op methods for old tests/fakes.
- Tightened ready collection WHERE clauses to use indexed fields directly and
  added query-plan regression coverage for ready-thumbnail collections.
- Marked rows under `Recently Deleted` as `is_deleted=1` during row mapping so
  ordinary collection queries can use the visible indexes without path-prefix
  filtering.

## Verification

Commands run:

```bash
.venv/bin/pytest tests/application/test_library_asset_query_service.py tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/performance/test_refactor_performance_baseline.py
.venv/bin/pytest tests/cache/test_index_store_features.py tests/test_scanner_adapter.py tests/application/test_library_scan_service.py tests/test_asset_grid_scroll.py tests/test_thumbnail_cache_service.py tests/library/test_scanner_worker.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py
```

Result:

- 58 passed for the focused scan/scroll/performance command.
- 67 passed for the wider cache/scan/gallery/coordinator command.
- Existing warning remains: pytest unknown config option `env`.

## Handoff

Current behavior to preserve:

- `ScanBatchCommitted` remains ready-only and is now the preferred Gallery
  refresh transport.
- Legacy `scanChunkReady` is still connected for location/gallery compatibility.
- Backfill remains scoped to visible-window stale recovery; no startup full
  library backfill was added.
- Ready collection queries now rely on `is_deleted`, `thumbnail_state`, and
  thumbnail payload fields being normalized by migrations/row mapping.

Next recommended work:

- Add more GUI consumers to `scanBatchCommitted`, then evaluate deprecating
  legacy `scanChunkReady`.
- Add opt-in 100k/1M synthetic scroll and scan visible-publish benchmarks.
- Continue expanding scan job stage timings for discover, metadata, thumbnail,
  commit, publish, and derived jobs.

---

# Scan/Scroll Follow-up Log

Date: 2026-05-31
Branch: `codex/large-library-performance`

## Scope

This follow-up continues the scan and scrolling performance work after the
Phase 3/4 baseline. It focuses on a pragmatic production slice:

- explicit ready-only `ScanBatchCommitted` transport
- Gallery consumption of scan batches without dropping the legacy chunk signal
- visible-window-first stale thumbnail recovery
- bounded priority thumbnail generation in the Qt thumbnail cache
- collection window count/revision reuse

The existing deletion of `docs/requirements/INITIAL_SCAN_LARGE_LIBRARY_STABILITY.md`
was preserved and not modified.

## Completed Changes

- Added `scan_job_id` to `ScanLibraryResult`.
- Added `scan_batch_callback` to the scan use case/service path and emit
  `ScanBatchCommitted` DTOs containing only thumbnail-ready rows.
- Persisted richer `scan_events.batch_committed` payloads with requested,
  merged, ready, elapsed, and revision details.
- Added `scanBatchCommitted = Signal(object)` through `ScannerSignals`,
  `LibraryRuntimeController`, `LibraryUpdateService`, and `AppFacade`.
- Connected `MainCoordinator` so Gallery consumes explicit scan batches while
  legacy `scanChunkReady` remains connected for compatibility.
- Added `GalleryCollectionStore.handle_scan_batch()` and visible-window stale
  backfill triggering.
- Moved stale thumbnail backfill off the Gallery/UI call path into a single
  background worker owned by `LibraryAssetQueryService`; Gallery now polls for
  completion via a lightweight 500ms timer before reloading the visible window.
- Added repository `read_thumbnail_backfill_candidates()` and
  `update_thumbnail_ready()`.
- Added per-query collection count/revision cache for `read_collection_window()`
  with invalidation on repository writes and thumbnail state updates.
- Hardened ready collection SQL so `thumbnail_state='ready'` also requires
  `micro_thumbnail` or `thumb_cache_key`.
- Changed `ThumbnailCacheService` to queue misses through bounded priority
  queues, limit active generation jobs, support pending cancellation, and apply
  a 60s failure cooldown.
- Changed Gallery `DecorationRole` thumbnail requests to use visible priority.

## Verification

Commands run:

```bash
.venv/bin/pytest tests/application/test_library_scan_service.py tests/cache/test_index_store_features.py tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/test_thumbnail_cache_service.py tests/library/test_scanner_worker.py
.venv/bin/pytest tests/cache/test_index_store_features.py tests/test_scanner_adapter.py tests/application/test_library_scan_service.py tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/test_asset_grid_scroll.py tests/performance/test_refactor_performance_baseline.py tests/test_thumbnail_cache_service.py tests/application/test_library_asset_query_service.py tests/library/test_scanner_worker.py
.venv/bin/pytest tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/services/test_library_update_service_global_db.py
.venv/bin/pytest tests/cache/test_index_store_features.py tests/test_scanner_adapter.py tests/application/test_library_scan_service.py tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/test_asset_grid_scroll.py tests/performance/test_refactor_performance_baseline.py tests/test_thumbnail_cache_service.py tests/application/test_library_asset_query_service.py tests/library/test_scanner_worker.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/services/test_library_update_service_global_db.py
python3 -m compileall -q src/iPhoto
git diff --check
QT_QPA_PLATFORM=offscreen .venv/bin/python -m iPhoto.gui.main
```

Result:

- Phase 3/4 + follow-up + GUI/service signal boundary pytest group:
  129 passed.
- compileall passed.
- diff whitespace check passed.
- offscreen GUI launch reached saved-library bind and All Photos selection for
  `/Users/haibinzhao/Documents/testbase`; the test process was then stopped.
- pytest still reports the existing warning: unknown `env` config option.

## Handoff

Current behavior to preserve:

- `ScanBatchCommitted.rows` and legacy `scanChunkReady` GUI chunks must remain
  ready-only.
- Gallery should continue to use DB window reloads as source of truth after
  scan batches, not append arbitrary rows into the model.
- `ThumbnailCacheService` L1/L2 hits must not enqueue generation work.
- Failed thumbnail generation should respect cooldown until invalidated.

Next recommended work:

- Replace Gallery backfill timer polling with an explicit Qt/application
  completion event that can publish `ScanBatchCommitted` on success.
- Add explicit Qt timer coalescing for scan-triggered Gallery reloads.
- Add query-plan and opt-in synthetic 100k/1M performance benchmarks.

---

# Scan/Scroll Location Batch + Benchmark Log

Date: 2026-05-31
Branch: `codex/large-library-performance`
Status: completed

## Scope

This follow-up continues the scan/scroll event migration after
`31-large-library-performance-scan-scroll-events-handoff.md`:

- Location/Map now consumes ready-only `ScanBatchCommitted` batches.
- Legacy `scanChunkReady` remains wired as a compatibility fallback.
- Scan batches and persisted batch events carry available stage timing.
- Performance coverage now includes Gallery scroll materialization bounds,
  scan visible-publish latency, and opt-in 100k/1M synthetic scroll checks.

The existing deletion of `docs/requirements/INITIAL_SCAN_LARGE_LIBRARY_STABILITY.md`
was preserved and not modified.

## Completed Changes

- Added `GalleryViewModel.handle_location_scan_batch()` and routed both
  runtime and facade `scanBatchCommitted` signals to it.
- Kept `handle_location_scan_chunk()` and `scanChunkReady` in place for old
  transport compatibility while making batch transport the preferred path for
  both Gallery and Location/Map.
- Reused existing Location/Map snapshot semantics for batches:
  map mode emits one `map_assets_changed`, cluster-gallery mode updates the
  cached snapshot without refreshing the gallery, and inactive mode only
  invalidates a cached location snapshot.
- Added available stage timing to `ScanBatchCommitted.stage_elapsed_ms` and
  `scan_events.batch_committed` payloads: discover, stat-cache validation,
  metadata extraction elapsed so far, and DB commit.
- Added final `visible_publish` timing to persisted scan stage events.
- Added performance coverage for bounded Gallery scroll materialization,
  scan visible-publish latency, and opt-in synthetic 100k/1M scroll benchmarks
  guarded by `IPHOTO_RUN_STRESS=1`.
- Changed micro-thumbnail byte decoding to prefer Pillow when available, using
  Qt's byte decoder only as a fallback when Pillow support is unavailable.

## Verification

Commands run:

```bash
.venv/bin/pytest tests/gui/viewmodels/test_gallery_viewmodel.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/gui/viewmodels/test_gallery_collection_store.py tests/application/test_library_scan_service.py tests/performance/test_refactor_performance_baseline.py tests/test_utils_image_loader.py -q
python3 -m compileall -q src/iPhoto
git diff --check
```

Result:

- 115 passed, 2 skipped for the focused GUI/scan/performance command.
- compileall passed.
- diff whitespace check passed.
- Existing warning remains: pytest unknown config option `env`.

## Handoff

Current behavior to preserve:

- `ScanBatchCommitted.rows` remains ready-only.
- Location/Map should prefer `scanBatchCommitted`; `scanChunkReady` remains a
  fallback until remaining legacy producers are formally retired.
- Location/Map batch handling must not refresh cluster gallery views.
- The stress benchmark is opt-in only through `IPHOTO_RUN_STRESS=1`.
- No startup full-library thumbnail backfill was added.

Next recommended work:

- Add richer scanner-owned timing if/when the scanner exposes discover,
  metadata, thumbnail, and derived-job boundaries directly.
- Continue evaluating `scanChunkReady` removal once all GUI and service
  consumers prove they no longer depend on legacy chunk payloads.
- Add JSON/CSV benchmark output for long-running local stress comparisons.

---

# Scan Thumbnail Cache-Key Readiness Log

Date: 2026-06-01
Branch: `codex/large-library-performance`
Status: completed

## Scope

This follow-up tightens the thumbnail-ready invariant for large-library scan
and scroll behavior:

- ready rows now require a stable 512px thumbnail cache key, not micro-only
  payloads.
- scan-time thumbnail generation writes both micro payloads and the shared L2
  disk thumbnail used by `ThumbnailCacheService`.
- cached scan rows, stale backfill, and move/restore reuse paths refresh missing
  full thumbnails before publishing rows as visible-ready.

## Completed Changes

- Added shared thumbnail cache-key helpers so scanner and GUI cache service use
  the same path/size key and disk filename.
- Updated `ThumbnailCacheService` to resolve disk files through the shared key
  helpers.
- Updated scanner thumbnail generation to compose/write a 512px JPEG cache file
  and publish `thumb_cache_key` with ready rows.
- Changed ready collection SQL, row mapping, migration cleanup, repository
  update guards, and scan batch filtering to require `thumb_cache_key`.
- Updated stale thumbnail backfill and move/restore lifecycle paths so refreshed
  rows get a full thumbnail cache entry before becoming ready.
- Added tests for scan-written L2 cache hits, cached-row refresh, move/restore
  cache directory selection, and the stricter ready invariant.

## Verification

Commands run:

```bash
.venv/bin/pytest tests/application/test_library_asset_lifecycle_service.py tests/application/test_library_asset_query_service.py tests/application/test_library_scan_service.py tests/cache/test_index_store_features.py tests/test_scanner_adapter.py tests/test_thumbnail_cache_service.py tests/library/test_scanner_worker.py tests/test_utils_image_loader.py -q
python3 -m compileall -q src/iPhoto
git diff --check
```

Result:

- 86 passed for the scan/cache/query/lifecycle/thumbnail focused command.
- compileall passed.
- diff whitespace check passed.
- Existing warning remains: pytest unknown config option `env`.

## Handoff

Current behavior to preserve:

- `thumbnail_state='ready'` now requires a non-empty `thumb_cache_key`.
- Micro thumbnails remain useful as a temporary fallback but are not sufficient
  for normal visible collection membership.
- Scanner, stale backfill, move/restore, and GUI thumbnail cache must continue
  sharing the same 512px cache key convention.
- `IPHOTO_RUN_STRESS=1` benchmarks remain opt-in only.

Next recommended work:

- Add a maintenance/repair command for older libraries that have micro-only
  ready rows and need full L2 cache backfill.
- Add benchmark JSON/CSV output for comparing L2 cache hit rates over time.
