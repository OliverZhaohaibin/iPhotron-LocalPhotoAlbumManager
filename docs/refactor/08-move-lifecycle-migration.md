# 08 - Move Lifecycle Migration Notes

> Date: 2026-04-29

## Goal

This pass finished the next Phase 3 handoff from
`07-session-scan-followup.md`: move/delete/restore index mutations and Live
Photo pairing now enter through a session-owned lifecycle service instead of
being assembled inside the Qt `MoveWorker`.

Repository consolidation remains intentionally out of scope. The lifecycle
service still writes through the current global index-store repository, but the
entry point is now owned by `LibrarySession` so Phase 2 can later replace that
adapter behind one boundary.

## What Changed

- Added `LibraryAssetLifecycleService`.
  - Removes source rows and caches metadata before destination insertion.
  - Reuses cached source metadata for moved files and scans only uncached
    destination files.
  - Annotates delete-to-trash rows with original relative path, album UUID, and
    original album subpath.
  - Clears restore metadata when rows are restored out of Recently Deleted.
  - Cleans stale Recently Deleted rows before inserting new trash rows.
  - Runs Live Photo pairing once after the lifecycle update.
- Exposed the lifecycle service as `LibrarySession.asset_lifecycle`.
  - `RuntimeContext.open_library()` binds it into `LibraryManager`.
  - `RuntimeContext.close_library()` clears it with the scan service.
- Refactored GUI worker/service entry points.
  - `AssetMoveService` passes the active lifecycle service into `MoveWorker`.
  - `MoveWorker` now handles file moves, cancellation, progress, and result
    signals; index writes and pairing are delegated to the lifecycle service.
  - `RestorationService` reads restore metadata through the lifecycle service.
  - `LibraryUpdateService` preserves trash restore metadata through the same
    lifecycle service during restore rescans.
- Extended `AssetRepositoryPort` with row append/remove/read-by-rel operations
  used by lifecycle commands.

## Behavioral Notes

- Existing isolated callers remain supported: when a worker or service has no
  active session, it constructs a compatibility lifecycle service using the
  available library root.
- Restore operations no longer carry `original_rel_path`,
  `original_album_id`, or `original_album_subpath` values into the restored
  destination row.
- Delete operations still require a library root to annotate trash metadata
  safely.
- The concrete persistence implementation is unchanged; this pass narrows the
  command surface before repository consolidation.

## Verification

Run with the project `.venv`:

- `.venv/bin/python tools/check_architecture.py`
- `.venv/bin/python -m pytest tests/architecture -q`
- `.venv/bin/python -m pytest tests/services/test_asset_move_service.py tests/services/test_library_update_service_global_db.py tests/cache/test_move_delete_optimizations.py tests/application/test_library_scan_service.py tests/application/test_library_asset_lifecycle_service.py tests/services/test_restoration_service.py tests/application/test_library_session.py tests/application/test_runtime_context.py -q`

Observed warnings are the existing `Unknown config option: env` pytest warning
and legacy model shim deprecation warnings.

## Next Handoff

1. Replace remaining GUI direct global-repository reads for favorites, map
   aggregation, export, and asset loading with session commands or application
   ports.
2. Move People and face-scan repository access behind bounded-context ports.
3. Move watcher-triggered incremental refresh and trash cleanup helpers behind
   session/application commands.
4. Resume Phase 2 repository consolidation once these remaining GUI lifecycle
   flows no longer assemble concrete index-store dependencies directly.
5. Add broader temp-library end-to-end tests for import/move/delete/restore
   preserving favorite/trash/original-location state across rescans.
