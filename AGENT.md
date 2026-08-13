# AGENT.md - iPhotron Development Principles

This file is the authoritative working guide for coding agents and contributors.
It reflects the current production state: runtime composition converges on
`RuntimeContext -> LibrarySession -> application ports/services`, the desktop
coordinator graph is owned by `DesktopCoordinatorRuntime`, and the removed
legacy application tree is not a production extension point.

## 1. Current Architecture Status

- Production runtime code must not import `iPhoto.legacy` or `iPhoto.models.*`.
- The removed `src/iPhoto/legacy/` application tree must not be restored.
- GUI, CLI, file watchers, Qt workers, and future automation entry points enter
  library behavior through `RuntimeContext`, `LibrarySession`, and application
  surfaces.
- New business logic belongs in application use cases/services, session
  surfaces, domain values/pure services, or infrastructure adapters. GUI code
  is presentation and Qt transport.
- Desktop GUI composition is owned by
  `iPhoto.gui.coordinators.desktop_coordinator_runtime.DesktopCoordinatorRuntime`.
  `gui/coordinators/main_coordinator.py` is a compatibility import only; do not
  document or introduce a separate production `MainCoordinator` contract.
- Recognition is feature-driven. Application startup may warm cached People/Pets
  dashboard data, but it must not start model inference. `RecognitionCoordinator`
  requests recognition scans only after the People surface has been shown and
  its first viewport is ready; activation is deliberately delayed so first
  content delivery is not competing with model work.
- Gallery-to-Detail still rendering uses one GPU-first production path: a
  render transaction owns generation and terminal state, viewport-aware neutral
  surfaces feed bounded disk/CPU/GPU caches, and Detail/Edit share one render
  session.

The current architecture entry point is `docs/architecture.md`. Completed vNext
migration records live under `docs/finished/refactor/vnext-2026-06/`.

## 2. Product Invariants

- **Folder-native library.** A folder is an album. Users can browse folders
  without an import step.
- **Local-first.** Core library, browsing, editing, Live Photo, People, Pets,
  and Maps behavior is local. Optional runtimes degrade gracefully when absent.
- **Non-destructive editing.** Visual edits are stored in `.ipo` sidecars.
  Original media is not overwritten by normal editing.
- **Explicit metadata write-back only.** Assign Location persists local state
  first, then best-effort writes GPS metadata through ExifTool and reports
  warnings on failure.
- **Rebuildable facts vs durable choices.** Scan facts, thumbnails, Live Photo
  materialization, People runtime snapshots, and Pets runtime snapshots can be
  rebuilt. Favorites, hidden/trash state, pinned items, album order, manual
  metadata, People names/groups/manual faces/covers, and Pets names/covers/
  hidden/rejected decisions must survive rescans and rebuilds.
- **Cross-platform desktop first.** macOS, Windows, and Linux remain supported.
  Platform-specific rendering, maps, ExifTool, FFmpeg, and AI behavior stay
  behind adapters or runtime discovery.

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
infrastructure/cache/core/io/library/people/pets -> gui
production runtime -> iPhoto.legacy
production runtime -> iPhoto.models.*
```

Key runtime objects:

- `RuntimeContext`: process composition root, settings/theme/recent libraries,
  active `LibrarySession` lifecycle.
- `LibrarySession`: library-scoped adapters and surfaces for assets, state,
  scanning, album metadata, People, Pets, Maps, thumbnails, edit sidecars,
  location, asset lifecycle, and file operations.
- `LibraryRuntimeController`: GUI/runtime controller bound to the active session;
  it does not recreate standalone compatibility services.
- `DesktopCoordinatorRuntime`: desktop presentation composition root. It owns the
  coordinator graph and late feature promotion/binding, not durable business
  state.
- `RecognitionCoordinator`: lazy recognition presentation/service coordinator.
  It may warm persisted dashboard snapshots without loading inference models,
  and activates scans only after People is actually used and its first viewport
  has rendered.

## 4. Files And State

Album markers:

- `.iphoto.album.json`: folder-local album manifest.
- `.iphoto.album`: minimal marker for folder-native album discovery.
- `.iPhoto/manifest.json`: compatibility manifest location supported by the
  current manifest repository.

Library workspace:

```text
/<LibraryRoot>/.iPhoto/
  global_index.db       # asset index; includes independent face_status/pet_status
  links.json            # Live Photo compatibility materialization
  cache/thumbs/         # rebuildable thumbnail cache
  cache/detail-surfaces/v3/
  faces/
    face_index.db       # rebuildable People runtime snapshot
    face_state.db       # durable People user decisions
    thumbnails/         # rebuildable face crops
  pets/
    pet_index.db        # rebuildable Pets detections/identity snapshot
    pet_state.db        # durable Pets user decisions
    thumbnails/         # rebuildable pet crops except referenced covers
  manifest.bak/
  locks/
```

State rules:

- `global_index.db` is the current source of truth for asset scan rows,
  pagination, Live Photo roles, trash/favorite/hidden flags, independent
  `face_status`/`pet_status`, and repository-backed user-state boundaries.
- Large-library Gallery reads are SQL-first and windowed. Normal visible rows
  must be thumbnail-ready and carry a `thumb_cache_key`.
- Gallery paint/model access is memory-only. Sparse rows load asynchronously;
  generation checks reject stale window and thumbnail results.
- `links.json` is derived compatibility materialization; runtime behavior reads
  Live Photo roles through repository/session surfaces.
- `faces/face_index.db` and `pets/pet_index.db` are rebuildable.
- `faces/face_state.db` and `pets/pet_state.db` contain durable choices and must
  not be replaced by a runtime rebuild.
- `.ipo` sidecars are the durable source of non-destructive edit parameters.
- Scan merge is idempotent and must not implicitly clear durable user state.

## 5. Module Responsibilities

- `bootstrap/`: `RuntimeContext`, `LibrarySession`, and session-bound services.
- `application/ports/`: public boundaries such as `AssetRepositoryPort`,
  `LibraryStateRepositoryPort`, `MediaScannerPort`, `PeopleIndexPort`,
  `PetIndexPort`, `MapRuntimePort`, `EditSidecarPort`,
  `LocationAssetServicePort`, and `MapInteractionServicePort`.
- `application/use_cases/`: owning workflow use cases such as scanning.
- `application/services/`: album manifests, pinned state, location queries, map
  interaction, explicit location assignment, and other application workflows.
- `domain/`: dataclasses, value objects, query models, and pure domain services;
  no Qt, SQLite, runtime singletons, or IO ownership.
- `infrastructure/`: concrete SQLite/manifest/sidecar/ExifTool/FFmpeg/maps/
  thumbnail/scanner adapters.
- `cache/index_store/`: SQLite global index implementation behind session and
  repository surfaces; GUI/application code must not bypass the boundary.
- `gui/`: PySide6 views, viewmodels, controllers, coordinators, menus, Qt
  tasks/signals, and rendering presentation state.
- `library/`: runtime controller and tree/watch/scan/trash/album shell code bound
  to session services.
- `people/`: optional face detection/clustering runtime, repositories, durable
  People state, manual faces, groups, covers, and People service API.
- `pets/`: optional YOLOX/DINOv2 pet detection and identity clustering runtime,
  rebuildable Pets index, durable Pets state, and Pet service API.
- `maps/`: optional offline Maps runtime and OBF/native/helper integration.
- `core/`: Live Photo pairing, adjustment math, geometry, export transforms,
  filters, raw loading, and other pure/rendering-oriented algorithms.
- `io/`: metadata extraction, scanner adapters, and sidecar parsing helpers.
- `legacy/`: removed. Do not recreate it.

## 6. Startup And Recognition Rules

- The installed GUI script is `iphoto-gui = iPhoto.entrypoint:main`.
- `iPhoto.entrypoint` stays lightweight and handles helper dispatch before the
  full Qt GUI import.
- The main window shell and first-paint path must remain isolated from optional
  heavy feature imports and model inference.
- Platform-required GPU detail construction may happen before `show()` where Qt
  native-window behavior requires it; optional feature promotion remains owned
  by the desktop startup/coordinator path.
- `DesktopCoordinatorRuntime`, not a conceptual `MainCoordinator`, is the
  production desktop composition boundary.
- Recognition dashboard snapshot warmup may read existing local People/Pets
  state. Warmup is not scan activation and must not initialize AI models.
- Recognition scan activation requires both `people_view_shown` and
  `firstViewportReady`; `RecognitionCoordinator` then schedules the library
  activation after a short quiet window.
- Do not reintroduce “start People/Pets after metadata scan” as an application
  startup rule.

## 7. Bounded Context Rules

### People

- InsightFace/ONNXRuntime are optional; missing AI runtime must not break normal
  browsing, editing, Live Photo, Pets state, Maps, or library state.
- Runtime rebuilds may replace `face_index.db` but preserve/repair
  `face_state.db`.
- Names, covers, hidden flags, order, groups, pinned state, group covers, manual
  faces, and group caches are durable user state.
- UI mutations route through the session-bound People service.

### Pets

- `pets-ai` is optional. Missing dependencies or models leave eligible scan rows
  resumable and must not block normal application use.
- `pet_index.db` is rebuildable; `pet_state.db` is durable.
- The production clustering contract is `species-bounded-single-link-v3`.
  Cats and dogs never share an identity cluster. Candidate joins obey cannot-link
  constraints and a bounded cluster diameter so single-link chains cannot grow
  without limit.
- People/Pets conflict filtering uses geometry but is not an unconditional
  “People always wins” rule. Strong face overlap normally suppresses a pet box,
  while a substantially larger plausible pet-body detection containing a
  smaller face can be preserved subject to the runtime image-coverage rule.
- Pet names, covers, hidden state, rejected detection keys, redirects, and other
  explicit user choices survive runtime rebuilds.
- Cross-kind People/Pets composition belongs to recognition coordination/state;
  each bounded context keeps ownership of its own runtime records.
- Production inference consumes prebuilt model artifacts. Torch Hub may be used
  by release conversion/provenance tooling, but production runtime must not
  execute arbitrary Torch Hub Python.
- The manifest is authoritative for integrity metadata. A `null`
  `torchscript_url` means the DINOv2 artifact must already be packaged/staged;
  documentation must not promise an automatic DINO download in that state.

### Maps

- Maps are optional. Missing native OBF/helper/widget runtime must show graceful
  fallback.
- Runtime availability belongs behind `MapRuntimePort`.
- Location aggregation and marker-click semantics belong behind session
  location/map interaction surfaces.
- Qt overlay/event behavior remains GUI transport.

### Thumbnails

- Thumbnail generation/cache lookup must not block the UI thread.
- Cache hits avoid re-running generators.
- Gallery-visible rows do not treat missing full-thumbnail keys as ready media.
- Thumbnail rendering may apply `.ipo` state; durable edit persistence stays
  behind edit sidecar/session services.

### Edit

- Normal edits are non-destructive and stored in `.ipo` sidecars.
- Editing math belongs in `core/`; persistence belongs behind `EditSidecarPort`
  or session edit services.
- Static Detail/Edit share `PhotoRenderSessionHandle`; do not restore a second
  source decoder, CPU full-image preview session, or duplicate texture path.
- Sidecar changes replace immutable `EditRenderState`; they do not enter source
  decode keys or invalidate source-identical neutral surfaces/textures.

## 8. Packaging And Model Assets

- `src/extension/models/...` is a packaging/staging convention, not guaranteed
  tracked content of a fresh clone. Build docs must say when the directory must
  be provided before packaging.
- People/Pets capable offline packages must include the required optional Python
  runtime and model artifacts explicitly.
- The Pets detector has a fixed HTTPS artifact contract. DINOv2 runtime loading
  uses the prebuilt TorchScript artifact and its manifest hash/size contract;
  the current manifest has no runtime DINO download URL.
- AppImage and Debian build contracts live in `docs/misc/BUILD_APPIMAGE.md` and
  `docs/misc/BUILD_DEB.md`.
- A published Flatpak file is not the same as a reproducible in-repo Flatpak
  build. Follow `docs/misc/BUILD_FLATPAK.md`.

## 9. Coding Rules

- Prefer session/application patterns over new facades.
- Use application ports for cross-layer behavior.
- Keep GUI workers thin and presentation-oriented.
- Use `Path` and shared normalizers; do not string-build filesystem paths.
- Use atomic writes for manifests, links, settings, sidecars, and durable user
  state; use SQLite transactions for multi-row writes.
- Use the ExifTool/FFmpeg wrappers rather than shell-concatenating paths.
- Recoverable external-tool failures should report warnings without corrupting
  local state.
- Keep comments focused on non-obvious intent, boundaries, or failure modes.

## 10. Rendering And Platform Rules

- macOS media preview defaults to QRhi/Metal; OpenGL/Qt decode are compatibility
  fallbacks. Windows uses QRhi/OpenGL and prefers WIC for non-RAW stills. Linux
  uses QRhi/OpenGL with the Qt still decoder. RAW remains routed through rawpy.
- Platform decoder fallback stays inside the worker lane and preserves
  cancellation/detached RGBA8888/sRGB output.
- GPU residency/LOD policy is a rendering concern, not application workflow.
- Native OsmAnd helper/widget selection belongs to Maps runtime adapters and
  widget factories.
- Packaged builds include required QSB shaders and optional extension assets
  only when those features are claimed by the package.

## 11. Testing And Verification

Run architecture checks after boundary changes:

```bash
python3 tools/check_architecture.py
.venv/bin/python -m pytest tests/architecture -q
```

Use focused tests for the behavior changed. For recognition work include People,
Pets, dashboard, playback/detail annotation, and relevant repository/service
coverage. For Detail rendering changes run the Detail transaction/scheduler/
decoder/cache/session tests and packaged validation on the affected platform.

Required guardrail expectations:

- `application/` has no GUI or concrete persistence imports.
- `infrastructure/` has no GUI imports.
- production source has no `iPhoto.legacy` or `iPhoto.models.*` imports.
- GUI runtime has no compatibility service-factory fallback.
- People/Pets inference is not an automatic application-startup task.
- Architecture and documentation checks are part of CI.

## 12. Release And Documentation Rules

- Keep `README.md`, `docs/readme/README_zh-CN.md`, and
  `docs/readme/README_de.md` semantically aligned.
- README files distinguish development-branch capabilities from the latest
  published binary release.
- `docs/architecture.md` is the current architecture entry point.
- `docs/misc/PETS_RECOGNITION_RUNTIME.md` is the canonical Pets runtime note.
- `docs/requirements/` contains active work or explicitly labelled historical/
  residual-debt material; completed normative work belongs under
  `docs/finished/`.
- Historical requirement text must not silently override current architecture,
  runtime guardrails, or code.
- Run the docs link/parity checks when changing maintained documentation.

This guide is authoritative for new production work. When it conflicts with an
older example or historical requirement, follow the current runtime/session
boundary and update or explicitly supersede the stale documentation.
