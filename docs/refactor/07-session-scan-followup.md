# 07 - Session Scan Follow-up Notes

> Date: 2026-04-29

## Goal

This pass finished the next Phase 3 handoff from
`06-scan-entry-migration.md`: album-open preparation, import incremental
scans, and restore rescans now enter through `LibraryScanService` instead of
reassembling scan/index behavior in `app.py` or GUI workers.

The repository consolidation remains intentionally out of scope. The active
scan service still writes to the current global index store; this pass narrows
the entry surface so Phase 2 can later replace that adapter behind one boundary.

## What Changed

- Extended `LibraryScanService`.
  - `prepare_album_open()` performs scoped lazy counts, optional autoscan,
    link materialization, and legacy manifest favorite sync.
  - `count_assets()` and `read_scoped_assets()` give GUI callers scoped index
    reads without exposing the concrete repository singleton.
  - `scan_specific_files()` handles import chunk refresh through the shared
    scan merge path and writes library-relative rows for subalbums.
- Slimmed legacy `app.py` scan functions.
  - `open_album()`, `rescan()`, `scan_specific_files()`, and `pair()` now create
    a `LibraryScanService` and forward to it while preserving public signatures.
- Moved GUI scan entry points.
  - `AppFacade.open_album()` opens the legacy album model directly, then asks
    the active scan service to prepare index state and decide whether a
    background scan is needed.
  - `ImportWorker` now accepts an optional scan service for chunk scanning,
    pairing, and full-rescan fallback.
  - `RescanWorker` now accepts an optional scan service for restore refreshes.
  - `LibraryUpdateService` passes the active manager-bound session scan service
    into import/restore workers when available.

## Behavioral Notes

- Lazy album open still avoids hydrating all rows. It performs a scoped count;
  when the count is zero and autoscan is disabled, it materializes empty link
  state to preserve legacy behavior.
- App compatibility wrappers still support callers that do not have a
  `RuntimeContext` / `LibrarySession`; they construct a local scan service using
  the passed `library_root` or album root.
- Import workers continue to prefer incremental chunk merge plus Live Photo
  pairing. If a chunk merge or pairing fails, the worker falls back to a full
  scan through the same service.
- Restore rescans now use the session scan service, but restore destination
  metadata lookup still reads the global repository directly in
  `RestorationService`.

## Verification

Run with the project `.venv`:

- `.venv/bin/python tools/check_architecture.py`
- `.venv/bin/python -m pytest tests/architecture -q`
- `.venv/bin/python -m pytest tests/application/test_library_scan_service.py tests/test_app_open_album_lazy.py tests/ui/tasks/test_import_worker.py tests/services/test_library_update_service_global_db.py tests/services/test_asset_import_service.py -q`
- `.venv/bin/python -m pytest tests/library/test_rescan_worker_session.py tests/test_app_facade_session_open.py -q`
- `.venv/bin/python -m pytest tests/application/test_app_rescan_atomicity.py tests/library/test_scanner_worker.py -q`

Observed warnings are the existing `Unknown config option: env` pytest warning
and legacy model shim deprecation warnings.

## Next Handoff

1. Move `MoveWorker` index mutations and Live Photo pairing calls into a
   session/application lifecycle command.
2. Replace direct GUI `get_global_repository()` usage for favorite, restore
   metadata lookup, map aggregation, export reads, and asset loading with
   session commands or application ports.
3. Resume Phase 2 repository consolidation once GUI lifecycle flows no longer
   assemble concrete index-store dependencies directly.
4. Add temp-library end-to-end tests for import/move/delete/restore preserving
   favorite/trash/original-location state across rescans.
