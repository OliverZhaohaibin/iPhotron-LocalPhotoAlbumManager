# Scan Visible Publish Guardrails

This note protects the current scan-to-gallery update contract. Do not weaken
these rules when changing scanners, scan services, Qt workers, Gallery
viewmodels, or status/progress plumbing. The
[scan architecture](../architecture.md#scan-and-index) describes scan scopes,
row preparation, persistence, and finalization.

## Contract

- Production scan UI updates use `ScanBatchCommitted`.
- Do not restore `scanChunkReady` as a production transport. It may remain only
  as archived legacy reference code.
- `ScanBatchCommitted.rows` must contain only rows that are immediately visible:
  `thumbnail_state == "ready"` and a non-empty `thumb_cache_key`.
- Failed, pending, stale, or old no-key rows may be persisted for diagnostics or
  repair, but must not enter normal Gallery collections.
- Keep `ScanLibraryRequest.visible_publish_size` in the 50-200 ready-row range.
  The default is 100 and the last batch may be smaller. Publication size and
  database flush policy are separate settings.
- A row can remain ready during a thumbnail refresh if it still has a verified
  published artifact for the same source. Keep its key and preview drawable
  until the replacement is published. Retry interrupted refreshes for the
  current revision. See [Thumbnail Rendering](../architecture.md#thumbnail-rendering).
- Empty-library scans must still refresh the initial Gallery window when the
  first batch arrives. Do not drop pending refresh just because there is no
  visible range yet.
- The adapter coalesces committed scan and backfill batches on a 150 ms timer.
  An in-flight read may complete while newer commits are pending. Keep a
  follow-up refresh for those commits and coalesce overlapping queued
  refreshes. Reject older snapshots and results from superseded selections.
- Successful scan completion requests a fresh snapshot after scope
  reconciliation and Live Photo pairing. This catches changes such as
  deletions and pairing that a ready-row batch cannot describe. Cancellation
  leaves already committed batches available and skips success finalization.
- Location/Map scan consumers should subscribe to `ScanBatchCommitted`, not
  legacy chunk payloads.
- Metadata commits may update Face/Pet eligibility and status, but must not
  publish uncommitted scanner chunks into recognition snapshot databases.
- Startup, metadata-scan completion, persisted `pending`/`retry` rows, and an
  interactive rescan do not activate AI workers. Desktop activation requires
  the People view to have been shown and its first viewport to be ready, then
  waits for the 350 ms quiet interval.
- After recognition has been activated for the current library, Face and Pet
  workers may drain persisted eligible rows and consume later committed scan
  rows. Keep `face_status` and `pet_status` independent.

## Applying Window Results

- A content-only update keeps the collection order and other loaded windows.
  Publish changes to loaded rows through local model notifications.
- Before applying a new order, stage the active Gallery and Filmstrip ranges
  at the same order revision. A partial guard or explicit Detail read can
  trigger a staged refresh when it cannot supply the visible ranges.
- Use incremental Qt row insertion, removal, and layout signals for
  versioned scan refreshes. Resolve retained selections, persistent indexes,
  and scroll anchors by asset identity. A new asset that takes an old row
  number must never inherit its selection, including after Select All.
- Preserve the asset under a mouse press and the visible anchor during
  reordering. Map Filmstrip structural changes through its spacer proxy.
  Resolve pending move offsets on the window worker before applying the new
  layout. See the [move/restore guardrails](MOVE_RESTORE_OPTIMISTIC_UI_GUARDRAILS.md).

## Focused Checks

```bash
.venv/bin/python -m pytest tests/application/test_scan_library_use_case.py tests/application/test_library_scan_service.py tests/library/test_scanner_worker.py -q
.venv/bin/python -m pytest tests/test_scanner_adapter.py tests/test_library_watch_service.py tests/infrastructure/test_thumbnail_artifact.py -q
.venv/bin/python -m pytest tests/gui/viewmodels/test_gallery_scan_progress.py tests/gui/viewmodels/test_gallery_backfill_completion_order.py -q
.venv/bin/python -m pytest tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/gui/coordinators/test_coordinator_domain_boundaries.py tests/test_library_manager.py -q
```

`test_gallery_scan_progress.py` covers a real JPEG scan with committed
thumbnails drawn before scan completion, including cancellation after a commit.
Follow the [Gallery scroll guardrails](GALLERY_SCROLL_PIPELINE_GUARDRAILS.md)
for platform timing checks.
