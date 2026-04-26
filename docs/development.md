# 🧰 Development Guide

> Development environment, dependencies, build/package, debugging, code style, and commit conventions for **iPhotron**.

---

## Prerequisites

| Requirement | Version |
|-------------|---------|
| Python | ≥ 3.12 |
| ExifTool | Latest (in `PATH`) |
| FFmpeg / FFprobe | Latest (in `PATH`) |
| Git | Latest |

---

## Setup

### 1. Clone the Repository

```bash
git clone https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager.git
cd iPhotron-LocalPhotoAlbumManager
```

### 2. Create a Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate   # macOS / Linux
.venv\Scripts\activate      # Windows
```

### 3. Install Dependencies

```bash
# Core + development dependencies
pip install -e ".[dev]"
```

This installs all runtime dependencies plus dev tools (`pytest`, `ruff`, `black`, `mypy`).

---

## Dependencies

### Runtime Dependencies

Managed in `pyproject.toml`:

| Package | Purpose |
|---------|---------|
| `jsonschema` | JSON Schema validation |
| `PySide6` | Qt6 GUI framework |
| `Pillow` / `pillow-heif` | Image loading (HEIC support) |
| `imagehash` / `xxhash` | Perceptual & fast hashing |
| `opencv-python-headless` | Image processing |
| `reverse-geocoder` | GPS → location name |
| `pyexiftool` | ExifTool wrapper |
| `numpy` / `numba` | Numeric computation & JIT |
| `mapbox-vector-tile` | Map tile parsing |
| `av` | Video decoding |
| `PyOpenGL` / `PyOpenGL_accelerate` | OpenGL rendering |

### Optional Face Recognition Dependencies

The People face-scanning pipeline is installed through the optional `ai-demo`
extra:

```bash
pip install -e ".[ai-demo]"
```

That extra intentionally stays small:

| Package | Purpose |
|---------|---------|
| `insightface>=0.7.3,<1.0` | Face detection and face embeddings |
| `onnxruntime>=1.18,<2` | ONNX model execution backend |

Do not add InsightFace's unused mask-rendering dependency chain to the runtime
unless the product starts using it directly. The app does not need
`albumentations` or `pydantic` for People clustering.

### Dev Dependencies

```bash
pip install -e ".[dev]"
```

Includes: `pytest`, `pytest-mock`, `pytest-qt`, `ruff`, `black`, `mypy`, `types-Pillow`, `types-python-dateutil`.

---

## Album Naming Rules

Album creation and rename flows must reject directory names reserved for
internal library infrastructure. Today the reserved names are:

- `.iPhoto`
- `.Trash`
- `exported`

These names are intentionally hidden by the library scan layer and therefore
must never be accepted as user album names. If a create/rename flow allows one
of them, the album can appear to "disappear" because the directory still exists
on disk but is filtered out of the visible album tree/dashboard.

Implementation rules:

- Keep the validation in the library layer so every entry point stays aligned
  (`album dashboard`, sidebar menus, and any future CLI/API path).
- Keep the reserved-name list in a single shared source of truth used by both
  name validation and album discovery.
- Raise a normal `LibraryError` path such as `AlbumOperationError` with a clear
  user-facing message; UI surfaces should only display the warning and should
  not duplicate the rule locally.
- Add regression coverage when touching album naming logic:
  library tests should verify reserved names are rejected and existing albums
  remain listed, while UI tests should verify reserved-name rename attempts show
  a warning instead of removing the album from the dashboard.

---

## Maps Extension Development Workflow

### What the maps extension is

iPhotron's offline OsmAnd/OBF runtime is expected to live in a self-contained
directory rooted at `src/maps/tiles/extension/`. At runtime,
`MapSourceSpec.osmand_default()` resolves that directory and expects the
following layout:

| Path | Purpose |
|------|---------|
| `src/maps/tiles/extension/World_basemap_2.obf` | Default offline OBF map dataset |
| `src/maps/tiles/extension/misc/` | OsmAnd miscellaneous resources |
| `src/maps/tiles/extension/poi/` | OsmAnd POI resources |
| `src/maps/tiles/extension/rendering_styles/` | OsmAnd style XML files; the default is `snowmobile.render.xml` |
| `src/maps/tiles/extension/routing/` | OsmAnd routing resources |
| `src/maps/tiles/extension/bin/` | Platform-specific helper/native widget binaries and dependent shared libraries (`.exe`/`.dll` on Windows, ELF binaries/`.so` on Linux) |

This directory is the contract used by:

- local source checkouts
- `iphoto-gui` map startup and `PhotoMapView`
- `scripts/build_nuitka_windows.ps1`
- `scripts/build_nuitka_fast.sh` and Linux standalone packaging
- the Windows installer's optional map-extension package

### Linux runtime notes

On Linux, iPhotron can use both the helper-backed OBF renderer and the native
OsmAnd widget. The native widget currently expects Qt's XCB desktop OpenGL path,
so when that backend is selected iPhotron auto-sets:

- `QT_QPA_PLATFORM=xcb`
- `QT_OPENGL=desktop`
- `QT_XCB_GL_INTEGRATION=xcb_glx`

That means native maps on Linux currently run best on X11 or XWayland. If a
`PySide6-OsmAnd-SDK/` checkout exists either inside this repository root or as a
sibling directory next to it, iPhotron prefers its
`tools/osmand_render_helper_native/dist-linux/` widget build during development.

### Upstream sub-project: `PySide6-OsmAnd-SDK`

The source of truth for building the map extension is the standalone upstream
repository:

- `https://github.com/OliverZhaohaibin/PySide6-OsmAnd-SDK`

That repository exists specifically to build and validate the OsmAnd runtime
outside of the main iPhotron application. It contains:

- vendored `OsmAnd-core`, `OsmAnd-core-legacy`, and `OsmAnd-resources`
- Windows and Linux build scripts/output directories for helper and native widget runtimes
- the PySide6/OsmAnd preview app used to validate the runtime independently
- a stable place to iterate on Qt6/PySide6 integration without touching the
  entire iPhotron application

In practice:

- `PySide6-OsmAnd-SDK` builds the runtime
- `iPhotron` vendors the produced runtime into `src/maps/tiles/extension/`
- packaged builds then consume the vendored extension from this repository

### Recommended build strategy

For Windows and Linux packaging, the recommended path is:

1. build the runtime in `PySide6-OsmAnd-SDK`
2. copy the resulting map data, OsmAnd resources, and native binaries into
   `iPhotron/src/maps/tiles/extension/`
3. verify the runtime from the iPhotron checkout
4. package with Nuitka from the iPhotron checkout

This keeps the OsmAnd-specific toolchain work in the dedicated side project,
while keeping iPhotron releases self-contained.

### Step 1: Clone and prepare the side project

```powershell
git clone https://github.com/OliverZhaohaibin/PySide6-OsmAnd-SDK
cd PySide6-OsmAnd-SDK
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
```

If you want to work with the same Python environment as iPhotron, that is also
fine as long as `PySide6`, `cmake`, and the required Windows toolchains are
available.

### Step 2: Build the native runtime in the side project

For the full iPhotron maps extension on Windows, prefer the MSVC build because
it produces the complete native widget runtime mirrored under
`tools\osmand_render_helper_native\dist-msvc`:

```powershell
powershell -ExecutionPolicy Bypass -File tools\osmand_render_helper_native\build_native_widget_msvc.ps1 -BuildType Release
```

For Linux, build the native helper/widget runtime into `dist-linux`:

```bash
bash tools/osmand_render_helper_native/build_linux.sh
```

Useful alternatives inside `PySide6-OsmAnd-SDK`:

- `build_helper.ps1`
  Shortest path if you only need the helper EXE and optional MinGW widget build.
- `build_helper_official.ps1`
  Runs the official OsmAnd MinGW-oriented chain in a staged workspace.
- `build_native_widget_msvc.ps1`
  Recommended for iPhotron release work because it produces the native widget
  DLL and the `dist-msvc` runtime consumed most directly by the packaging flow.
- `build_linux.sh`
  Produces the Linux helper and `.so` widget runtime under `dist-linux`.

The main outputs you need are:

| Side-project output | Why it matters in iPhotron |
|---------------------|----------------------------|
| `tools\osmand_render_helper_native\dist-msvc\osmand_render_helper.exe` | Helper-backed Python OBF rendering |
| `tools\osmand_render_helper_native\dist-msvc\osmand_native_widget.dll` | Native Qt/OpenGL OsmAnd widget |
| `tools\osmand_render_helper_native\dist-msvc\OsmAndCore_shared.dll` | Native OsmAnd core runtime |
| `tools\osmand_render_helper_native\dist-msvc\OsmAndCoreTools_shared.dll` | Native OsmAnd tools runtime |
| `tools\osmand_render_helper_native\dist-msvc\Qt6*.dll` | Required Qt runtime dependencies for the native/helper binaries |
| `tools/osmand_render_helper_native/dist-linux/osmand_render_helper` | Linux helper-backed Python OBF rendering |
| `tools/osmand_render_helper_native/dist-linux/osmand_native_widget.so` | Linux native Qt/OpenGL OsmAnd widget |
| `tools/osmand_render_helper_native/dist-linux/libOsmAndCore_shared.so` | Linux native OsmAnd core runtime |
| `tools/osmand_render_helper_native/dist-linux/libOsmAndCoreTools_shared.so` | Linux native OsmAnd tools runtime |
| `vendor\osmand\resources\...` | Rendering styles and supporting OsmAnd resources |
| `src\maps\tiles\World_basemap_2.obf` | Default demo OBF dataset used by the extension |

### Step 3: Sync the side-project outputs into `iPhotron`

The safest approach is to copy the side-project outputs into
`src/maps/tiles/extension/` so the iPhotron checkout stays self-contained.

Example PowerShell sync:

```powershell
$sdkRoot = "D:\python_code\iPhoto\PySide6-OsmAnd-SDK"
$repoRoot = "D:\python_code\iPhoto\iPhotos"
$extensionRoot = Join-Path $repoRoot "src\maps\tiles\extension"
$binRoot = Join-Path $extensionRoot "bin"

New-Item -ItemType Directory -Force -Path $extensionRoot, $binRoot | Out-Null

Copy-Item -LiteralPath (Join-Path $sdkRoot "src\maps\tiles\World_basemap_2.obf") `
  -Destination $extensionRoot -Force

foreach ($resourceDir in "misc", "poi", "rendering_styles", "routing") {
  Copy-Item -LiteralPath (Join-Path $sdkRoot "vendor\osmand\resources\$resourceDir") `
    -Destination $extensionRoot -Recurse -Force
}

Copy-Item -LiteralPath (Join-Path $sdkRoot "tools\osmand_render_helper_native\dist-msvc\*") `
  -Destination $binRoot -Recurse -Force
```

Equivalent Linux sync:

```bash
sdk_root="$HOME/python-code/PySide6-OsmAnd-SDK"
repo_root="$HOME/python-code/iPhotron-LocalPhotoAlbumManager"
extension_root="$repo_root/src/maps/tiles/extension"
bin_root="$extension_root/bin"

mkdir -p "$extension_root" "$bin_root"
cp -f "$sdk_root/src/maps/tiles/World_basemap_2.obf" "$extension_root/"
for resource_dir in misc poi rendering_styles routing; do
  rm -rf "$extension_root/$resource_dir"
  cp -a "$sdk_root/vendor/osmand/resources/$resource_dir" "$extension_root/"
done
cp -a "$sdk_root/tools/osmand_render_helper_native/dist-linux/." "$bin_root/"
```

If you are intentionally using the MinGW path instead of MSVC, replace
`dist-msvc` with `dist`. The helper-backed Python renderer only requires the
helper executable plus its dependent DLLs, but the native widget path also
requires a usable widget DLL in the same `bin/` directory.

On Linux and macOS, native widget discovery prefers the sibling
`PySide6-OsmAnd-SDK` build when it exists, so keep that checkout in sync with
the runtime you actually want to exercise.

### Step 4: Verify the runtime from the iPhotron checkout

After syncing the extension, return to the iPhotron repository and verify the
runtime before packaging:

```powershell
cd D:\python_code\iPhoto\iPhotos
python -m pip install -e ".[dev]"
python src\maps\main.py --backend auto
python src\maps\main.py --backend python
python src\maps\main.py --backend native
```

Recommended additional checks:

```powershell
pytest tests\test_maps_main.py tests\test_photo_map_view.py -q
iphoto-gui
```

What to look for:

- `--backend auto` chooses the native widget when it is healthy
- `--backend python` succeeds with the helper-backed OBF renderer
- `--backend native` loads the native widget library without missing runtime errors
- the GUI Location view starts without falling back unexpectedly
- on Linux, the native path starts under X11/XWayland rather than failing with missing GLX/XCB support

### Development-time overrides

For experimentation you can point iPhotron directly at a side-project checkout
without copying files first:

| Environment variable | Purpose |
|----------------------|---------|
| `IPHOTO_OSMAND_OBF_PATH` | Override the `.obf` file |
| `IPHOTO_OSMAND_RESOURCES_ROOT` | Override the OsmAnd resources root |
| `IPHOTO_OSMAND_STYLE_PATH` | Override the active style XML |
| `IPHOTO_OSMAND_RENDER_HELPER` | Override the helper executable/command |
| `IPHOTO_OSMAND_NATIVE_WIDGET_LIBRARY` | Override the native widget library path |
| `IPHOTO_PREFER_OSMAND_NATIVE_WIDGET` | Set to `0` to force the Python OBF path in auto mode |

Example:

```powershell
$env:IPHOTO_OSMAND_OBF_PATH = "D:\python_code\iPhoto\PySide6-OsmAnd-SDK\src\maps\tiles\World_basemap_2.obf"
$env:IPHOTO_OSMAND_RESOURCES_ROOT = "D:\python_code\iPhoto\PySide6-OsmAnd-SDK\vendor\osmand\resources"
$env:IPHOTO_OSMAND_STYLE_PATH = "D:\python_code\iPhoto\PySide6-OsmAnd-SDK\vendor\osmand\resources\rendering_styles\default.render.xml"
$env:IPHOTO_OSMAND_RENDER_HELPER = "D:\python_code\iPhoto\PySide6-OsmAnd-SDK\tools\osmand_render_helper_native\dist-msvc\osmand_render_helper.exe"
$env:IPHOTO_OSMAND_NATIVE_WIDGET_LIBRARY = "D:\python_code\iPhoto\PySide6-OsmAnd-SDK\tools\osmand_render_helper_native\dist-msvc\osmand_native_widget.dll"
iphoto-gui
```

Linux example:

```bash
export IPHOTO_OSMAND_OBF_PATH="$HOME/python-code/PySide6-OsmAnd-SDK/src/maps/tiles/World_basemap_2.obf"
export IPHOTO_OSMAND_RESOURCES_ROOT="$HOME/python-code/PySide6-OsmAnd-SDK/vendor/osmand/resources"
export IPHOTO_OSMAND_STYLE_PATH="$HOME/python-code/PySide6-OsmAnd-SDK/vendor/osmand/resources/rendering_styles/default.render.xml"
export IPHOTO_OSMAND_RENDER_HELPER="$HOME/python-code/PySide6-OsmAnd-SDK/tools/osmand_render_helper_native/dist-linux/osmand_render_helper"
export IPHOTO_OSMAND_NATIVE_WIDGET_LIBRARY="$HOME/python-code/PySide6-OsmAnd-SDK/tools/osmand_render_helper_native/dist-linux/osmand_native_widget.so"
iphoto-gui
```

This is convenient for debugging, but release builds should still copy the
runtime into `src/maps/tiles/extension/` so the repository and packaged app stay
self-contained.

### Linux packaged native-widget guardrails

The Linux source checkout and the Linux Nuitka bundle must be treated as two
separate runtime targets. A map preview that works from `python src/maps/main.py`
does **not** prove that the packaged GUI will survive opening the map section.
The failure mode that triggered this guidance was:

```text
ERROR: Failed to initialize GLEW: GLX 1.2 and up are not supported
```

That error appeared only after entering the map section in a packaged build,
even though the unfrozen source checkout already had Linux X11 forcing in the
main entry point.

When touching Linux map packaging, keep these rules in place:

- Treat packaged/frozen Linux runs as a dedicated code path. Before
  `QApplication` is created, force `QT_QPA_PLATFORM=xcb` for packaged builds
  unless `IPHOTO_ALLOW_PACKAGED_LINUX_WAYLAND=1` is set explicitly for
  debugging.
- When `QT_QPA_PLATFORM=xcb`, keep `QT_OPENGL=desktop` and
  `QT_XCB_GL_INTEGRATION=xcb_glx` aligned so the native OsmAnd widget gets the
  GLX-backed desktop OpenGL context expected by GLEW.
- Do not use the generic OpenGL probe as the only gate for the native widget.
  Backend selection must still run `probe_native_widget_runtime(...)`; if the
  native library loads cleanly, prefer it even when the generic Qt OpenGL probe
  failed, and if the runtime probe fails, log the reason and fall back to the
  Python OBF path.
- Keep the Linux/Nuitka packaging inputs explicit. The current fast build
  script needs `--enable-plugin=pyside6`,
  `--include-qt-plugins=qml,multimedia`, `--include-package=OpenGL`, and
  `--include-package=OpenGL_accelerate` so the packaged runtime matches the
  editable environment more closely.
- After a Linux Nuitka build, verify the packaged OsmAnd binaries can still
  resolve Qt at runtime. If the packaged `maps/tiles/extension/bin` runtime can
  not find the bundled PySide6 Qt libraries, repair its RUNPATH before treating
  the build as releasable.
- Regressions must be tested from the packaged executable, not only from the
  source checkout. The minimum smoke test is: start the packaged app on Linux,
  switch into the map section, confirm no GLEW/GLX error is emitted, and verify
  the view uses the native widget only when `probe_native_widget_runtime(...)`
  succeeds.

For future work, do not remove the packaged-Linux override just because
development mode works under Wayland/XWayland. If you want to relax that rule,
first prove the packaged map section is stable from a fresh Nuitka bundle and
keep the opt-out behind a documented environment variable.

---

## Face Recognition Development Workflow

### Runtime contract

The People feature scans assets in the background, detects faces with
InsightFace, stores face embeddings, and clusters those embeddings into people.
The runtime model cache is shared at:

| Path | Purpose |
|------|---------|
| `src/extension/models/buffalo_s/` | Checked-in InsightFace model cache |
| `src/extension/models/buffalo_s/det_500m.onnx` | Face detector |
| `src/extension/models/buffalo_s/w600k_mbf.onnx` | Face recognition embedding model |

The default packaged path is resolved from the installed package as
`extension/models`. For local debugging, override it with:

```powershell
$env:IPHOTO_FACE_MODEL_DIR = "D:\python_code\iPhoto\iPhotos\src\extension\models"
```

The model directory may be absent in a packaged build. In that case
InsightFace can download the model pack on first use, but release builds should
still bundle the model cache when an offline-ready distribution is required.

### InsightFace import rules

Always import the concrete FaceAnalysis module through the People pipeline's
compatibility path:

```python
from insightface.app.face_analysis import FaceAnalysis
```

Do not switch back to:

```python
from insightface.app import FaceAnalysis
```

The package-level import pulls in InsightFace's mask-rendering path, which can
drag in `albumentations` and `pydantic`. In Nuitka builds this has produced
runtime annotation failures such as `name 'Literal' is not defined` and
`name 'NDArray' is not defined`.

The pipeline intentionally installs two compatibility shims before importing
InsightFace:

- runtime typing names in `builtins`, for third-party annotations evaluated at
  runtime inside packaged apps
- a lightweight `albumentations` stub, because iPhotron does not use
  InsightFace mask rendering

Keep these shims in place unless the packaging strategy changes and the
replacement has been verified in a Nuitka build.

### Required InsightFace modules

People clustering only needs bounding boxes and embeddings. Keep
`FaceAnalysis` constrained to:

```python
allowed_modules=["detection", "recognition"]
```

Do not load `landmark_2d_106`, `landmark_3d_68`, or `genderage` for the People
scan. Those models are not used for clustering and have caused packaged
asset-level failures such as:

```text
'NoneType' object has no attribute 'shape'
```

If a future feature needs landmarks or gender/age attributes, add that feature
behind a separate tested path and validate it in a Nuitka package before
enabling it for background scanning.

### Scan status and retry rules

Face scan state is stored per asset. The intended behavior is:

- `pending`: asset has not been scanned yet
- `done`: scan completed, with or without detected faces
- `skipped`: asset is not eligible for face scanning
- `retry`: asset failed once and should be attempted again
- `failed`: asset failed after a retry and should not block the whole queue

Important rules:

- A batch-level exception should pause scanning and surface the real exception
  text in the People page.
- An asset-level exception should be logged, then marked `retry` on the first
  failure.
- If the same asset fails again while already in `retry`, mark it `failed` so
  the queue can continue.
- A full library rescan must reset `retry` and `failed` face statuses back to
  the initial status. It should preserve only stable completed states such as
  `done` and `skipped` when the asset identity is unchanged.

This prevents a broken image or transient packaged-runtime issue from
deadlocking People scanning until the user deletes the database manually.

### Debugging packaged face scan failures

The app writes rotating logs to:

```powershell
%LOCALAPPDATA%\iPhoto\iPhoto.log
```

For custom locations:

```powershell
$env:IPHOTO_LOG_DIR = "D:\tmp\iphoto-logs"
```

Useful log messages:

- `Face scanning paused: ...`
  Batch-level failure; the message should include the actual exception.
- `Face detection failed for ...`
  Full traceback for an asset-level detection failure.
- `Face scan failed for asset ...`
  The worker marked a specific asset for retry or failure.

When a packaged build says `Some assets could not be face scanned and will be
retried after a rescan`, inspect the log before changing model paths. That
message means model initialization succeeded far enough to process assets, but
at least one asset failed during detection/embedding.

### Verification checklist

After changing People scanning, run the focused tests:

```powershell
pytest tests\test_people_pipeline.py tests\test_people_service.py tests\cache\test_global_repository.py tests\test_face_cluster_pipeline.py
```

For a local smoke test against a real image:

```powershell
python -c "from pathlib import Path; from iPhoto.people.pipeline import FaceClusterPipeline; p=FaceClusterPipeline(model_root=Path('src/extension/models')); out=p.detect_faces_for_rows([{'id':'test','rel':'DSCF5586.JPG'}], library_root=Path(r'C:\Users\Olive\Downloads\face'), thumbnail_dir=Path(r'C:\Users\Olive\Downloads\face\.iPhoto\faces')); print([(x.asset_rel, x.error, len(x.faces)) for x in out]); print(sorted(p._ensure_face_analysis().models.keys()))"
```

The loaded InsightFace models should be only:

```text
['detection', 'recognition']
```

For release verification, rebuild with Nuitka and test the People page from the
packaged executable, not from the editable source checkout.

---

## Build & Package

### Running the Application

```bash
# Launch the GUI
iphoto-gui
```

### Building the Executable

For distribution, iPhotron uses **Nuitka** with an AOT compilation step for Numba filters.

#### Step 1: AOT Compilation

```bash
python src/iPhoto/core/filters/build_jit.py
```

This generates a compiled C-extension (`.so` / `.pyd`) in `src/iPhoto/core/filters/`.

#### Step 2: Build with Nuitka

```bash
bash scripts/build_nuitka_fast.sh
```

The script uses a startup-optimized Nuitka profile (`--standalone`, `--python-flag=no_site`, `--lto=yes`, `--clang`) and excludes heavy dev/runtime-only packages from the final bundle.
It also includes `src/maps/tiles`, so Linux standalone builds keep the bundled
OBF/resources layout intact as long as `src/maps/tiles/extension/` is staged
correctly before packaging.

For Windows release work that includes the native maps extension, prefer:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_nuitka_windows.ps1 -OutputDir build
```

That script stages `src/maps/tiles/extension/bin` from the native runtime before
invoking Nuitka, so it is the recommended packaging entry point whenever the
OsmAnd helper/native widget runtime is part of the build.

See [docs/misc/BUILD_EXE.md](misc/BUILD_EXE.md) for detailed troubleshooting and manual flags.

---

## Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_example.py

# Run tests matching a pattern
pytest -k "test_scan"
```

Test configuration is in `pyproject.toml` under `[tool.pytest.ini_options]`:
- Test paths: `tests/`
- GUI tests (`tests/ui`, `tests/gui`) are excluded by default.

---

## Debugging

### GUI Debugging

```bash
# Enable Qt debug output
export QT_DEBUG_PLUGINS=1
iphoto-gui
```

### Common Issues

| Issue | Solution |
|-------|----------|
| `ExifTool not found` | Ensure `exiftool` is in your `PATH` |
| `FFmpeg not found` | Ensure `ffmpeg` and `ffprobe` are in your `PATH` |
| OpenGL errors | Update GPU drivers; ensure OpenGL 3.3+ support |
| `_jit_compiled` module not found | Run AOT compilation step (see Build section) |

---

## Popup Guardrails

The application has shared popup plumbing for information/warning surfaces.
When popup code is refactored, prefer the project's own popup implementation
instead of dropping back to native/system-styled `QMessageBox` windows.

- Route routine in-app warning/info popups through the shared themed helpers.
- Make popup theme resolution follow the active app/window theme before the OS
  color scheme.
- Keep popup positioning centered on the hosting top-level window.
- See
  [`docs/misc/PROJECT_POPUP_GUARDRAILS.md`](misc/PROJECT_POPUP_GUARDRAILS.md)
  for the project-wide rule plus the People dashboard regression checklist.

### Context Menu Guardrails

Qt context menus must use the project menu styling instead of bare `QMenu`
instances. The main window uses translucent rounded chrome, and unstyled menus
can inherit that translucency and render with a transparent background.

- For sidebar and album-related menus, call
  `_apply_main_window_menu_style(menu, parent)` from
  `iPhoto.gui.ui.menus.album_sidebar_menu` before adding or executing actions.
- If a menu uses a local stylesheet instead, set
  `menu.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)` and apply
  an explicit opaque `QMenu { background-color: ... }` rule.
- Do not create and execute a naked `QMenu(self)` from widgets such as
  dashboards, cards, sidebars, or popups.
- When adding a new right-click surface, add or update a focused GUI test that
  verifies the menu is styled and that important actions are present.

#### Unified Right-Click Menu Rules

The app now treats sidebar, dashboard, and gallery context menus as one shared
interaction system. When you add or change a right-click entry, keep these
rules aligned across surfaces:

- Use `MenuContext` + `populate_menu()` for declarative menus whenever the
  surface already participates in the shared menu system.
- Right-clicking an asset in gallery must first sync selection to the clicked
  row before computing menu visibility, so selection-scoped actions operate on
  the intended asset.
- Album-cover actions must resolve paths relative to the active album root
  before calling `facade.set_cover(...)`. Do not assume `AssetDTO.rel_path`
  already matches the current album root.
- `Rename…` is the canonical label for rename actions. Use the same ellipsis
  style and the same empty-name validation across sidebar and pinned-item menus.
- Pinned-item rename is a sidebar-local alias. Persist it through
  `PinnedItemsService` instead of mutating the underlying album/person/group
  entity name.
- `Pin`/`Unpin` and `Rename…` should stay adjacent on sidebar-driven menus so
  users can manage the same entity without hunting across different surfaces.
- Any regression around menu visibility or per-surface action parity needs a
  targeted test in the menu/controller/widget layer that owns that surface.

### People UI Conventions

#### Reusable person-picker popup

The canonical picker for choosing one or more People cards is
`GroupPeopleDialog` in
`src/iPhoto/gui/ui/widgets/people_dashboard_dialogs.py`.

Use this dialog for all People-selection flows instead of creating ad-hoc
`QInputDialog` or combo-box popups. Current uses include:

- `New Group` from the People dashboard
- `Merge Into...` from a People card context menu
- `Choose Someone Else...` from the Info panel face actions

When reusing it:

- pass `dark_mode=` from the hosting window/theme context explicitly when the
  caller is not the People dashboard itself
- use `min_selection=1` and `max_selection=1` for single-target pickers
- customize `title_text`, `prompt_text`, and `confirm_text` per workflow
- keep multi-select behavior only for true grouping flows


---

## Code Style

### Linters & Formatters

| Tool | Purpose | Config |
|------|---------|--------|
| `ruff` | Linting & import sorting | `pyproject.toml` `[tool.ruff]` |
| `black` | Code formatting | `pyproject.toml` `[tool.black]` |
| `mypy` | Static type checking | — |

### Style Rules

- **Line length:** ≤ 100 characters
- **Type hints:** Use full annotations (e.g., `Optional[str]`, `list[Path]`, `dict[str, Any]`)
- **Imports:** Sorted by `ruff` (isort-compatible)
- **Docstrings:** Use triple-double-quote style

### Running Linters

```bash
# Lint check
ruff check src/

# Auto-fix lint issues
ruff check --fix src/

# Format code
black src/

# Type check
mypy src/
```

---

## Commit Conventions

Follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>(<scope>): <short summary>

<optional body>

<optional footer>
```

### Types

| Type | Description |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `style` | Formatting (no code change) |
| `refactor` | Code refactoring (no feature/fix) |
| `perf` | Performance improvement |
| `test` | Adding or updating tests |
| `build` | Build system or dependencies |
| `ci` | CI/CD configuration |
| `chore` | Maintenance tasks |

### Examples

```
feat(edit): add selective color adjustment panel
fix(cache): resolve SQLite WAL checkpoint deadlock
docs: update architecture diagram with MVVM layer
refactor(gui): extract coordinator from main window
test(core): add unit tests for curve resolver
```

---

## Project Entry Points

| Command | Entry Point | Description |
|---------|-------------|-------------|
| `iphoto-gui` | `iPhoto.gui.main:main` | GUI application |
