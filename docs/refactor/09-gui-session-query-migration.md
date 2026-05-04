# 09 - GUI Session Query Migration Notes

> Date: 2026-04-29

## Goal

This pass continued the handoff from
`08-move-lifecycle-migration.md`: GUI-facing favorite writes, asset grid reads,
export reads, album dashboard counts/covers, and map aggregation now go through
session-bound query/state surfaces instead of importing the global index-store
singleton directly.

Repository consolidation remains intentionally out of scope. The new query
service still reads from the current global index store, but the concrete
dependency is now owned by the bootstrap/session boundary.

## What Changed

- Added `LibraryAssetQueryService`.
  - Provides scoped counts, lightweight geometry rows, full asset rows, and
    geotagged rows.
  - Converts album-scoped rows to album-relative paths for GUI consumers.
  - Provides a scoped location-cache writer so gallery reads can keep their
    best-effort location caching behavior without importing the repository.
- Exposed session surfaces from `LibrarySession`.
  - `LibrarySession.asset_queries` owns GUI/query reads.
  - `LibrarySession.state` continues to own durable user-state writes.
  - `RuntimeContext.open_library()` binds both surfaces into `LibraryManager`;
    `close_library()` clears them.
- Refactored GUI and library reads.
  - Favorite toggles use `LibraryStateRepositoryPort`.
  - Asset loader utilities/workers and incremental refresh accept/use the query
    service with compatibility fallback construction.
  - Export-all-edited, Albums dashboard metadata, and Location map aggregation
    use the active query service.
- Extended architecture checks so GUI runtime imports of
  `iPhoto.cache.index_store` fail.

## Behavioral Notes

- The GUI still preserves existing fallback behavior for isolated callers and
  tests: if no active session is bound, a local `LibraryAssetQueryService` is
  constructed from the relevant root.
- Asset grid rows remain album-relative when browsing a physical album and
  library-relative when browsing the library root.
- Export-all-edited still scans indexed rows and exports assets that have an
  edit sidecar.
- The concrete index-store repository remains the production source of truth
  until the Phase 2 repository consolidation decision is completed.

## Verification

Run with the project `.venv`:

- `.venv/bin/python tools/check_architecture.py`
- `.venv/bin/python -m pytest tests/architecture -q`
- `.venv/bin/python -m pytest tests/application/test_library_asset_query_service.py tests/application/test_library_session.py tests/application/test_runtime_context.py -q`
- `.venv/bin/python -m pytest tests/services/test_album_metadata_service.py tests/ui/tasks/test_asset_loader_missing_files.py tests/test_library_geotagged_assets.py tests/ui/controllers/test_export_controller.py -q`

Observed warnings are expected to match the existing `Unknown config option:
env` pytest warning and legacy shim deprecation warnings where those tests touch
compatibility code.

## Next Handoff

1. Move People service and face-scan worker direct repository access behind a
   People application port/session surface.
2. Move watcher-triggered live-row reads and remaining trash cleanup helper
   paths behind session/application commands.
3. Resume Phase 2 repository consolidation after the remaining non-GUI runtime
   direct index-store dependencies are behind ports.
4. Add broader temp-library end-to-end tests for import/move/delete/restore and
   user-state preservation across rescans.
