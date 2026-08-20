# Gallery Scroll Pipeline Guardrails

This note protects the latency-sensitive Gallery path introduced by the sparse
window and demand-driven thumbnail pipeline. A convenient synchronous fallback
in this area can turn one wheel event into SQLite, filesystem, decode, or broad
repaint work.

## Threading And Paint Contract

- Delegate paint, `model.data()`, `asset_at()`, and thumbnail peek operations are
  memory-only. Never add SQLite queries, `Path.exists()`, L2 reads, image decode,
  blocking waits, synchronous layout, or `repaint()` to these paths.
- Window rows and thumbnail hints are loaded by `GalleryWindowLoader` and
  `GalleryThumbnailHintLoader`. Results carry collection revision, surface id,
  and that surface's demand generation. A newer Filmstrip request must not
  invalidate still-relevant Gallery work, or vice versa.
- Workers decode to `QImage`. `QPixmap` creation remains on the GUI thread and
  is drained through the bounded item/time publish budget.
- Thumbnail-ready updates are coalesced into exact row ranges and roles. Do not
  restore one broad `dataChanged` or full-viewport repaint per result.

## Demand And Cache Contract

- `AssetViewportDemand` is the single per-surface source for visible, full guard,
  far speculative, and micro-warm ranges. New heuristics belong in the demand
  policy, not as independent widget/viewmodel prefetch loops. Filmstrip ranges
  come from actual horizontal geometry and map through every proxy layer.
- Continuous medium/fast bursts prioritize input and visible recovery: they must
  not start hint queries or speculative L2 reads. Slow/dwell/idle work remains
  generation-aware and cancellable.
- Keep visible, guard, and far speculative lanes isolated. Far work cannot use
  urgent guard capacity, and newly visible requests must promote matching
  in-flight prefetch work rather than duplicate it.
- L1 is one byte-budgeted multi-surface cache. Cache keys include display size;
  bucket changes must not flush the pool. Preserve the union of visible pins,
  per-surface lease release, demand-aware eviction, staging caps, miss TTLs,
  low-memory release, and speculative backoff when changing cache behavior.
- Paint, model data, and peek paths remain memory-only for every surface. A
  missing Filmstrip full thumbnail is recovered by viewport demand, never by a
  request issued from the delegate.
- Gallery SQL projections stay narrow: tile windows omit wide metadata, while
  hint windows return only paths and existing full-thumbnail keys and do not
  repeat collection counts.
- Explicit detail-row loads survive viewport generation changes. Optimistic move
  overlays, pinned rows, collection revisions, and selection snapshots must also
  survive sparse-window merges.

## Focused Checks

```bash
.venv/bin/python -m pytest tests/test_gallery_demand.py tests/test_asset_grid_scroll.py tests/ui/test_gallery_grid_view.py -q
.venv/bin/python -m pytest tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/gui/viewmodels/test_gallery_demand_coordinator.py tests/gui/viewmodels/test_gallery_thumbnail_hint_loader.py -q
.venv/bin/python -m pytest tests/test_thumbnail_cache_service.py tests/test_thumbnail_runtime_policy.py tests/cache/test_index_store_features.py tests/application/test_library_asset_query_service.py -q
```

The real Qt benchmark is opt-in because it exercises timing and disk-latency
scenarios:

```bash
IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1 .venv/bin/python -m pytest tests/performance/test_gallery_scroll_qt_benchmark.py -q
```

Run it on Windows and Linux with a real L2 cache before merging scheduling,
worker-count, publish-budget, display-bucket, or scroll-intent changes. macOS is
a separate no-regression baseline, not a substitute for either platform.
