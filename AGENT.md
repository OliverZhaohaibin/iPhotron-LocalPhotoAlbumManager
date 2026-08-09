# AGENT.md - iPhotron Development Principles

This file is the working guide for coding agents and contributors. It reflects
the current vNext state: the production runtime has converged on
`RuntimeContext -> LibrarySession -> application ports/services`, and the
legacy compatibility application tree has been removed.

## 1. Current Architecture Status

- The vNext cleanup is complete for production source code.
- Production runtime code must not import `iPhoto.legacy` or `iPhoto.models.*`.
- The removed `src/iPhoto/legacy/` application tree must not be restored.
  Historical behavior that remains a product requirement must be tested
  through current application, session, domain, or infrastructure surfaces.
- GUI, CLI, file watchers, Qt workers, and future automation entry points must
  enter library behavior through `RuntimeContext`, `LibrarySession`, and
  application-level surfaces.
- New business logic belongs in application use cases/services, session
  surfaces, domain values/pure services, or infrastructure adapters. GUI code
  is presentation and Qt transport only.
- Gallery-to-Detail still rendering uses one GPU-first production path: a
  render transaction owns generation and terminal state, viewport-aware
  neutral surfaces feed bounded disk/CPU/GPU caches, and Detail/Edit share one
  render session. The removed Detail v2 frame cache and still Edit CPU-preview
  path are not extension points.

The authoritative current architecture is tracked in `docs/architecture.md`.
Completed vNext migration records are archived under
`docs/finished/refactor/vnext-2026-06/`.

## 2. Product Invariants

- **Folder-native library.** A folder is an album. Users can browse folders
  without an import step.
- **Local-first.** Core library, browsing, editing, Live Photo, People, and Maps
  behavior is local. Optional runtimes must degrade gracefully when unavailable.
- **Non-destructive editing.** Visual edits are stored in `.ipo` sidecars.
  Original media is not overwritten by normal editing.
- **Explicit metadata write-back only.** Assign Location is the explicit
  exception: it persists the location locally first, then best-effort writes GPS
  metadata to the original file through ExifTool and reports warnings on
  failure.
- **Rebuildable facts vs durable choices.** Scan facts, thumbnails, Live Photo
  materialization, and People runtime snapshots can be rebuilt. Favorites,
  hidden/trash state, pinned items, album order, manual metadata, People names,
  covers, groups, group order, hidden flags, and manual faces must survive
  rescans and rebuilds.
- **Cross-platform desktop first.** macOS, Windows, and Linux remain supported.
  Platform-specific rendering, maps, ExifTool, FFmpeg, and AI behavior must be
  isolated behind adapters or runtime discovery.

## 3. Runtime And Layering Rules

The production dependency direction is:

```text
gui -> bootstrap/runtime -> application -> domain
infrastructure -> application ports / domain values
bounded contexts -> application ports / domain values
```

Forbidden directions:

```text
domain -> application/gui/infrastructure
application -> gui/concrete cache/concrete infrastructure
infrastructure/cache/core/io/library/people -> gui
production runtime -> iPhoto.legacy
production runtime -> iPhoto.models.*
```

Key runtime objects:

- `RuntimeContext`: process composition root, current settings/theme/recent
  libraries, active `LibrarySession` lifecycle.
- `LibrarySession`: library-scoped adapters and surfaces for assets, state,
  scanning, album metadata, People, Maps, thumbnails, edit sidecars, location,
  asset lifecycle, and file operations.
- `LibraryRuntimeController`: GUI/runtime controller bound to the active
  session; it should not re-create standalone compatibility services.

The removed compatibility tree is not a production extension point. Do not
restore `src/iPhoto/legacy/`; migrate any still-required behavior to current
application/session surfaces instead.

## 4. Files And State

Album markers:

- `.iphoto.album.json`: folder-local album manifest.
- `.iphoto.album`: minimal marker for folder-native album discovery.
- `.iPhoto/manifest.json`: compatibility manifest location supported by the
  current manifest repository.

Library workspace:

```text
/<LibraryRoot>/.iPhoto/
  global_index.db       # SQLite index and current asset/state repository store
  links.json            # Live Photo compatibility materialization
  cache/thumbs/         # Rebuildable thumbnail cache
  cache/detail-surfaces/v3/ # SQLite-indexed rebuildable neutral RGBA8/sRGB surfaces
  faces/
    face_index.db       # Rebuildable People runtime snapshot
    face_state.db       # Durable People user decisions
    thumbnails/         # Rebuildable cropped face thumbnails
  manifest.bak/         # Manifest/links backup area
  locks/                # File-level locks for JSON sidecars
```

State rules:

- `global_index.db` is the current source of truth for asset scan rows,
  pagination, Live Photo roles, trash/favorite/hidden flags, face scan status,
  and the repository-backed user-state boundary.
- Large-library gallery reads are SQL-first and windowed through collection
  query APIs. Normal visible rows must be thumbnail-ready and carry a
  `thumb_cache_key`.
- Gallery paint/model access is memory-only. Sparse rows load asynchronously;
  viewport generations reject stale window and thumbnail results, and visible,
  guard, and far-speculative thumbnail lanes keep separate capacity.
- `links.json` is derived compatibility materialization for Live Photo payloads;
  target runtime behavior should read roles through repository/session surfaces.
- `cache/thumbs/` and People thumbnails are disposable.
- `faces/face_index.db` is rebuildable; `faces/face_state.db` is durable.
- `.ipo` sidecars are the durable source of non-destructive edit parameters.
- Scan merge must be idempotent and must not implicitly clear durable user
  state.

## 5. Module Responsibilities

- `bootstrap/`: `RuntimeContext`, `LibrarySession`, and session-bound services
  that wire application behavior to the current library root.
- `application/ports/`: public application boundary protocols, including
  `AssetRepositoryPort`, `LibraryStateRepositoryPort`, `MediaScannerPort`,
  `PeopleIndexPort`, `MapRuntimePort`, `EditSidecarPort`,
  `LocationAssetServicePort`, and `MapInteractionServicePort`.
- `application/use_cases/`: owning use cases for workflows such as scanning.
- `application/services/`: application-level services for album manifests,
  pinned state, location queries, map interaction, and explicit location
  assignment.
- `domain/`: dataclasses, value objects, query models, and pure domain services.
  Domain code must not perform IO or import Qt/SQLite/runtime adapters.
- `infrastructure/`: concrete adapters for SQLite-backed state, manifests,
  `.ipo` sidecars, ExifTool, FFmpeg, maps runtime discovery, thumbnail caches,
  filesystem scanning, and runtime services.
- `cache/index_store/`: current SQLite global index implementation used behind
  repository/session surfaces. GUI and application code must not bypass the
  session boundary to call it directly.
- `gui/`: PySide6 views, widgets, controllers, viewmodels, coordinators, menus,
  Qt task/signal adapters, and the Detail transaction/scheduler/cache/session
  boundary. It owns presentation and GPU residency state, not durable workflow
  rules.
- `library/`: runtime controller, tree/watch/scan coordination, trash and album
  filesystem shell code bound to session services.
- `people/`: optional People runtime, scan coordination, repositories, manual
  faces, stable People state, groups, covers, hidden flags, and service API.
- `maps/`: optional offline Maps runtime, tile parsing, OBF/native widget/helper
  integration, search, and map rendering internals.
- `core/`: pure or rendering-oriented algorithms for Live Photo pairing,
  adjustment math, geometry, export transforms, filters, and raw loading.
- `io/`: metadata extraction, scanner adapters, and sidecar parsing helpers.
- `legacy/`: removed. Do not recreate it or add compatibility imports.

## 6. Coding Rules

- Prefer existing session/application patterns over adding new facades.
- Use application ports before introducing cross-layer behavior.
- Keep GUI workers thin: they adapt Qt threading/progress and call session or
  application services.
- Use `Path` and shared path normalizers for filesystem paths. Never string-build
  paths.
- Use schema validation for album/link JSON payloads where a schema exists.
- Use atomic writes for manifest, links, settings, sidecars, and user state
  files.
- Use SQLite transactions for multi-row writes and scan merges.
- Use ExifTool/FFmpeg wrappers from `utils/`; never shell-concatenate user
  paths.
- Return warnings for recoverable external-tool failures without corrupting
  local state.
- Keep comments focused on non-obvious intent, boundaries, or failure modes.

## 7. Bounded Context Rules

### People

- InsightFace/ONNXRuntime are optional. Missing AI runtime must not break
  browsing, editing, Live Photo, Maps, or library state.
- Scan commits may rebuild `face_index.db`, but must preserve and repair
  `face_state.db`.
- Names, covers, hidden flags, person order, groups, group order, pinned state,
  group covers, manual faces, and group caches are durable user state.
- Do not merge people with incompatible hidden state.
- UI mutations must route through the session-bound People service or explicit
  test doubles.

### Maps

- Maps are optional. Missing native OBF/helper/widget runtime must show graceful
  fallback.
- Runtime availability belongs behind `MapRuntimePort`.
- Location asset aggregation and marker-click semantics belong behind session
  location/map interaction surfaces.
- Qt overlay painting, pointer hit testing, drag cursors, and widget event
  filters remain GUI transport details.

### Thumbnails

- Thumbnail generation and cache lookup must not block the UI thread.
- Memory/disk cache hits must avoid re-running generators.
- Gallery-visible rows must not treat missing full thumbnail cache keys as
  ready media. Old no-key rows belong on stale/backfill paths.
- Thumbnail rendering may apply `.ipo` edit state, but durable edit persistence
  belongs behind edit sidecar/session services.

### Edit

- All normal edits are non-destructive and stored in `.ipo` sidecars.
- Editing math belongs in `core/`; persistence belongs behind `EditSidecarPort`
  or session edit services.
- Static Detail and Edit exchange `PhotoRenderSessionHandle`; entering Edit,
  Done/Cancel, compare, fullscreen, and adjustment updates must not restore a
  second source decoder, CPU full-image preview session, or duplicate texture
  upload path.
- Sidecar changes replace immutable `EditRenderState` only. They must not enter
  `DetailDecodeKey` or invalidate source-identical neutral surfaces/textures.
- QRhi/Metal/OpenGL backend choice must not leak into product workflow rules.

## 8. Rendering And Maps Platform Rules

- macOS media preview defaults to QRhi/Metal and may decode non-RAW stills with
  ImageIO; OpenGL and Qt decode are compatibility fallbacks.
- Windows uses QRhi/OpenGL and prefers WIC for non-RAW stills; WIC/COM
  declarations must use fixed-width Windows ABI types. Linux uses QRhi/OpenGL
  with the Qt still decoder. RAW remains routed through rawpy.
- Platform decoders may fall back to Qt only inside the existing worker lane.
  They preserve cancellation and return detached RGBA8888/sRGB surfaces.
- Initial still presentation is viewport-LOD based and non-mipmapped. GPU
  residency retains current/previous/next within both the three-texture and
  192MB limits; a higher LOD replaces the current layer only after a real draw.
- Legacy OpenGL maps use the `QOpenGLWindow + createWindowContainer()` surface
  where required to avoid transparent-window composition issues.
- Native OsmAnd widget/helper selection belongs to maps runtime adapters and
  widget factories.
- Packaged builds must include required QSB shaders and maps extension runtime
  assets when those features are enabled.

## 9. Testing And Verification

Run architecture checks after boundary changes:

```bash
python3 tools/check_architecture.py
.venv/bin/python -m pytest tests/architecture -q
```

Use targeted regression tests for changed behavior:

```bash
.venv/bin/python -m pytest tests/application/test_runtime_context.py tests/application/test_library_session.py tests/application/test_scan_library_use_case.py -q
.venv/bin/python -m pytest tests/application/test_temp_library_end_to_end.py tests/application/test_library_asset_lifecycle_service.py tests/services/test_asset_move_service.py tests/services/test_restoration_service.py -q
.venv/bin/python -m pytest tests/performance -q
```

For Detail rendering, decoder, cache, Edit session, or viewer changes, run:

```bash
.venv/bin/python -m pytest -q tests/gui/test_detail_pipeline.py tests/gui/test_detail_render_coordinator.py tests/gui/test_detail_decode_backend.py tests/gui/test_detail_request_scheduler.py tests/gui/test_detail_surface_cache.py tests/gui/test_detail_render_session.py tests/ui/controllers/test_player_view_controller_adjustments.py tests/ui/widgets/test_still_texture_residency.py tests/test_detail_benchmark.py
.venv/bin/python tools/check_architecture.py
.venv/bin/python -m compileall -q src tools
```

Platform backend changes also require packaged manual validation on the target
OS. Source/offscreen tests do not prove WIC, ImageIO, QRhi/Metal, or native
OpenGL behavior.

Before touching scan visible publishing, collection query performance, trash
state, or move/restore optimistic UI behavior, read the matching guardrail under
`docs/misc/`.

Required guardrail expectations:

- `application/` has no GUI or concrete persistence imports.
- `infrastructure/` has no GUI imports.
- production source has no `iPhoto.legacy` or `iPhoto.models.*` imports.
- GUI runtime has no compatibility service factory fallback.
- Architecture checks are part of CI.

## 10. Release And Documentation Rules

- Keep `README.md` product-facing and concise.
- Keep `docs/architecture.md` as the current architecture entry point.
- Keep completed refactor records under `docs/finished/refactor/`.
- Do not treat archived refactor documents under `docs/finished/` as current
  implementation instructions.
- Keep `docs/requirements/DETAIL_OPEN_BENCHMARK_RUNBOOK.md` aligned with the
  production Detail profiler/harness whenever transaction stages, cache tiers,
  decoder names, or SLO validation fields change.
- Release validation may include manual Qt GUI smoke testing and opening an
  existing library, but these are product acceptance checks rather than
  architecture guardrail replacements.

This guide is authoritative for new production work. When it conflicts with old
examples, follow the vNext runtime/session boundary and update the stale
example as part of the change.
