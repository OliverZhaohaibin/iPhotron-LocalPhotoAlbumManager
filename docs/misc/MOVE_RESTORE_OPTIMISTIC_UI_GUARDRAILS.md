# Move Restore Optimistic UI Guardrails

This note protects the Gallery optimistic update layer for move, delete, and
restore operations.

## Contract

- Apply optimistic UI updates only after the backend accepts and queues the
  operation.
- Build move overlays and remove fallback selection rows only for the submitted
  plan's sources. Skipped or rejected files must not acquire pending move state.
- Delete/move/restore must preserve pending overlays across aggregate views,
  physical albums, favorites, videos, Recently Deleted, and deep windows.
- Pending source rows must be hidden from source queries, and pending
  destination rows must appear in destination/aggregate queries when they match
  the active filter.
- Deep-window offsets and `row_for_path()` must account for pending source rows
  before the requested raw row.
- Successful `moveCompletedDetailed` must clear both source and destination
  pending paths.
- Failed, partially failed, or destination-update-failed operations must roll
  back pending moves.
- Restore should remove rows from the trash view only after restore planning
  returns accepted batches. If the user declines every fallback or no work is
  queued, the UI must not hide selected trash rows.

## Focused Checks

```bash
.venv/bin/python -m pytest tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/coordinators/test_main_coordinator_pending_moves.py -q
.venv/bin/python -m pytest tests/services/test_asset_move_service.py tests/services/test_restoration_service.py -q
```
