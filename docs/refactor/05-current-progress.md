# 05 - Current Progress

> Last updated: 2026-04-29

## Summary

This document tracks the active vNext refactor state. The app has not completed
all six roadmap phases, but new work now has executable boundaries for the next
migration steps.

## Completed In This Pass

- Added `application/ports/` with vNext protocol boundaries for repositories,
  scanner/metadata/thumbnail/edit, People, Maps, and task scheduling.
- Added `LibrarySession` and exposed it from `RuntimeContext` and the legacy
  `AppContext` proxy.
- Added a library-state infrastructure adapter,
  `IndexStoreLibraryStateRepository`, backed by the current global index store.
- Refactored `AssignLocationService` so application code no longer imports
  `get_global_repository()` or concrete metadata readers/writers.
- Added `ExifToolLocationMetadataService` as the infrastructure adapter for
  explicit GPS write/read-back behavior.
- Moved thumbnail infrastructure off the GUI geo-utils shim and onto
  `iPhoto.core.geo_utils`.
- Added `ScanLibraryUseCase` and `FilesystemMediaScanner`; `ScannerWorker` uses
  the new scan orchestration for chunk persistence, progress, cancellation, and
  batch-failure accounting, while `app.rescan()` reuses discovery and defers
  persistence until the scan completes.
- Added executable layer-boundary checks and wired them into
  `tools/check_architecture.py` and GitHub Actions.

## Completed In Scan Entry Migration

- Added `LibraryScanService` as the session-owned scan command surface for
  scan, finalize, pair, and report behavior.
- Exposed the service from `LibrarySession.scans` and added
  `create_headless_library_session()` for CLI/non-GUI entry points.
- Bound the active session scan service into `LibraryManager` from
  `RuntimeContext.open_library()` and cleared it from `close_library()`.
- Refactored `ScannerWorker` so Qt threading/cancel/progress/chunk signals wrap
  the session scan service instead of assembling scanner/repository dependencies
  inside the worker.
- Refactored `ScanCoordinatorMixin` scan completion and background pairing to
  call the session scan service instead of `app.py` scan helpers.
- Refactored CLI `scan`, `pair`, and `report` to use a headless
  `LibrarySession`; `cli.py` no longer imports `app.py` or
  `get_global_repository()`.
- Fixed the CLI command wrapper so Typer preserves command names instead of
  registering decorated commands as `wrapper`.
- Added `docs/refactor/06-scan-entry-migration.md` as the process handoff for
  this migration.

## Completed In Session Scan Follow-up

- Extended `LibraryScanService` with lazy album-open preparation, scoped asset
  counts/reads, specific-file scan merge, and manifest favorite compatibility
  sync.
- Slimmed `app.open_album()`, `app.rescan()`, `app.scan_specific_files()`, and
  `app.pair()` into legacy forwarders that call the session scan service.
- Moved `AppFacade.open_album()` off `app.open_album()` and direct global
  repository reads; it now opens the legacy album model and asks the active
  scan service to prepare the scoped index.
- Refactored import chunk refresh and restore rescans to accept/use the active
  `LibraryScanService`, with compatibility fallback service construction for
  older isolated callers.
- Added `docs/refactor/07-session-scan-followup.md` as the process handoff for
  this pass.

## Current Phase Status

- Phase 0 is partially complete: vNext docs are in place and architecture
  guardrails now cover application/concrete imports, lower-layer GUI imports,
  and new legacy model shim imports.
- Phase 1 is partially complete: `LibrarySession` exists and is reachable from
  runtime entry objects, but GUI/coordinators/viewmodels still need broader
  session-surface migration.
- Phase 2 is partially complete: repository/state ports exist and Assign
  Location uses the state boundary, but asset persistence is not yet fully
  collapsed to one public repository port.
- Phase 3 is partially complete: `ScannerWorker`, LibraryManager scan
  coordinator paths, CLI scan/report, app compatibility scan calls,
  `AppFacade.open_album()`, import incremental scans, and restore rescans now
  enter through `LibrarySession` / `LibraryScanService` and
  `ScanLibraryUseCase`; move/delete index updates and watcher lifecycle cleanup
  still need migration.
- Phase 4 is not complete: `gui.facade.py` and GUI services still carry legacy
  business orchestration and direct global repository access.
- Phase 5 is partially complete: thumbnail and Assign Location boundaries were
  improved; People, Maps, Edit sidecar, and full thumbnail renderer ports still
  need deeper migration.
- Phase 6 is partially complete: new tests and CI architecture check were added,
  but broad end-to-end and performance baselines remain open.

## Known Migration Exceptions

- Existing legacy model shim imports remain allowlisted in the architecture
  checker. Do not add new `iPhoto.models.*` runtime imports outside that list.
- `app.py`, `gui.facade.py`, `library.manager.py`, and several GUI services are
  still compatibility surfaces, not finished vNext entry points.
- `LibraryScanService` still uses the current index-store repository as the
  scan facts source of truth. This is intentional until the Phase 2 repository
  consolidation decision is completed.
- Move/delete index updates, restore metadata lookup, favorite updates, map
  aggregation, export reads, and several GUI asset-loading paths still access
  the global repository directly or through compatibility paths.
- User state still physically lives in `global_index.db`; the new state port is
  an API boundary, not a separate `library_state.db` migration.
- `global_index.db` compatibility schemas may not have a `metadata` column; the
  state adapter preserves best-effort behavior when the column is absent.

## Verification Run

Latest scan-entry migration verification run with the project `.venv`:

- `.venv/bin/python tools/check_architecture.py`
- `.venv/bin/python -m pytest tests/architecture -q`
- `.venv/bin/python -m pytest tests/application/test_library_scan_service.py tests/application/test_cli_session_scan.py tests/application/test_library_session.py tests/application/test_runtime_context.py tests/library/test_scanner_worker.py -q`
- `.venv/bin/python -m pytest tests/application/test_app_rescan_atomicity.py tests/test_scanner_adapter.py tests/test_library_live_scan_results.py -q`

Results: all latest checks passed. Pytest still emits the existing
`Unknown config option: env` warning, plus legacy shim deprecation warnings in
some scan/cache tests.

Additional session scan follow-up verification:

- `.venv/bin/python -m pytest tests/application/test_library_scan_service.py tests/test_app_open_album_lazy.py tests/ui/tasks/test_import_worker.py tests/services/test_library_update_service_global_db.py tests/services/test_asset_import_service.py -q`
- `.venv/bin/python -m pytest tests/library/test_rescan_worker_session.py tests/test_app_facade_session_open.py -q`
- `.venv/bin/python -m pytest tests/application/test_app_rescan_atomicity.py tests/library/test_scanner_worker.py -q`

Results: all passed with the same existing pytest config and legacy shim
warnings.

The previous foundation pass also ran broader application/cache/scan suites and
a broad non-GUI/UI run (`1237 passed, 7 skipped`), excluding
`tests/test_aspect_ratio_constraint.py` because this environment lacks the
`qtbot` fixture.

## Next Handoff Steps

1. Replace direct `get_global_repository()` calls in GUI services/tasks with
   session commands or application ports, starting with favorite, restore,
   move/delete, and map aggregation paths.
2. Move move/delete index mutations and Live Photo pairing calls out of
   `MoveWorker` into a session/application lifecycle command.
3. Decide the final source of truth between `cache/index_store.AssetRepository`
   and `SQLiteAssetRepository`, then collapse callers to `AssetRepositoryPort`.
4. Expand end-to-end temp-library tests for import/move/delete/restore and
   user-state preservation across rescans.
