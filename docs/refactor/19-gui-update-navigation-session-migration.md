# 19 GUI Update + Navigation Session Migration

## Goal

Continue the GUI residual-orchestration cleanup without cutting across Maps or Edit in a broad way.

This slice targets two remaining seams:

- `LibraryUpdateService` still owning scan/pair/finalize/restore-refresh orchestration details.
- Location / Recently Deleted flows still reaching through GUI code into library/runtime behavior instead of consuming a narrow adapter surface.

The goal of this round is to leave GUI code with presentation coordination, Qt transport, and routing responsibilities only, while reusing the repository's current runtime/library boundary (`LibraryManager` plus bootstrap services and mixins) instead of forcing a parallel session abstraction.

## What Changed

### LibraryUpdate

- Added higher-level runtime scan entry points on `LibraryScanService`:
  - synchronous rescan
  - scan finalize hook
  - restore-refresh rescan entry
- The runtime finalize hook now centralizes:
  - Recently Deleted preserved-field merge
  - snapshot persistence
  - link rebuild
  - stale-row reconciliation
  - optional Live Photo pairing follow-up
- `RescanWorker` now refreshes restored albums through the runtime scan surface instead of directly composing lifecycle persistence rules.
- `LibraryUpdateService` no longer imports `ScannerWorker` / `RescanWorker` directly.
- Worker ownership moved into a dedicated GUI task runner so `LibraryUpdateService` remains a presentation adapter that:
  - starts or cancels tasks
  - relays progress and chunk signals
  - emits `indexUpdated`, `linksUpdated`, and `assetReloadRequested`
  - keeps facade-facing behavior stable

### Location / Trash

- Added a narrow GUI `LocationTrashNavigationService` for:
  - Recently Deleted directory preparation
  - trash cleanup throttling and background dispatch
  - background geotagged-asset loading
  - request-serial management for Location reloads
- `NavigationCoordinator` dropped its trash cleanup thread logic and remains a thin routing binder.
- `GalleryViewModel` no longer calls `ensure_deleted_directory()` or `get_geotagged_assets()` directly.
- `GalleryViewModel` now consumes adapter results and keeps only UI state:
  - static selection
  - route changes
  - cluster gallery state
  - cached location snapshot state

### Guardrails

- Extended architecture checking so `gui/services/library_update_service.py` cannot import `library.workers.*`.
- Updated targeted GUI regressions to assert the new boundary shape rather than old worker-construction details.

## Behavioral Notes

- `AppFacade` public API shape stays the same; the refactor only changes internal forwarding.
- The current branch still uses `LibraryManager` and bootstrap runtime services as the effective boundary. This round does not claim a new full `LibrarySession` / `RuntimeContext` rollout where the code does not already use one.
- Maps runtime extraction is still incomplete. The new Location/Trash adapter is a cleanup seam for future work, not the final Phase 5 port.
- People residual fallback behavior remains for a later slice.

## Verification

Targeted regressions updated for:

- `LibraryUpdateService` runtime forwarding and task-runner delegation
- `GalleryViewModel` Recently Deleted and Location flows through the new adapter
- `NavigationCoordinator` remaining free of direct trash cleanup calls
- `AppFacade` preserving its public async-rescan forwarding shape
- architecture boundary checks for `LibraryUpdateService` worker imports

Environment note:

- Final command-based verification was partially blocked by the local Codex escalation limit during this round, so any remaining test execution should be rerun once command access is available again.

## Next Handoff

- Continue with the remaining People fallback/coordinator residuals as the next Phase 4 GUI cleanup slice.
- When returning to Maps work, build on the new `LocationTrashNavigationService` seam instead of reintroducing direct `LibraryManager` reads into coordinator/viewmodel code.
- Keep Edit sidecar, full Maps fallback cleanup, and temp-library end-to-end validation out of this slice unless a later round explicitly re-scopes them.
