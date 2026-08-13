# 🔒 Security

## Overview

iPhotron is a local-first photo manager. Core library browsing, editing, Live
Photo handling, persisted People/Pets state, and offline Maps operation do not
require a cloud account. User libraries and iPhotron state remain on local
storage unless the user explicitly invokes a network-backed optional download.

## Filesystem And External Tools

The application reads user-selected media libraries and writes managed local
state such as `.iPhoto/`, folder manifests, thumbnails, recognition state, and
`.ipo` sidecars. Normal editing is non-destructive.

Assign Location is an explicit metadata-write exception: iPhotron saves local
location state first and may then best-effort write GPS metadata back to the
selected original through ExifTool. External-tool errors must not corrupt the
local database/state transaction.

ExifTool and FFmpeg/FFprobe are invoked through the repository wrappers rather
than shell-concatenated user paths.

## Optional Network Access

Normal library operation requires no network connection. Optional runtimes may
have separately controlled download paths, for example a published Maps
extension or model artifact with an explicit source/integrity contract.

A package that claims offline People/Pets or Maps capability must stage the
required optional runtime/assets at build time rather than silently depend on a
network request after installation.

## Model Asset Locations

`src/extension/models/...` is a build/staging convention used by packaging
scripts. It is **not** a guarantee that a fresh source checkout contains every
AI model.

At runtime, model roots may be selected through the feature's configured cache
or environment override. Packaging documentation must distinguish:

- tracked application code/manifests;
- locally staged model assets;
- packaged assets;
- runtime-downloaded assets with a declared download contract.

Do not treat a local staging directory as a trusted source merely because it is
inside the repository checkout.

## Pets Model Trust Boundary

Pets uses a YOLOX detector artifact and a DINOv2 TorchScript embedding artifact.
The model manifest under `src/iPhoto/pets/model_manifest.json` defines the
runtime integrity contract.

### YOLOX detector

The detector entry declares a fixed HTTPS artifact source and SHA-256/size
constraints. When first-use model downloads are enabled, the runtime may fetch
that declared artifact and must validate it before use.

### DINOv2 embedder

The DINOv2 entry separates **source provenance** from **production runtime
trust**:

- `source_repository` and `source_revision` identify the upstream source used by
  release conversion tooling;
- `torchscript_sha256` and `torchscript_size` identify the exact prebuilt
  TorchScript artifact accepted by production runtime;
- the current `torchscript_url` is `null`.

Therefore the current production contract is package/prestage-first for DINOv2.
A missing DINOv2 artifact cannot be described as automatically downloadable
until a fixed runtime URL is explicitly added to the manifest and validated by
the same integrity checks.

### Torch Hub boundary

Production runtime must not execute arbitrary Torch Hub repository Python to
obtain DINOv2. A pinned Torch Hub/upstream revision may be used by controlled
release tooling such as `tools/convert_dinov2_torchscript.py` to reproduce or
validate a TorchScript artifact. That is a build/provenance operation, not a
runtime trust mechanism.

The resulting prebuilt artifact is trusted only after the manifest's expected
hash/size checks succeed. A source revision alone is not sufficient runtime
integrity validation.

## People Model Boundary

People recognition uses the optional InsightFace/ONNX Runtime stack. Packaged
People support must explicitly include or provision the required local model
artifacts. Missing People AI dependencies/models must degrade the recognition
feature without blocking the rest of the library application.

Rebuildable People runtime data and durable People user state remain separate:
`face_index.db` can be rebuilt, while `face_state.db` stores durable choices such
as names/covers/groups/manual faces.

## Pets Persistence Boundary

Pets follows the same rebuildable-versus-durable split:

- `.iPhoto/pets/pet_index.db`: rebuildable detections/identity snapshot;
- `.iPhoto/pets/pet_state.db`: durable names, covers, hidden/rejected decisions,
  redirects, and other explicit user choices;
- `.iPhoto/pets/thumbnails/`: rebuildable crops subject to durable cover
  references.

A runtime rebuild or model-version migration must not silently erase durable
user decisions.

## Recognition Activation And Resource Isolation

Recognition inference is feature-driven. Application startup may warm persisted
recognition summaries, but People/Pets model inference is activated only after
the People surface is actually shown and its first viewport is ready.

This reduces unnecessary model execution during normal startup and keeps
optional AI initialization out of the first-frame path. Missing or invalid model
assets leave recognition unavailable/resumable according to its runtime status
contract rather than turning into a desktop-startup failure.

## Database / Sidecar Safety

- Use SQLite transactions for multi-row state changes.
- Keep scan merges idempotent and preserve durable user choices.
- Use atomic writes for JSON manifests/settings and `.ipo` sidecars.
- Treat `.iPhoto/global_index.db`, People/Pets state databases, and sidecars as
  user-controlled local data; validate inputs and do not execute content from
  them as code.
- Keep rebuildable caches replaceable without making them authoritative durable
  state.

## Maps Runtime

Maps is optional. Native helper/widget binaries and offline map/search data are
packaging/runtime assets and must be discovered through the Maps runtime
boundary. Missing native binaries must produce graceful fallback rather than
cause core startup failure.

## Packaging And Release Provenance

Build outputs should preserve an auditable relationship between source,
optional native/runtime assets, and the produced artifact. Current AppImage and
Linux standalone tooling emit build-manifest provenance; Debian packaging wraps
the standalone bundle rather than silently rebuilding different contents.

A published release artifact does not prove a current source-build path exists.
In particular, the v6.6.8 Flatpak download is distinct from current
`edit-base` build support: this branch does not presently contain a maintained
Flatpak manifest/build driver. See `misc/BUILD_FLATPAK.md`.

## Reporting Security Issues

Do not publish sensitive exploit details, private library data, access tokens, or
other credentials in public issues. Use the repository's available private
security-reporting channel when the finding could materially compromise user
files, packaged binaries, model delivery, or update/download integrity.
