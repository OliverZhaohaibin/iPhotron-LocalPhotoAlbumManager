# 🧰 Development Guide

This guide documents the current development contracts for iPhotron. For
architecture-sensitive work, read `../AGENT.md`, `architecture.md`, and the
relevant guardrail under `misc/` before historical requirements.

## Setup

Requirements: Python 3.12+, Git, ExifTool, and FFmpeg/FFprobe.

```bash
git clone https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager.git
cd iPhotron-LocalPhotoAlbumManager
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate        # Windows
pip install -e ".[dev]"
```

The installed desktop entry point is:

```text
iphoto-gui = iPhoto.entrypoint:main
```

`iPhoto.entrypoint` is intentionally lightweight: helper dispatch happens before
the full Qt GUI import. `iPhoto.gui.main:main` is the internal GUI dispatch
target, not the console-script contract.

## Architecture And Startup

Production development follows:

```text
RuntimeContext -> LibrarySession -> application ports/services -> domain
```

`DesktopCoordinatorRuntime` in
`iPhoto.gui.coordinators.desktop_coordinator_runtime` owns the desktop
coordinator graph. `gui/coordinators/main_coordinator.py` is a compatibility
import only; do not introduce or document a separate production
`MainCoordinator` ownership model.

Keep optional heavy imports and AI model initialization off the first-frame
path. A platform-required GPU Detail surface may be created before `show()` when
Qt native-window behavior requires it, but optional feature promotion must not
move unrelated blocking work back into first paint.

### Recognition activation

People/Pets inference is feature-driven. `RecognitionCoordinator` requests scan
activation only after both conditions are true:

1. the People surface has actually been shown; and
2. its first viewport is ready.

Activation then runs after a short quiet delay. Opening the app, opening a
library, or finishing metadata scan does not independently start People/Pets
model inference. Dashboard warmup may read existing local recognition state
without initializing inference models.

## Architecture Checks

```bash
python3 tools/check_architecture.py
.venv/bin/python -m pytest tests/architecture -q
```

Production source must not restore `iPhoto.legacy` / `iPhoto.models.*`, and
application/infrastructure code must not bypass the current layer boundaries.

## Current Guardrails

- Scan publishing: `misc/SCAN_VISIBLE_PUBLISH_GUARDRAILS.md`
- Large-library queries: `misc/LARGE_LIBRARY_QUERY_GUARDRAILS.md`
- Gallery scrolling: `misc/GALLERY_SCROLL_PIPELINE_GUARDRAILS.md`
- Detail acceptance: `requirements/DETAIL_OPEN_BENCHMARK_RUNBOOK.md`
- People/Pets: `misc/PETS_RECOGNITION_RUNTIME.md`
- GUI i18n: `misc/I18N_UI_TEXT_GUARDRAILS.md`
- macOS Maps GL: `misc/MACOS_MAP_GL_TRANSPARENCY_NOTES.md`

The old `docs/requirements/i18n/` reference is obsolete. The current long-term
i18n contract is `docs/misc/I18N_UI_TEXT_GUARDRAILS.md`.

## Internationalization

Use `iPhoto.gui.i18n.tr(...)` or `QCoreApplication.translate(...)` with stable
contexts for user-visible text. Business logic must use stable ids/callbacks,
not translated labels. Long-lived widgets should support `retranslate_ui()`.

```bash
bash scripts/i18n_extract.sh
bash scripts/i18n_compile.sh
python tools/check_i18n_strings.py src/iPhoto/gui src/maps
```

## Optional People Runtime

```bash
pip install -e ".[ai-demo]"
```

People uses InsightFace and ONNX Runtime. Missing AI dependencies must not block
normal browsing, editing, Live Photo, Pets state, Maps, or library state.
`face_index.db` is rebuildable; `face_state.db` is durable.

## Optional Pets Runtime

```bash
pip install -e ".[pets-ai]"
```

The extra supplies `certifi`, `onnxruntime`, `torch`, `torchvision`, and
`usearch`.

Current model layout expected by the Pets contract is:

```text
pets/
  detector/yolox_nano_coco.onnx
  embedding/dinov2_vits14/dinov2_vits14.pt
```

`IPHOTO_PET_MODEL_DIR` can select the model root. Build scripts also use
`src/extension/models/...` as a packaging/staging convention; that directory is
not guaranteed tracked content in a fresh clone.

The detector manifest has a fixed HTTPS source plus integrity metadata. The
DINOv2 manifest describes a prebuilt TorchScript artifact, pinned source
provenance, SHA-256, and exact size. Its current `torchscript_url` is `null`.
Therefore production runtime must not promise a DINOv2 first-use download:
DINOv2 must be packaged or explicitly staged while that URL remains null.

Production inference does not execute arbitrary Torch Hub Python. Torch Hub / a
pinned upstream revision belongs to release conversion and provenance tooling
(`tools/convert_dinov2_torchscript.py`); runtime trust is the prebuilt
TorchScript artifact plus manifest hash/size validation.

The current clustering pipeline is `species-bounded-single-link-v3`: clustering
is species-separated, obeys cannot-link constraints, and bounds cluster diameter
to prevent uncontrolled chaining. Strong People-face overlap normally suppresses
a pet candidate, but a substantially larger plausible pet-body box containing a
smaller face may be preserved by the runtime size/image-coverage exception.
Do not summarize the rule as unconditional “People always wins”.

See `misc/PETS_RECOGNITION_RUNTIME.md` for thresholds, status transitions,
persistence, and reconciliation behavior.

## Maps Development

The optional OsmAnd runtime is staged under:

```text
src/maps/tiles/extension/
```

Its upstream build project is
`OliverZhaohaibin/PySide6-OsmAnd-SDK`. Build the platform runtime there, stage
map resources/search/native helper or widget binaries into the extension tree,
then validate and package from iPhotron. Missing Maps runtime must degrade
gracefully rather than block desktop startup.

## Large-Library / Scan Work

Gallery reads are SQL-first and windowed. Paint/model access stays memory-only;
sparse windows and thumbnail demand load asynchronously and reject stale
generations. Visible scan updates use post-commit `ScanBatchCommitted` events.
Do not restore historical `scanChunkReady` production transport.

`requirements/scan_c_hotspot_optimization.md` contains historical profiling and
an obsolete scanner call graph. New native/compiled optimization decisions must
first profile the current scanner/application/index-store path. See
`requirements/README.md` for document lifecycle rules.

## Packaging

- Windows/general Nuitka: `misc/BUILD_EXE.md`
- Linux standalone: `scripts/build_nuitka_fast.sh` -> `dist/entrypoint.dist/`
- Debian: `misc/BUILD_DEB.md`
- AppImage: `misc/BUILD_APPIMAGE.md`
- Flatpak status: `misc/BUILD_FLATPAK.md`

The Debian/AppImage paths are current in-repository build contracts. The v6.6.8
Flatpak bundle is a published release artifact, but this branch currently has no
maintained in-repository Flatpak manifest/build driver; do not conflate the two.

Offline People/Pets capable packages must explicitly stage their optional Python
runtime and model files. Build documentation must not describe
`src/extension/models` as guaranteed checked-in content.

## Entry Points

| Surface | Current boundary |
| --- | --- |
| Installed GUI | `iPhoto.entrypoint:main` |
| Internal Qt GUI | `iPhoto.gui.main:main` |
| CLI | `iPhoto.cli:app` |
| Desktop coordinator graph | `DesktopCoordinatorRuntime` |
| Library composition | `RuntimeContext` / `LibrarySession` |
| Recognition presentation | `RecognitionCoordinator` |

## Documentation Lifecycle

`requirements/README.md` defines Active, residual-debt, Historical/Superseded,
and Finished states. Historical requirement documents are design evidence, not a
higher-authority production contract than current code, architecture, AGENT, or
active guardrails.

When maintained documentation changes, run the docs link and README parity CI
checks in addition to subsystem-specific tests.
