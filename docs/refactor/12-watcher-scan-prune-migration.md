# 12 - Watcher Scan / Prune Migration Notes

> Date: 2026-04-30

## Goal

This pass continued the handoff from
`11-session-cleanup-live-read-migration.md`: watcher-triggered refresh now
enters the session scan surface, and stale-row pruning is no longer hidden
inside ordinary scan finalization.

Repository consolidation remains intentionally out of scope. The concrete
global index store is still owned by bootstrap/session adapters while Phase 2
decides the final repository source of truth.

## What Changed

- Split scan finalization from stale-row deletion.
  - `LibraryScanService.finalize_scan()` now performs additive scan-fact merge
    and Live Photo link materialization only.
  - `index_sync_service.py` no longer imports `get_global_repository()`; callers
    pass the active repository port into sync helpers.
- Added explicit lifecycle reconciliation.
  - `LibraryAssetLifecycleService.reconcile_missing_scan_rows()` owns scoped
    stale-row pruning after a completed scan.
  - GUI sync/async rescans, restore rescans, import fallback rescans, CLI
    `scan`, legacy `app.rescan()`, and `LibraryManager` scan completion now
    call lifecycle reconciliation explicitly after scan finalization.
- Moved watcher-triggered refresh into the session scan path.
  - `FileSystemWatcherMixin` records changed directories, refreshes the album
    tree on debounce, and then calls the active session scan service for
    filters before starting the scan.
  - Suspended watcher notifications still do not queue scans.
- Extended architecture guardrails.
  - `tools/check_layer_boundaries.py` now fails if `index_sync_service.py`
    imports the concrete index-store module again.

## Behavioral Notes

- Scan merge remains additive and state-preserving. Deleting rows because files
  disappeared is now a lifecycle decision, not a side effect of
  `finalize_scan()`.
- Watcher changes that touch multiple sibling directories are collapsed to a
  root-level scan because `LibraryManager` still owns a single active scanner.
- Import incremental chunks still use `scan_specific_files()` and only use full
  scan reconciliation when the import worker falls back to a full rescan.
- Compatibility callers without an active session still construct fallback scan
  and lifecycle services from the available root.

## Verification

Run with the project `.venv`:

- `.venv/bin/python -m pytest tests/application/test_library_scan_service.py tests/application/test_library_asset_lifecycle_service.py tests/test_index_sync_service.py tests/test_app_live_sync.py -q`
- `.venv/bin/python -m pytest tests/test_app_open_album_lazy.py tests/library/test_rescan_worker_session.py tests/ui/tasks/test_import_worker.py tests/services/test_library_update_service_global_db.py tests/application/test_cli_session_scan.py -q`
- `.venv/bin/python -m pytest tests/test_library_bind_double_scan.py tests/test_library_manager.py -q`
- `.venv/bin/python tools/check_architecture.py`

Observed warnings are expected to match the existing `Unknown config option:
env` pytest warning and legacy shim deprecation warnings where those tests touch
compatibility code.

Results: all passed in this environment.

## Next Handoff

1. Resume Phase 2 repository consolidation: decide the final source of truth
   between `cache/index_store.AssetRepository` and `SQLiteAssetRepository`, then
   collapse callers to `AssetRepositoryPort`.
2. Continue reducing `gui.facade.py`, `library.manager.py`, and GUI services to
   presentation/compatibility surfaces only.
3. Add broader temp-library end-to-end tests for import/move/delete/restore,
   People state preservation, and user-state preservation across rescans.
