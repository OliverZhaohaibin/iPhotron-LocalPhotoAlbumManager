# 15 - Gallery Query Read Migration Notes

> Date: 2026-04-30

## Goal

This pass completed the first handoff item from
`14-legacy-domain-repository-retirement.md`: gallery collection and windowed
reads no longer load through the legacy `domain.repositories.IAssetRepository`
adapter. The gallery model now reads through the session-owned
`LibraryAssetQueryService`.

## What Changed

- Extended `LibraryAssetQueryService`.
  - Added `count_query_assets()` and `read_query_asset_rows()`.
  - The query surface now understands `AssetQuery` for album/all-photos reads,
    favorite/video/live smart collections, and People asset-id clusters.
  - Recently Deleted remains excluded from normal all-photos style queries and
    included only when explicitly querying that collection.
- Refactored gallery collection reads.
  - `GalleryCollectionStore` now depends on a session query surface instead of
    `IAssetRepository`.
  - Windowed paging, lazy row fetch, direct asset clusters, pending moves, and
    scan refresh handling keep their existing behavior.
  - `GalleryListModelAdapter` creates and rebinds the store with
    `asset_query_service`.
- Refactored `MainCoordinator` binding.
  - Startup and library-tree rebinding now pass
    `context.library.asset_query_service` into the gallery model path.
  - `AssetService` still receives the compatibility repository for legacy
    fallback behavior and non-gallery paths.
- Added an architecture guard.
  - GUI viewmodel/model modules now fail the architecture check if they import
    `iPhoto.domain.repositories`.

## Behavioral Notes

- `global_index.db` remains the runtime asset facts source of truth.
- This pass does not delete `SQLiteAssetRepository` or
  `IndexStoreAssetRepositoryAdapter`; they remain for compatibility tests and
  old domain-repository use cases.
- Rootless startup keeps an empty gallery store until a library session binds a
  query surface.
- The gallery now converts index rows directly to `AssetDTO`, so session query
  behavior is the source of truth for gallery ordering and filtering.

## Verification

Run with the project `.venv`:

- `.venv/bin/python -m pytest tests/application/test_library_asset_query_service.py tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/gui/viewmodels/test_gallery_viewmodel.py -q`
- `.venv/bin/python -m pytest tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/test_phase4_integration.py -q`
- `.venv/bin/python -m pytest tests/cache/test_sqlite_store.py -q`
- `.venv/bin/python -m pytest tests/architecture -q`
- `.venv/bin/python -m pytest tests/ui/tasks/test_asset_loader_missing_files.py -q`
- `.venv/bin/python tools/check_architecture.py`

Observed warnings are expected to match the existing pytest config warning and
legacy shim deprecation warnings where compatibility tests touch old model
shims.

## Next Handoff

1. Continue reducing `gui.facade.py`, `library.manager.py`, and GUI services to
   presentation/compatibility surfaces only.
2. Review remaining legacy scan-like application services before marking Phase
   3 fully complete.
3. Add broader temp-library end-to-end tests for import/move/delete/restore and
   user-state preservation across rescans.
