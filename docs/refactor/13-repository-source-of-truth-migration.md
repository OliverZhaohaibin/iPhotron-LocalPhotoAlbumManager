# 13 - Repository Source-of-Truth Migration Notes

> Date: 2026-04-30

## Goal

This pass resumed Phase 2 repository consolidation. The current runtime source
of truth is now explicit: `cache/index_store.AssetRepository` backed by
`global_index.db` owns library asset facts. `SQLiteAssetRepository` remains in
the tree for legacy/domain repository tests and older use cases, but it is no
longer created by the library-scoped runtime.

## What Changed

- Added `IndexStoreAssetRepositoryAdapter`.
  - It implements the legacy `IAssetRepository` interface over the global index
    store for callers that still need domain `Asset` objects.
  - It supports id/path reads, album/path queries, offset pagination, favorite
    saves, delete-by-id, and hidden Live Photo motion-row filtering.
- Refactored `LibraryAssetRuntime`.
  - Runtime binding no longer creates `ConnectionPool` or
    `SQLiteAssetRepository`.
  - `assets` exposes the session-facing `AssetRepositoryPort`.
  - `repository` remains as a compatibility `IAssetRepository` adapter for
    current GUI/viewmodel callers.
- Refactored `LibrarySession.assets` to return the true session asset port
  instead of the legacy domain adapter.
- Extended layer-boundary checks so `LibraryAssetRuntime` cannot reintroduce
  the old SQLite repository binding.

## Behavioral Notes

- Scan, lifecycle, query, People, and legacy GUI asset reads now converge on the
  same `global_index.db` runtime facts.
- Saving through the compatibility adapter merges with existing index-store rows
  so favorite toggles do not drop scanner-owned metadata such as GPS and
  `face_status`.
- The compatibility adapter is intentionally transitional. New application
  flows should still prefer `AssetRepositoryPort`, session query surfaces, or
  focused application services.
- `SQLiteAssetRepository` is not deleted in this pass because old domain use
  cases and repository tests still depend on it.

## Verification

Run with the project `.venv`:

- `.venv/bin/python -m pytest tests/infrastructure/test_index_store_asset_repository_adapter.py -q`
- `.venv/bin/python -m pytest tests/infrastructure/test_library_asset_runtime.py tests/application/test_library_session.py tests/application/test_runtime_context.py -q`
- `.venv/bin/python -m pytest tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/gui/viewmodels/test_gallery_viewmodel.py -q`
- `.venv/bin/python tools/check_architecture.py`

Observed warnings are expected to match the existing `Unknown config option:
env` pytest warning and legacy shim deprecation warnings where compatibility
tests touch old model shims.

## Next Handoff

1. Migrate or retire old `domain.repositories.IAssetRepository` use cases so
   new application flows depend on `AssetRepositoryPort` or session query
   services.
2. Continue reducing `gui.facade.py`, `library.manager.py`, and GUI services to
   presentation/compatibility surfaces only.
3. Add broader temp-library end-to-end tests for import/move/delete/restore and
   user-state preservation across rescans.
