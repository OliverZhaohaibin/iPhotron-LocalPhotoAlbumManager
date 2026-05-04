# 16 - GUI File Operation Command Migration Notes

> Date: 2026-05-01

## Goal

This pass continued the handoff from
`15-gallery-query-read-migration.md`: GUI move/delete/restore services no
longer own durable file-operation planning rules. They now obtain validated
operation plans from a session-owned asset operation command surface and keep
their Qt responsibilities: worker submission, signals, user-facing messages,
and restore prompt forwarding.

## What Changed

- Added `LibraryAssetOperationService`.
  - The new bootstrap/session service plans move, delete, and restore requests
    without depending on Qt.
  - It returns `AssetMovePlan` / `AssetRestorePlan` dataclasses that GUI worker
    code can execute directly.
  - It owns path deduplication, active-root validation, delete Live Photo
    companion expansion, restore metadata lookup, stale-row fallback, album-id
    destination recovery, restore-to-root prompt decisions, and same-stem Live
    Photo restore fallback.
- Extended session binding.
  - `LibrarySession.asset_operations` is created beside the lifecycle service.
  - `RuntimeContext` binds/unbinds the active operation service into
    `LibraryManager`.
  - `LibraryManager` exposes the current `asset_operation_service` for GUI
    adapters.
- Slimmed GUI file-operation services.
  - `AssetMoveService` asks the operation service for a plan and then submits
    `MoveWorker`.
  - `DeletionService` resolves Recently Deleted and forwards the selected
    paths plus optional model metadata lookup.
  - `RestorationService` forwards selected trash paths, optional model metadata
    lookup, and the restore prompt callback, then submits returned restore
    batches.
- Added guardrails.
  - `tools/check_layer_boundaries.py` now fails if
    `deletion_service.py` or `restoration_service.py` directly imports
    `LibraryAssetLifecycleService` or `media_classifier` again.

## Behavioral Notes

- `MoveWorker` still performs actual filesystem moves and sidecar atomicity.
- `LibraryAssetLifecycleService` still owns post-move repository/link side
  effects.
- Restore prompt text and existing move/delete/restore status messages remain
  unchanged.
- No database schema, repository source-of-truth, or legacy use-case removal was
  part of this pass.

## Verification

Run with the project `.venv`:

- `.venv/bin/python -m pytest tests/application/test_library_asset_operation_service.py tests/services/test_asset_move_service.py tests/services/test_restoration_service.py -q`
- `.venv/bin/python -m pytest tests/application/test_library_session.py tests/application/test_runtime_context.py -q`
- `.venv/bin/python -m pytest tests/architecture -q`
- `.venv/bin/python tools/check_architecture.py`

Observed warnings are expected to match the existing pytest config warning and
legacy shim deprecation warnings where compatibility tests touch old model
shims.

## Next Handoff

1. Continue slimming `gui.facade.py` open/rescan/pair routing so it delegates
   to session command/query surfaces with less inline branching.
2. Review remaining legacy scan-like application services before marking Phase
   3 fully complete.
3. Add broader temp-library end-to-end tests for import/move/delete/restore and
   user-state preservation across rescans.
