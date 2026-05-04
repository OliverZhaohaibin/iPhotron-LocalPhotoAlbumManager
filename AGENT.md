# AGENT.md - iPhotron Development Principles

This file is the working guide for coding agents and contributors. It reflects
the current vNext state: the production runtime has converged on
`RuntimeContext -> LibrarySession -> application ports/services`, and legacy
compatibility code is quarantined under `src/iPhoto/legacy/`.

## 1. Current Architecture Status

- The vNext cleanup is complete for production source code.
- Production runtime code must not import `iPhoto.legacy` or `iPhoto.models.*`.
- Legacy compatibility and old domain-repository code live only in
  `src/iPhoto/legacy/`. That subtree is temporary quarantine for historical
  behavior tests and is planned for removal in the next major release.
- GUI, CLI, file watchers, Qt workers, and future automation entry points must
  enter library behavior through `RuntimeContext`, `LibrarySession`, and
  application-level surfaces.
- New business logic belongs in application use cases/services, session
  surfaces, domain values/pure services, or infrastructure adapters. GUI code
  is presentation and Qt transport only.

The authoritative refactor status is tracked in:

- `docs/refactor/04-implementation-checklist.md`
- `docs/refactor/05-current-progress.md`

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

Compatibility code is not a production extension point. Do not add new features
to `src/iPhoto/legacy/app.py`, `src/iPhoto/legacy/appctx.py`,
`src/iPhoto/legacy/bootstrap/*`, or other quarantine modules.

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
  and Qt task/signal adapters. It owns presentation state, not durable workflow
  rules.
- `library/`: runtime controller, tree/watch/scan coordination, trash and album
  filesystem shell code bound to session services.
- `people/`: optional People runtime, scan coordination, repositories, manual
  faces, stable People state, groups, covers, hidden flags, and service API.
- `maps/`: optional offline Maps runtime, tile parsing, OBF/native widget/helper
  integration, search, and map rendering internals.
- `core/`: pure or rendering-oriented algorithms for Live Photo pairing,
  adjustment math, geometry, export transforms, filters, raw loading, and
  preview backends.
- `io/`: metadata extraction, scanner adapters, and sidecar parsing helpers.
- `legacy/`: quarantine only. No production imports and no new functionality.

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
- Thumbnail rendering may apply `.ipo` edit state, but durable edit persistence
  belongs behind edit sidecar/session services.

### Edit

- All normal edits are non-destructive and stored in `.ipo` sidecars.
- Editing math belongs in `core/`; persistence belongs behind `EditSidecarPort`
  or session edit services.
- QRhi/Metal/OpenGL backend choice must not leak into product workflow rules.

## 8. Rendering And Maps Platform Rules

- macOS media preview should default to QRhi/Metal when available; OpenGL is a
  diagnostic or compatibility fallback.
- Windows/Linux may use QRhi/OpenGL-backed paths depending on runtime support.
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

Required guardrail expectations:

- `application/` has no GUI or concrete persistence imports.
- `infrastructure/` has no GUI imports.
- production source has no `iPhoto.legacy` or `iPhoto.models.*` imports.
- GUI runtime has no compatibility service factory fallback.
- Architecture checks are part of CI.

Known non-blocking warning currently documented by the refactor notes:

- `PytestConfigWarning: Unknown config option: env`

## 10. Release And Documentation Rules

- Keep `README.md` product-facing and concise.
- Keep `docs/architecture.md` as the current architecture entry point.
- Keep `docs/refactor/*` as the detailed vNext migration record and verification
  log.
- Do not treat archived refactor documents under `docs/finished/` as current
  implementation instructions.
- Release validation may include manual Qt GUI smoke testing and opening an
  existing library, but these are product acceptance checks rather than
  architecture guardrail replacements.

This guide is authoritative for new production work. When it conflicts with old
examples, follow the vNext runtime/session boundary and update the stale
example as part of the change.
