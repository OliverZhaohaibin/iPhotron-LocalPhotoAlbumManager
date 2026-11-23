# Large Library Query Guardrails

This note protects the large-library browsing path. Touch this area carefully:
small convenience fallbacks can reintroduce full-library work.

## Contract

- Normal Gallery reads should stay SQL-first through `CollectionQuery` /
  `WindowResult` when the query can be represented in the repository.
- All Photos, album, favorites, videos, map/GPS, media type, and date range
  views must not fall back to `read_all()` plus Python filtering/sorting.
- `GalleryCollectionStore.asset_at()` is cache-only. Do not make it
  synchronously fetch arbitrary rows.
- `row_for_path()` and live partner lookup must use repository/query-service
  lookup helpers, not full model scans.
- Read rows, count, and both collection revisions in one SQLite snapshot.
  Read disjoint Gallery and Filmstrip ranges together when staging a new
  order. The lightweight thumbnail-hint query omits the count.
- Use `collection_revision` to reject older asynchronous results and
  `order_revision` to version counts, keyset anchors, and numeric positions.
  A thumbnail update that preserves membership and ordering can advance
  the collection revision while keeping the order revision. Use bounded
  snapshot metadata for change detection.
- Resolve requested offscreen identities and pending move ranks on the
  window worker at the window's order revision. For a small set of paths,
  count disjoint indexed ranges and tie-breakers. For larger sets, rank the
  collection once for all requested paths. If the order revision changes
  during lookup, retry with a fresh window.
- Visible collection queries default to ready-only rows with non-empty
  `thumb_cache_key`.
- Stale thumbnail repair must be visible-window/backfill oriented. Do not run a
  full-library synchronous repair during startup or collection open.
- Keep ready-query `EXPLAIN QUERY PLAN` tests using visible/gps indexes and no
  temp sort for hot views.

## Visibility Flag Migrations

Collection queries use denormalized columns such as `is_deleted`, `has_gps`,
`thumbnail_state`, and `live_role` so large libraries can stay on covering
indexes instead of scanning path/text payloads. When adding or tightening one
of these flags, update both row writes and schema migration:

- New scan rows should derive the flag at write time in the index-store mapper.
- Existing databases need a migration backfill before hot queries depend on the
  new flag.
- Do not remove compatibility path predicates from a query unless the migration
  proves equivalent legacy rows are backfilled. `.Trash` is especially
  sensitive: normal collections must exclude it, but the Recently Deleted album
  still needs to show it.
- Add one focused upgrade regression test for legacy rows and one query-level
  test for the visible collection behavior.

## Focused Checks

```bash
.venv/bin/python -m pytest tests/application/test_library_asset_query_service.py tests/cache/test_index_store_features.py tests/performance/test_refactor_performance_baseline.py -q
.venv/bin/python -m pytest tests/gui/viewmodels/test_gallery_scan_progress.py tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/test_asset_grid_scroll.py -q
```
