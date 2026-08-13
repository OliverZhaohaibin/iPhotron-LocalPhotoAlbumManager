# Changelog

This changelog keeps the current development-branch contract concise. Older
release-by-release detail remains available in Git history and GitHub Releases.

## Unreleased — Startup & Architecture Hardening

### Desktop composition

- The installed GUI entry point is `iPhoto.entrypoint:main`; helper dispatch can
  occur before importing the full Qt desktop runtime.
- `DesktopCoordinatorRuntime` is the production desktop coordinator composition
  root in `gui/coordinators/desktop_coordinator_runtime.py`.
- `gui/coordinators/main_coordinator.py` is now a compatibility import only. New
  architecture and developer documentation must not describe a separate
  production `MainCoordinator` contract.
- Optional feature promotion remains separated from the first-frame path.
  Platform-required GPU Detail construction may still occur before `show()`
  where Qt native-window behavior requires it.

### Recognition activation

- People/Pets model inference is no longer an automatic application-startup or
  post-metadata-scan task.
- `RecognitionCoordinator` can bind services and warm persisted dashboard data
  without initializing inference models.
- Recognition scans are requested only after the People surface has actually
  been shown **and** its first viewport reports ready.
- Scan activation is then delayed briefly so first content/cover delivery is not
  immediately competing with AI work.

This supersedes older Unreleased wording that described Face/Pet workers as
starting after startup or metadata scan completion.

## Unreleased — People & Pets Recognition

### Pets runtime

- Pets remains an optional bounded context with rebuildable `pet_index.db` and
  durable `pet_state.db`.
- The production clustering pipeline is `species-bounded-single-link-v3`.
- Identity clustering is species-separated, applies cannot-link constraints,
  and bounds cluster diameter to prevent uncontrolled single-link chaining.
- People/Pets conflict filtering is geometry-based but is not an unconditional
  “People always wins” rule. Strong face overlap normally suppresses a pet
  candidate; a substantially larger plausible pet-body detection containing a
  smaller face may be preserved by the runtime size/image-coverage exception.
- People and Pets keep independent runtime/durable ownership while the combined
  UI can compose them into cards, groups, gallery queries, and annotations.

### Model trust and delivery

- Production Pets inference does not execute arbitrary Torch Hub Python.
- The pinned DINOv2 upstream revision is a release conversion/provenance input;
  production runtime trust is the prebuilt TorchScript artifact plus manifest
  SHA-256 and exact-size validation.
- The current Pets manifest has `torchscript_url: null`; DINOv2 therefore must
  be packaged or explicitly staged rather than being promised as a first-use
  runtime download.
- `src/extension/models/...` is a build/staging convention, not guaranteed
  tracked content of a fresh clone.

## Unreleased — Gallery / Detail GPU-first Rendering

- Gallery browsing uses sparse asynchronous SQL-backed windows rather than
  materializing entire collections.
- Viewport demand separates visible, guard, speculative, and micro-thumbnail
  work and rejects stale generations.
- Normal Gallery-visible rows require ready thumbnail state and a non-empty
  thumbnail key; repair/backfill rows do not masquerade as ready media.
- Detail still/video opens share a generation-safe render transaction.
- Static Detail and Edit share `PhotoRenderSessionHandle`, source surfaces, GPU
  residency, and immutable edit state instead of restoring a parallel CPU
  full-image preview path.
- Platform decoding/rendering remains adapter-specific: QRhi/Metal is preferred
  on macOS, QRhi/OpenGL on Windows/Linux, with platform decoder fallbacks inside
  the existing worker lane.

## Unreleased — Documentation & Packaging Contracts

- `docs/architecture.md`, `AGENT.md`, and `docs/development.md` now use the
  current desktop/recognition/session contracts.
- Pets historical clustering requirements are explicitly marked as historical;
  `docs/misc/PETS_RECOGNITION_RUNTIME.md` is the canonical runtime note.
- `docs/requirements/README.md` defines Active, residual-debt,
  Historical/Superseded, and Finished requirement states.
- Debian packaging derives the version from `pyproject.toml` and wraps the
  current `dist/entrypoint.dist/` standalone bundle.
- AppImage has an in-repository build guide matching `scripts/build_appimage.sh`.
- Flatpak documentation distinguishes the existing v6.6.8 release bundle from
  current source-build support: this branch does not yet contain a maintained
  Flatpak manifest/build driver.
- Maintained English, Simplified Chinese, and German README files distinguish
  published v6.6.8 binaries from development-branch / Unreleased capabilities.
- Documentation CI validates local Markdown links and README release-artifact
  parity to reduce future localization drift.

## v6.6.8 — Published Release Baseline

The download section in the maintained README files targets the published
v6.6.8 artifacts. Those binaries are a release baseline and must not be assumed
to include every feature described under the Unreleased sections above.

Current development documentation intentionally describes the `edit-base`
branch. When a feature is promoted into a release, move its user-facing release
status from Unreleased into the corresponding versioned release notes and keep
all maintained README languages aligned.

For older detailed release history, use the repository's Git history and GitHub
Releases. The pre-sync changelog remains available in commits prior to this
documentation-contract cleanup.
