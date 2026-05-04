# 14 - Legacy Domain Repository Retirement Notes

> Date: 2026-04-30

## Goal

This pass continued the handoff from
`13-repository-source-of-truth-migration.md`: active GUI favorite writes no
longer save through the legacy `domain.repositories.IAssetRepository` adapter.
The old domain-repository use case graph remains available for compatibility
tests and isolated callers, but new runtime code is now guarded away from it.

## What Changed

- Extended `AssetService`.
  - Added `bind_library_surfaces()` and `clear_library_surfaces()`.
  - When session surfaces are bound, `toggle_favorite_by_path()` resolves a
    library-relative `rel`, reads the current state from the session query
    surface, and writes the new favorite state through
    `LibraryStateRepositoryPort`.
  - When no session surfaces are bound, existing legacy repository fallback
    behavior is preserved.
- Refactored `MainCoordinator` binding.
  - Startup and library-tree rebinding now bind the active session state/query
    surfaces into `AssetService`.
  - The gallery model still uses the transitional legacy adapter for reads;
    this pass only retires active favorite writes from that adapter.
- Quarantined legacy domain use cases.
  - `application/use_cases/__init__.py` and `bootstrap/container.py` are now
    explicitly documented as compatibility-only surfaces.
  - `tools/check_layer_boundaries.py` fails if runtime code imports the old
    domain-repository use cases outside the compatibility allowlist.

## Behavioral Notes

- Favorite toggles from gallery/detail now update `global_index.db` through the
  session state boundary. User state remains in the current index-store
  database; this pass does not introduce a separate state database.
- Missing indexed rows during a session-bound favorite toggle return `False`
  and skip the write, matching the old "asset not found" fallback behavior.
- `SQLiteAssetRepository` is intentionally retained for legacy/domain tests and
  old use cases. It is still not the library-scoped runtime asset repository.
- `io/scanner_adapter.py` remains an explicit allowlisted bridge because it
  still reuses the legacy `FileDiscoveryThread` helper during scan migration.

## Verification

Run with the project `.venv`:

- `.venv/bin/python -m pytest tests/application/test_album_service_facade.py tests/application/test_library_asset_query_service.py tests/gui/viewmodels/test_gallery_viewmodel.py tests/gui/viewmodels/test_detail_viewmodel.py -q`
- `.venv/bin/python -m pytest tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py tests/infrastructure/test_index_store_asset_repository_adapter.py -q`
- `.venv/bin/python -m pytest tests/test_phase4_integration.py -q`
- `.venv/bin/python tools/check_architecture.py`

Observed warnings are expected to match the existing pytest config warning and
legacy shim deprecation warnings where compatibility tests touch old model
shims.

## Next Handoff

1. Migrate gallery collection/query reads from the legacy `IAssetRepository`
   adapter to a session query surface.
2. Continue reducing `gui.facade.py`, `library.manager.py`, and GUI services to
   presentation/compatibility surfaces only.
3. Add broader temp-library end-to-end tests for import/move/delete/restore and
   user-state preservation across rescans.
