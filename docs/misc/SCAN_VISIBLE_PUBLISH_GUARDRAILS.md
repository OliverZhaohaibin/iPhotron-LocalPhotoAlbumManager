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
- Face and Pet workers may consume committed scan rows, but must not publish
  uncommitted scanner chunks into their snapshot databases.
- Interactive scans feed both AI workers while metadata batches commit. Startup
  scans defer both workers until metadata completion, then drain persisted
  `pending`/`retry` status rows. Keep `face_status` and `pet_status` independent.

## Focused Checks

```bash
.venv/bin/python -m pytest tests/application/test_scan_library_use_case.py tests/application/test_library_scan_service.py tests/library/test_scanner_worker.py -q
.venv/bin/python -m pytest tests/gui/viewmodels/test_gallery_collection_store.py tests/gui/viewmodels/test_gallery_list_model_adapter.py tests/gui/coordinators/test_main_coordinator_asset_runtime_boundary.py -q
```
