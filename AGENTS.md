# AGENTS.md - iPhotron Development Principles

This guide defines the stable rules for coding agents and contributors. Read
the matching task references below before changing a protected workflow.
[Current architecture](docs/architecture.md) owns detailed runtime contracts,
platform policies, cache budgets, and validation commands. Those contracts
remain binding when referenced here.

## 1. Product Invariants

- **Folder-native library.** A folder is an album; browsing requires no import.
- **Local-first.** Core browsing, editing, Live Photo, People, Pets, and Maps
  behavior runs locally. Missing optional runtimes must degrade gracefully.
  Model and map provisioning use the existing extension installation workflows.
- **Non-destructive editing.** Normal visual edits are stored in `.ipo`
  sidecars and preserve original media.
- **Explicit metadata write-back.** Assign Location persists local state and
  its durable write-back job first, then best-effort writes GPS through
  ExifTool. Failures produce warnings without rolling back local state.
- **Durable user choices.** Rescans and rebuilds must preserve favorites,
  hidden/trash state, pinned items, album order, manual metadata, People/Pets
  names, covers, and hidden flags, People order, groups and group order, group
  covers and caches, manual faces, rejected pet detections, and identity redirects.
- **Cross-platform desktop.** macOS, Windows, and Linux remain supported.
  Platform rendering, maps, external tools, and AI runtime choices belong
  behind adapters and runtime discovery.

## 2. Runtime And Layer Boundaries

GUI, CLI, watchers, Qt workers, and automation enter library behavior through
`RuntimeContext`, `LibrarySession`, and application/session surfaces. Headless
callers use `create_headless_library_session(root)`.

```text
gui -> bootstrap/runtime -> application -> domain
infrastructure -> application ports / domain values
bounded contexts -> application ports / domain values
```

- Production runtime must not import `iPhoto.legacy` or `iPhoto.models.*`.
  Do not restore `src/iPhoto/legacy/` or tests of its removed interfaces;
  preserve required behavior through current application/session surfaces.
- `domain/` contains pure values and services: no IO, Qt, SQLite, application,
  GUI, or infrastructure dependencies. Application workflows depend on ports
  and domain values, without GUI or concrete cache/persistence imports.
- Infrastructure, cache, core, IO, library, People, Pets, and shared recognition
  code must not import GUI modules. Concrete adapters implement application
  ports; GUI and application code must not call `cache/index_store/` directly.
- `RuntimeContext` owns process composition and the active library lifecycle.
  Its lazy `map_extensions` and `recognition_extensions` services are
  process-scoped and can install components without an active library.
- `LibrarySession` owns library-scoped adapters and services.
  `LibraryRuntimeController` coordinates the active session. Do not construct
  independently bound fallback services in GUI/CLI code. Unbound People/Pets
  services may provide empty presentation state before a library exists, but
  must not open repositories or become another source of library truth.
- Business workflows belong in application/session services or their owning
  bounded context; pure rules belong in `domain/` or `core/`. GUI code owns
  presentation, Qt transport, and rendering state. Workers remain thin adapters
  calling session/application services.
- Module ownership is described in the architecture's
  [layer boundaries](docs/architecture.md#layer-boundaries). Maps lives in
  `src/maps/`; shared recognition coordination lives in `src/iPhoto/recognition/`.

## 3. Files, State, And Recovery

Library state lives under `<LibraryRoot>/.iPhoto/`. The complete layout is in
the [persistence model](docs/architecture.md#persistence-model).

| State | Required treatment |
| --- | --- |
| `global_index.db` | Authoritative asset rows, pagination, Live Photo roles, face/pet status, and repository-backed user state. It is not a disposable cache. |
| `faces/face_index.db`, `pets/pet_index.db` | Rebuildable runtime snapshots; commits must preserve and repair their durable state stores. |
| `faces/face_state.db`, `pets/pet_state.db` | Durable user choices. People group cache tables also hold custom cover choices. |
| `recognition/operations.db` | Durable mutation journal and event outbox. Preserve unfinished recovery and pending dispatch across restarts and snapshot rebuilds. |
| `cache/thumbs/`, `cache/detail-surfaces/` | Rebuildable thumbnail and neutral source-surface caches. |
| `faces/thumbnails/`, `pets/thumbnails/` | Cropped images with durable references; protect user cover/manual-face references when rebuilding or cleaning up. |
| `links.json` | Derived Live Photo compatibility materialization; repository/session roles remain authoritative. |
| `.ipo` sidecars beside media | Durable non-destructive edit parameters. |

- Album metadata remains folder-local: `.iphoto.album.json`, the minimal
  `.iphoto.album` discovery marker, and compatible `.iPhoto/manifest.json`.
- Scan merges must be idempotent and preserve durable choices even when facts
  and user state share a SQLite file. Never clear state based only on a table
  or directory being named "cache".
- Pets thumbnail cleanup is candidate-based after a successful commit. Retain
  paths referenced by current detections or durable covers; never replace it
  with a directory sweep that can delete crops from an in-flight batch.
- Cross-kind identity redirects and mixed groups are durable coordination
  state in the People state repository. People and Pets retain independent
  detection, embedding, canonical record, and runtime snapshot ownership.
- Use session `recognition_queries`, `recognition_merges`, and
  `recognition_edits` for composed queries and cross-context identity edits.
  Writes use the shared `recognition_mutations` coordinator and its recovery
  journal; do not bypass its mutation lease, state guards, or recovery path.
- Reject merges between identities with incompatible hidden state at the
  service/repository boundary, including People, Pets, and cross-kind redirects.

## 4. Startup, Rendering, And Background Work

- Prepare the hidden Detail shell and all QRhi surfaces with final native
  parents before widgets such as `QMenuBar` can force native handle creation
  and before top-level `show()`. Complete Detail chrome/multimedia, desktop
  coordinators, saved-library binding, and Gallery warm-up after `firstPainted`.
  Timers, platform allowlists, reparenting after show, and hide/show workarounds
  must not bypass the [startup contract](docs/architecture.md#runtime-contract).
- Failure to prepare the native hierarchy is terminal for that process.
  Startup retry requires a valid visible shell. Each startup generation has
  one terminal outcome; asynchronous library/viewport/render results must
  reject superseded sessions, generations, and revisions. Shutdown must not
  allow late callbacks to reactivate old library work.
- People/Pets service binding and cached dashboard warm-up must not initialize
  AI pipelines. Desktop recognition activation requires the People view to be
  shown, its first viewport ready, and the existing quiet-delay gate to pass.
  Startup, metadata scan completion, pending rows, and interactive rescans must
  not bypass that gate. Explicit headless/controller activation remains a
  separate integration surface.
- Missing People/Pets dependencies or models must preserve pending work and
  keep other workflows usable. Keep workers and their failures independent.
  Production Pets loads verified local TorchScript artifacts; Torch Hub is
  restricted to release conversion tooling.
- Gallery reads are SQL-first and windowed. Normal visible rows are
  thumbnail-ready with a non-empty `thumb_cache_key`; old no-key rows require
  repair/backfill. Paint, model access, and thumbnail peeks are memory-only.
  Disk access and decoding run off the UI thread; bounded `QPixmap` publication
  remains on the GUI thread.
- Gallery/Filmstrip demands keep independent leases and generations while
  sharing physical thumbnail keys. Preserve separate visible, guard, and far
  speculative capacity and avoid regenerating memory/disk cache hits.
- Detail still rendering has one GPU-first transaction/scheduler/cache path.
  A render transaction owns its generation and single terminal outcome.
  Static Detail/Edit share `PhotoRenderSessionHandle`; entering/exiting Edit,
  compare, fullscreen, and adjustment updates must not introduce another
  source decoder, CPU full-image preview session, or duplicate texture upload.
  The removed Detail v2 frame cache is not an extension point.
- Sidecar changes replace immutable `EditRenderState` only. They must not enter
  `DetailDecodeKey` or invalidate source-identical neutral surfaces/textures.
  Persist sidecars through session edit services or `EditSidecarPort`;
  thumbnail rendering may consume edit state without owning persistence.
  Preserve viewport-based initial LOD, bounded residency, and replacement only
  after a real draw, as specified by the
  [rendering contract](docs/architecture.md#gallery-to-detailedit-rendering).
- Backend selection stays in runtime adapters and widget factories, including
  `MapRuntimePort` for Maps availability. Location aggregation and marker-click
  semantics use session services; overlay painting and hit testing stay in GUI.
  Platform details and experimental restrictions remain binding through the
  task references below.

## 5. Coding And UI Rules

- Prefer existing session/application patterns and ports over new facades.
  Use `Path` and shared path normalizers rather than string-building paths.
- Validate album/link JSON against existing schemas. Use atomic writes for
  manifests, links, settings, sidecars, and file-backed user state; use SQLite
  transactions for multi-row writes and scan merges.
- Use the `utils/` ExifTool/FFmpeg wrappers without shell-concatenating user
  paths. Recoverable tool failures return warnings and preserve local state.
- New GUI text uses `iPhoto.gui.i18n.tr(context, source_text)` or
  `QCoreApplication.translate(...)`, English source strings, and named dynamic
  placeholders. Keep user data and technical values untranslated; use shared
  locale formatters for dates, numbers, and sizes.
- Long-lived widgets refresh through `retranslate_ui()` without rebuilding the
  library/session. Commands use stable ids, callbacks, types, or `QAction.data()`
  rather than translated labels. Update and compile translation resources as
  required by the i18n guardrail.
- Routine information/warning popups use `dialogs.show_information()` /
  `dialogs.show_warning()` and the shared themed popup family. Preserve active
  app-theme inheritance and positioning relative to the top-level window;
  People/Pets confirmations use the existing shared dialogs.
- Extension archive/network/filesystem operations belong in infrastructure
  installers behind application services; Qt workers transport progress and
  typed results. Preserve archive verification, locking, user-writable managed
  targets, and read-only package fallbacks. A staged `pending_restart` result
  must not be presented as activated.
- Keep comments focused on non-obvious intent, boundaries, and failure modes.

## 6. Required Task References And Verification

Read the matching references before changing these workflows, then run their
applicable regression checks. Detailed budgets, platform policies, and acceptance
matrices remain owned by these documents.

| Change | Read first |
| --- | --- |
| Runtime/session boundaries or service composition | [Architecture](docs/architecture.md#runtime-contract) and [verification groups](docs/architecture.md#verification-by-change-type) |
| Scan visibility or ready-row publishing | [Scan publishing guardrails](docs/misc/SCAN_VISIBLE_PUBLISH_GUARDRAILS.md) |
| Collection SQL, pagination, or large-library queries | [Large-library query guardrails](docs/misc/LARGE_LIBRARY_QUERY_GUARDRAILS.md) |
| Gallery/Filmstrip demand, thumbnails, or album-rename cache rekeying | [Gallery scroll guardrails](docs/misc/GALLERY_SCROLL_PIPELINE_GUARDRAILS.md) |
| Move/delete/restore planning, trash state, or optimistic UI | [Move/restore guardrails](docs/misc/MOVE_RESTORE_OPTIMISTIC_UI_GUARDRAILS.md) and [trash state guardrails](docs/misc/TRASH_RESTORE_STATE_GUARDRAILS.md) |
| Startup phases, native surfaces, or shutdown | [Startup benchmark](docs/requirements/STARTUP_BENCHMARK_RUNBOOK.md) and [manual validation matrix](docs/requirements/STARTUP_MANUAL_VALIDATION_MATRIX.md) |
| Detail/Edit, decoders, source caches, residency, or viewer behavior | [Rendering contract](docs/architecture.md#gallery-to-detailedit-rendering), [Detail tests](docs/architecture.md#detail-rendering-decoders-caches-and-edit), and [Detail benchmark](docs/requirements/DETAIL_OPEN_BENCHMARK_RUNBOOK.md) |
| People/Pets identities, recognition mutations, or crop cleanup | [Recognition coordination](docs/architecture.md#recognition-coordination), [Pets runtime](docs/misc/PETS_RECOGNITION_RUNTIME.md), and [dashboard/popup contracts](docs/misc/PROJECT_POPUP_GUARDRAILS.md) |
| GUI text, menus, themes, or dialogs | [i18n guardrails](docs/misc/I18N_UI_TEXT_GUARDRAILS.md) and [popup guardrails](docs/misc/PROJECT_POPUP_GUARDRAILS.md) |
| Maps runtime or extension installation | [Map installation](docs/misc/MAP_EXTENSION_INSTALLATION.md) and [packaged validation](docs/requirements/MAP_EXTENSION_INSTALL_VALIDATION.md) |
| People/Pets model installation or shared installer code | [Pets runtime](docs/misc/PETS_RECOGNITION_RUNTIME.md) and [recognition packaged validation](docs/requirements/PEOPLE_PETS_EXTENSION_INSTALL_VALIDATION.md); shared installer changes also require Map regressions |
| Windows graphics backend or experimental D3D11 | [Windows QRhi support matrix](docs/misc/WINDOWS_D3D11_QRHI_STATUS.md) |

Run after boundary changes, and retain architecture checks in CI:

```bash
python3 tools/check_architecture.py
.venv/bin/python -m pytest tests/architecture -q
```

Select other checks from the architecture's
[verification groups](docs/architecture.md#verification-by-change-type) and the
matching task references. Platform backend changes also require packaged manual
validation on the target OS; source/offscreen tests do not prove native decode,
graphics, or compositor behavior.

Use temporary libraries/settings or isolated copies for tests and manual
validation, and dedicated benchmark libraries for performance runs. Never clear
or mutate a user's production library to prepare a test. Report the commands
actually run, results, and remaining validation gaps. Passing static checks
does not waive documented rules outside their current coverage.

## 7. Documentation And Release Maintenance

- Keep `README.md` concise and product-facing, and `docs/architecture.md` as the
  current architecture entry point. Update owning references when contracts,
  budgets, or platform support change; do not silently relax a rule to match
  an implementation violation.
- Keep future work and current validation protocols in `docs/requirements/`;
  move completed/superseded designs to `docs/finished/` and confirmed unresolved
  debt to `docs/technical-debt/`. Archived documents are historical evidence,
  not current implementation instructions. The completed vNext migration is
  archived under [docs/finished/refactor/vnext-2026-06/](docs/finished/refactor/vnext-2026-06/).
- Keep Detail/startup runbooks aligned with production profiler stages, cache
  tiers, decoder names, milestones, SLO fields, and native-surface lifecycle.
  Update the startup manual matrix with lifecycle changes.
- Packages must include required QSB shaders, translation resources, and Maps
  runtime assets for enabled features. Manual GUI/library acceptance checks
  supplement the architecture guardrails.
