# 05 - Current Progress

> Last updated: 2026-04-30

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

## Completed In Move Lifecycle Migration

- Added `LibraryAssetLifecycleService` as the session-owned command surface for
  move/delete/restore index updates and post-move Live Photo pairing.
- Exposed the lifecycle service from `LibrarySession` and bound it into
  `LibraryManager` from `RuntimeContext.open_library()` / `close_library()`.
- Extended `AssetRepositoryPort` with row append/remove/read-by-rel operations
  needed by lifecycle commands.
- Refactored `MoveWorker` so it only moves files and emits Qt progress; source
  row removal, destination row merge, trash annotation, stale trash cleanup, and
  pairing now run through the lifecycle service.
- Refactored `RestorationService` restore metadata lookup and
  `LibraryUpdateService` trash metadata preservation to use the lifecycle
  service with compatibility fallback construction.
- Added lifecycle and restoration tests covering move metadata reuse, delete
  annotations, restore cleanup, stale trash cleanup, metadata preservation, and
  session service routing.
- Added `docs/refactor/08-move-lifecycle-migration.md` as the process handoff
  for this pass.

## Completed In GUI Session Query Migration

- Added `LibraryAssetQueryService` as the session-owned query surface for
  scoped counts, lightweight geometry rows, full asset rows, and geotagged rows.
- Exposed the query surface from `LibrarySession` and bound it, along with the
  durable state repository, into `LibraryManager` from
  `RuntimeContext.open_library()` / `close_library()`.
- Refactored GUI favorite writes, asset grid reads, export reads, Albums
  dashboard metadata, and Location map aggregation to use session query/state
  surfaces instead of direct `get_global_repository()` imports.
- Extended architecture checks so GUI runtime imports of the concrete index
  store fail.
- Added `docs/refactor/09-gui-session-query-migration.md` as the process
  handoff for this pass.

## Completed In People Session Migration

- Added `PeopleAssetRepositoryPort` for People-owned asset-row reads,
  pending/retry face-scan reads, single/batch `face_status` updates, and
  face-status counts.
- Added `bootstrap/library_people_service.py` as the People session assembly
  point and the only People adapter that imports the current global index-store
  singleton.
- Exposed `LibrarySession.people` and bound/unbound the active People service
  through `RuntimeContext.open_library()` / `close_library()` and
  `LibraryManager`.
- Refactored `PeopleService`, `PeopleIndexCoordinator`, and `FaceScanWorker` so
  asset row validation, group cover resolution, pending/retry reads, and
  post-commit `face_status` bookkeeping use injected ports/session services.
- Refactored key GUI People callers to prefer the session-bound People service,
  including dashboard loading, navigation, playback manual-face flow, context
  menu covers, gallery cluster refresh, pinned items, and album tree entries.
- Fixed the rootless startup path so `MainCoordinator` no longer calls
  `create_people_service(None)`, and fixed People dashboard root rebinding so
  groups can resolve shared-photo covers instead of permanently falling back to
  collage art.
- Extended architecture checks so People runtime code and the face-scan worker
  cannot import the concrete index store directly.
- Added `docs/refactor/10-people-session-migration.md` as the process handoff
  for this pass.

## Current Phase Status

- Phase 0 is partially complete: vNext docs are in place and architecture
  guardrails now cover application/concrete imports, lower-layer GUI imports,
  GUI concrete index-store imports, and new legacy model shim imports.
- Phase 1 is partially complete: `LibrarySession` exists and is reachable from
  runtime entry objects; scan, asset lifecycle, asset query, durable state, and
  People session surfaces are bound into `LibraryManager`, but GUI/coordinators/
  viewmodels still need broader session-surface migration.
- Phase 2 is partially complete: repository/state ports exist and Assign
  Location uses the state boundary; lifecycle row operations are now on the
  asset repository port, but asset persistence is not yet fully collapsed to
  one public repository implementation.
- Phase 3 is partially complete: `ScannerWorker`, LibraryManager scan
  coordinator paths, CLI scan/report, app compatibility scan calls,
  `AppFacade.open_album()`, import incremental scans, and restore rescans now
  enter through `LibrarySession` / `LibraryScanService` and
  `ScanLibraryUseCase`; move/delete/restore index updates now enter through
  `LibraryAssetLifecycleService`, while watcher lifecycle cleanup still needs
  migration.
- Phase 4 is partially complete: GUI favorite writes, asset grid reads, export
  reads, dashboard metadata, Location map aggregation, and key People dashboard/
  navigation/manual-face flows now use session surfaces, but `gui.facade.py` and
  GUI services still carry legacy business orchestration.
- Phase 5 is partially complete: thumbnail, Assign Location, and People
  boundaries were improved; Maps, Edit sidecar, and full thumbnail renderer
  ports still need deeper migration.
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
- `LibraryAssetLifecycleService` still uses the current index-store repository
  as the move/delete/restore lifecycle source of truth. This is intentional
  until repository consolidation is completed.
- People still uses `global_index.db` as its asset-row source of truth through
  the bootstrap/session adapter. `src/iPhoto/people/**` and the face-scan worker
  should not import `get_global_repository()` directly.
- Trash cleanup helpers, watcher refresh paths, and non-GUI compatibility paths
  still access the global repository directly or through compatibility adapters.
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

Additional move lifecycle migration verification:

- `.venv/bin/python tools/check_architecture.py`
- `.venv/bin/python -m pytest tests/architecture -q`
- `.venv/bin/python -m pytest tests/services/test_asset_move_service.py tests/services/test_library_update_service_global_db.py tests/cache/test_move_delete_optimizations.py tests/application/test_library_scan_service.py tests/application/test_library_asset_lifecycle_service.py tests/services/test_restoration_service.py tests/application/test_library_session.py tests/application/test_runtime_context.py -q`

Results: all passed with the same existing pytest config and legacy shim
warnings.

Additional GUI session query migration verification:

- `.venv/bin/python tools/check_architecture.py`
- `.venv/bin/python -m pytest tests/architecture -q`
- `.venv/bin/python -m pytest tests/application/test_library_asset_query_service.py tests/application/test_library_session.py tests/application/test_runtime_context.py -q`
- `.venv/bin/python -m pytest tests/services/test_album_metadata_service.py tests/ui/tasks/test_asset_loader_missing_files.py tests/test_library_geotagged_assets.py tests/ui/controllers/test_export_controller.py -q`

Results: all passed with the same existing pytest config and legacy shim
warnings.

Additional People session migration verification:

- `.venv/bin/python tools/check_architecture.py`
- `.venv/bin/python -m pytest tests/architecture -q`
- `.venv/bin/python -m pytest tests/test_people_service.py tests/application/test_library_people_service.py tests/application/test_library_session.py tests/application/test_runtime_context.py -q`
- `.venv/bin/python -m pytest tests/gui/widgets/test_people_dashboard_widget.py tests/gui/coordinators/test_playback_coordinator.py -q`
- `.venv/bin/python -m pytest tests/test_navigation_coordinator_cluster_gallery.py tests/gui/viewmodels/test_gallery_viewmodel.py tests/ui/controllers/test_context_menu_cover.py -q`

Results: all passed with the same existing pytest config and legacy shim
warnings.

The previous foundation pass also ran broader application/cache/scan suites and
a broad non-GUI/UI run (`1237 passed, 7 skipped`), excluding
`tests/test_aspect_ratio_constraint.py` because this environment lacks the
`qtbot` fixture.

## Next Handoff Steps

1. Move watcher-triggered incremental refresh and remaining trash cleanup helper
   paths behind session/application commands.
2. Decide the final source of truth between `cache/index_store.AssetRepository`
   and `SQLiteAssetRepository`, then collapse callers to `AssetRepositoryPort`.
3. Expand end-to-end temp-library tests for import/move/delete/restore and
   user-state preservation across rescans.
