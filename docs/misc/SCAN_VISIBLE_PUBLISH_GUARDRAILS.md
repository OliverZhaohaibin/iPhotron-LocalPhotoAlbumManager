# Scan Visible Publish Guardrails

This note protects the current scan-to-gallery update contract. Do not weaken
these rules when changing scanners, scan services, Qt workers, Gallery
viewmodels, or status/progress plumbing.

## Contract

- Production scan UI updates use `ScanBatchCommitted`.
- Do not restore `scanChunkReady` as a production transport. It may remain only
  as archived legacy reference code.
- `ScanBatchCommitted.rows` must contain only rows that are immediately visible:
  `thumbnail_state == "ready"` and a non-empty `thumb_cache_key`.
- Failed, pending, stale, or old no-key rows may be persisted for diagnostics or
  repair, but must not enter normal Gallery collections.
- Keep `ScanLibraryRequest.visible_publish_size` in the 50-200 ready-row range;
  the default is 100.
- Empty-library scans must still refresh the initial Gallery window when the
  first batch arrives. Do not drop pending refresh just because there is no
  visible range yet.
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

## Focused Checks

```bash
.venv/bin/python -m pytest tests/application/test_scan_library_use_case.py tests/application/test_library_scan_service.py tests/library/test_scanner_worker.py -q
.venv/bin/python -m pytest tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/gui/coordinators/test_coordinator_domain_boundaries.py tests/test_library_manager.py -q
```
