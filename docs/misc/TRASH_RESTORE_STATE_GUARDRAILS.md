# Trash Restore State Guardrails

This note protects Recently Deleted, restore metadata, and durable user state.

## Contract

- Normal collections must exclude Recently Deleted rows through `is_deleted`.
- The Recently Deleted collection must still show deleted rows.
- Delete-to-trash must preserve enough restore metadata:
  `original_rel_path`, `original_album_id`, and `original_album_subpath` where
  available.
- Restore must clear trash metadata from the destination row after successful
  lifecycle application.
- Restore planning should use session-bound `plan_restore_request(...)`; do not
  bypass it with GUI-only path guesses.
- Restore must keep Live Photo pairs together only when the restore planner
  identifies the pair. Do not add same-stem motion files for non-Live assets.
- Scan merge and rescan must not implicitly clear durable user decisions such as
  favorites, hidden/trash state, pinned/order, manual metadata, People/Pets state, or
  edit sidecars.

## Focused Checks

```bash
.venv/bin/python -m pytest tests/application/test_temp_library_end_to_end.py tests/application/test_library_asset_lifecycle_service.py tests/services/test_restoration_service.py -q
.venv/bin/python -m pytest tests/cache/test_global_repository.py tests/cache/test_index_store_features.py -q
```
