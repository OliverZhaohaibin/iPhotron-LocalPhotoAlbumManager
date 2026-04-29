# 06 - Scan Entry Migration Notes

> Date: 2026-04-29

## Goal

This pass continued the Phase 3 scan-pipeline convergence from
`05-current-progress.md`: CLI scan/report and LibraryManager-driven background
scans now enter through `LibrarySession` and the shared `ScanLibraryUseCase`
instead of each entry point assembling scanner/repository dependencies itself.

The repository consolidation is intentionally not solved here. Scan facts still
persist through the current index-store repository; this pass centralizes the
composition and command surface so later Phase 2 work can swap the repository
behind one boundary.

## What Changed

- Added `LibraryScanService` as the library-scoped scan command surface.
  - `scan_album()` resolves manifest/default filters, loads incremental index
    state, applies library-relative path transforms, and executes
    `ScanLibraryUseCase`.
  - `finalize_scan()` applies final snapshot merge/prune behavior and rebuilds
    Live Photo links/roles.
  - `pair_album()` replaces scan-entry calls to `app.pair()`.
  - `report_album()` gives the CLI a session-backed report path.
- Exposed the service as `LibrarySession.scans`.
- Added `create_headless_library_session()` for non-GUI entry points.
- Updated `RuntimeContext.open_library()` / `close_library()` to bind and clear
  the active scan service on `LibraryManager`.
- Updated `ScannerWorker` to use an injected/default `LibraryScanService`; the
  worker now only adapts Qt progress, cancellation, chunks, and final signals.
- Updated `ScanCoordinatorMixin` scan completion to call
  `LibraryScanService.finalize_scan()` and the background pairing worker to call
  `pair_album()`.
- Updated CLI `scan`, `pair`, and `report` to use a headless session.
- Fixed the CLI error wrapper with `functools.wraps`; without it Typer exposed
  the decorated commands under the duplicate name `wrapper`.

## Behavioral Notes

- Synchronous CLI scanning remains atomic at the command level:
  `scan_album(..., persist_chunks=False)` collects rows first, and
  `finalize_scan()` is called only after the scan succeeds.
- Background GUI scans still stream chunks:
  `ScannerWorker` calls `scan_album(..., persist_chunks=True)` so listeners can
  receive persisted chunks while the scan runs.
- `LibraryUpdateService` now passes the active session scan service into
  `ScannerWorker` when a `LibraryManager` is available, but broader GUI service
  cleanup remains out of scope for this pass.
- `app.py`, import/move incremental scans, restore rescans, `open_album()`
  autoscan, People, Maps, and remaining direct GUI repository reads remain
  migration exceptions for future passes.

## Verification

Run with the project `.venv`:

- `.venv/bin/python tools/check_architecture.py`
- `.venv/bin/python -m pytest tests/architecture -q`
- `.venv/bin/python -m pytest tests/application/test_library_scan_service.py tests/application/test_cli_session_scan.py tests/application/test_library_session.py tests/application/test_runtime_context.py tests/library/test_scanner_worker.py -q`
- `.venv/bin/python -m pytest tests/application/test_app_rescan_atomicity.py tests/test_scanner_adapter.py tests/test_library_live_scan_results.py -q`

Observed warnings are the existing `Unknown config option: env` pytest warning
and legacy model shim deprecation warnings from compatibility paths.

## Next Handoff

1. Move `open_album()` autoscan to `LibraryScanService` / `LibrarySession`, then
   keep `app.open_album()` as a compatibility forwarder.
2. Move import-worker incremental scanning and restore rescans to session scan
   commands.
3. Continue replacing GUI service direct `get_global_repository()` reads/writes
   with session commands or application ports, starting with favorite,
   restore, move/delete, and map aggregation paths.
4. Resume Phase 2 repository consolidation after these scan entry points no
   longer assemble concrete index-store dependencies directly.
