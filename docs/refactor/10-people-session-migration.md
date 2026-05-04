# 10 - People Session Migration Notes

> Date: 2026-04-30

## Goal

This pass continued the handoff from
`09-gui-session-query-migration.md`: People service, People coordinator, and the
face-scan worker now reach asset rows and `face_status` bookkeeping through a
session/application boundary instead of importing the global index-store
singleton directly.

Repository consolidation remains intentionally out of scope. `global_index.db`
is still the production source of truth for asset rows, but People runtime code
now reaches it through the bootstrap-level adapter only.

## What Changed

- Added the People asset repository port.
  - `PeopleAssetRepositoryPort` covers asset-row reads, pending/retry reads,
    single/batch `face_status` updates, and face-status counts.
  - The existing `PeopleIndexPort` remains the People index boundary.
- Added `bootstrap/library_people_service.py`.
  - Owns `IndexStorePeopleAssetRepository`, the only People adapter that imports
    `get_global_repository()`.
  - Provides `create_people_asset_repository()` and `create_people_service()`
    for session and compatibility construction.
- Exposed People from the active library session.
  - `LibrarySession.people` is created with the active library root.
  - `RuntimeContext.open_library()` binds the session People service into
    `LibraryManager`; `close_library()` clears it.
- Refactored People runtime code.
  - `PeopleService` accepts injected asset repository/coordinator dependencies.
  - `PeopleIndexCoordinator` uses the injected asset repository for post-commit
    `face_status` updates.
  - `FaceScanWorker` uses the session People service for row reads and status
    bookkeeping.
- Refactored key GUI call sites to use the session-bound People service.
  - Main/navigation coordinators, People dashboard loading, playback manual-face
    flow, context menu group cover flow, gallery cluster refresh, pinned items,
    and album tree People entries now avoid direct People/index construction
    where a session service is available.
  - Compatibility root-based construction now goes through
    `create_people_service(root)` so group covers and manual-face validation can
    see asset rows.
- Extended architecture guardrails.
  - `src/iPhoto/people/**` and
    `src/iPhoto/library/workers/face_scan_worker.py` may not runtime-import
    `iPhoto.cache.index_store`.

## Behavioral Notes

- Group dashboard cards now use `PeopleGroupSummary.cover_asset_path` when the
  service can resolve a common group asset. The collage remains only the visual
  fallback when no group-cover asset is available.
- `PeopleDashboardWidget.set_library_root()` rebuilds through the People
  service factory when a real root becomes available, preventing an unbound
  placeholder `PeopleService` from causing permanent collage fallbacks.
- `MainCoordinator` no longer calls `create_people_service(None)` during startup
  with no active library root; it uses an unbound placeholder service until a
  real session/root is available.
- Isolated tests and legacy callers may still create an unbound `PeopleService`,
  but that compatibility path does not import the concrete index store.

## Verification

Run with the project `.venv`:

- `.venv/bin/python tools/check_architecture.py`
- `.venv/bin/python -m pytest tests/architecture -q`
- `.venv/bin/python -m pytest tests/test_people_service.py tests/application/test_library_people_service.py tests/application/test_library_session.py tests/application/test_runtime_context.py -q`
- `.venv/bin/python -m pytest tests/gui/widgets/test_people_dashboard_widget.py tests/gui/coordinators/test_playback_coordinator.py -q`
- `.venv/bin/python -m pytest tests/test_navigation_coordinator_cluster_gallery.py tests/gui/viewmodels/test_gallery_viewmodel.py tests/ui/controllers/test_context_menu_cover.py -q`

Observed warnings are expected to match the existing `Unknown config option:
env` pytest warning and legacy shim deprecation warnings where those tests touch
compatibility code.

## Next Handoff

1. Move watcher-triggered live-row reads and remaining trash cleanup helper
   paths behind session/application commands.
2. Resume Phase 2 repository consolidation: decide the final source of truth
   between `cache/index_store.AssetRepository` and `SQLiteAssetRepository`, then
   collapse callers to `AssetRepositoryPort`.
3. Continue reducing `gui.facade.py`, `library.manager.py`, and GUI services to
   presentation/compatibility surfaces only.
4. Add broader temp-library end-to-end tests for import/move/delete/restore,
   People state preservation, and user-state preservation across rescans.
