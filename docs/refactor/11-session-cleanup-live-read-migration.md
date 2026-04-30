# 11 - Session Cleanup / Live Read Migration Notes

> Date: 2026-04-30

## Goal

This pass continued the handoff from
`10-people-session-migration.md`: Recently Deleted cleanup and live scan
database fallback reads now go through session-owned application surfaces
instead of direct library-layer access to the global index-store singleton.

Repository consolidation remains intentionally out of scope. The concrete
global index store is still owned by bootstrap/session adapters while Phase 2
decides the final repository source of truth.

## What Changed

- Extended `LibraryAssetLifecycleService`.
  - Added `cleanup_deleted_index(trash_root)` as the public cleanup command for
    stale Recently Deleted rows.
  - Kept cleanup best-effort: repository and filesystem errors are logged and
    return `0` removals.
- Refactored `TrashManagerMixin.cleanup_deleted_index()`.
  - The library manager now delegates to the active session lifecycle service.
  - Isolated compatibility callers still construct a lifecycle service from the
    bound library root.
- Extended `LibraryAssetQueryService`.
  - Added `read_library_relative_asset_rows(root)` for callers that need scoped
    reads without stripping the library-relative `rel` prefix.
- Refactored `ScanCoordinatorMixin.get_live_scan_results()`.
  - Empty-buffer live scan fallback reads now use the session query service.
  - `src/iPhoto/library/scan_coordinator.py` and
    `src/iPhoto/library/trash_manager.py` no longer import the concrete index
    store.
- Hardened delete flows against stale source paths.
  - Delete validation skips already-missing sources instead of surfacing a modal
    `File not found` error.
  - The move worker also treats missing delete sources as a benign race, while
    ordinary move/restore operations still emit errors.
  - Context-menu deletion now releases preview/player handles before requesting
    deletion, waits for the backend to accept the delete worker, then applies
    optimistic model mutation.
  - Delete/move services now return whether a worker was queued so UI callers do
    not hide rows or show a `Deleted` toast when validation rejected the request.
- Hardened restore flows for legacy/broken trash metadata.
  - Restore can recover from files that exist under `.Trash` while the index
    still only has their pre-delete rows.
  - Live Photo restore adds same-stem motion files from `.Trash` when model
    metadata does not expose the companion path.
  - The main coordinator now registers the restore-to-library-root prompt so
    unknown original locations can still be restored with user confirmation.

## Behavioral Notes

- Opening Recently Deleted still schedules background cleanup through the
  navigation coordinator, but persistence rules are now owned by the lifecycle
  service.
- Live scan fallback rows remain library-relative until the scan coordinator
  rewrites them for the active view, preserving parent/child album behavior.
- Missing source files during delete are treated as already gone. If every
  selected file is missing, no worker is queued and the operation completes as
  a no-op.
- Missing source files during ordinary move or restore still produce the
  existing error signal.
- Right-click delete no longer mutates the gallery before the deletion service
  has inspected the selected model rows. This preserves Live Photo companion
  lookup and avoids showing successful delete feedback when no worker was
  scheduled.
- Restore no longer skips a trashed file solely because the `.Trash/...` index
  row is missing. It first tries the stale original row, then falls back to the
  existing restore-to-root confirmation.

## Verification

Run with the project `.venv`:

- `.venv/bin/python tools/check_architecture.py`
- `.venv/bin/python -m pytest tests/architecture -q`
- `.venv/bin/python -m pytest tests/application/test_library_asset_lifecycle_service.py tests/test_library_manager_cleanup.py tests/test_library_live_scan_results.py tests/application/test_library_asset_query_service.py tests/application/test_library_session.py tests/application/test_runtime_context.py -q`
- `.venv/bin/python -m pytest tests/services/test_asset_move_service.py tests/ui/controllers/test_context_menu_operations.py -q`
- `.venv/bin/python -m pytest tests/application/test_library_asset_lifecycle_service.py tests/test_library_manager_cleanup.py tests/test_library_live_scan_results.py tests/application/test_library_asset_query_service.py tests/application/test_library_session.py tests/application/test_runtime_context.py tests/services/test_asset_move_service.py tests/ui/controllers/test_context_menu_operations.py -q`
- `.venv/bin/python -m pytest tests/services/test_restoration_service.py tests/application/test_library_asset_lifecycle_service.py tests/services/test_asset_move_service.py -q`

Observed warnings are expected to match the existing `Unknown config option:
env` pytest warning and legacy shim deprecation warnings where those tests touch
compatibility code.

Results: all passed in this environment.

## Next Handoff

1. Move the remaining watcher-triggered incremental scan/refresh orchestration
   behind session/application commands.
2. Resume Phase 2 repository consolidation: decide the final source of truth
   between `cache/index_store.AssetRepository` and `SQLiteAssetRepository`, then
   collapse callers to `AssetRepositoryPort`.
3. Continue reducing `gui.facade.py`, `library.manager.py`, and GUI services to
   presentation/compatibility surfaces only.
4. Add broader temp-library end-to-end tests for import/move/delete/restore,
   People state preservation, and user-state preservation across rescans.
