# Building the Executable with Nuitka and AOT

This document outlines the process for building the iPhotron executable, including the mandatory Ahead-Of-Time (AOT) compilation step for Numba filters.

## Overview

The application uses **Numba** for JIT-compiled image processing kernels at
development time. For release builds the kernels are compiled ahead-of-time
(AOT) into a native C-extension so that the heavy `numba` and `llvmlite`
packages can be completely excluded from the final distribution.

Runtime Numba imports use **conditional (try/except)** import guards; the AOT
build tool itself requires Numba. When the compiled extension (`_jit_compiled`) is present the
application loads it directly; when it is absent and Numba is installed it
falls back to runtime JIT; otherwise it uses a pure-NumPy implementation.
This means the executable works correctly without `numba` or `llvmlite`
installed as long as the AOT module has been built.

## Prerequisites

1. Install the development dependencies (Numba is required for the AOT
   compilation step):

   ```bash
   python -m pip install -e ".[dev]"
   ```

2. Ensure **Nuitka** is installed:

   ```bash
   python -m pip install nuitka
   ```

## Maps Extension in Release Builds

The [Maps development guide](MAP_EXTENSION_DEVELOPMENT.md) owns SDK build,
sync, and staging. SDK commands run in the separate upstream checkout, not this
repository. Stage the intended platform runtime under
`src/maps/tiles/extension/` before a build that includes bundled maps.

The layout includes `World_basemap_2.obf`, OsmAnd resource directories,
`search/geonames.sqlite3`, and helper/native libraries under `bin/`. Windows
base builds omit this large extension and can install it through Settings;
`-IncludeOptionalAssets` includes staged assets. The maintained Linux and macOS
scripts include staged map assets. Runtime discovery keeps managed user targets
separate from read-only bundled fallbacks.

Keep all required QSB shaders and translations in every enabled media/UI
configuration. Test the packaged app on the target OS using the
[Map installation matrix](../requirements/MAP_EXTENSION_INSTALL_VALIDATION.md)
and the [startup matrix](../requirements/STARTUP_MANUAL_VALIDATION_MATRIX.md).
A successful source map preview is not packaged acceptance.

## Face Recognition in Release Builds

The People feature uses InsightFace for face detection and embeddings. The
maintained platform scripts explicitly include this runtime. Any frozen build
that promises People scanning must include its Python dependencies even when
models are provisioned later: standalone/no-site output will not read packages
from the user's Python environment after packaging.

Required Python packages for the face runtime are:

```toml
ai-demo = [
  "insightface>=0.7.3,<1.0",
  "onnxruntime>=1.18,<2",
]
```

Install them into the build environment before running Nuitka:

```powershell
python -m pip install -e ".[ai-demo]"
```

People loads local ONNX models with InsightFace downloads disabled. The
[shared extension contract](../architecture.md#recognition-extension-installation)
owns installation roots, automatic attempts, and recovery. The official ZIP supplies both
recognition model sets on first use or through Settings. An offline build can
bundle models from the ignored `src/extension/models/` staging directory, with
the People ONNX files under `src/extension/models/buffalo_s/`.

On Windows the extension is installed into
`%LOCALAPPDATA%\iPhoto\extensions\recognition\v1\models`. The Face model
directory can be overridden for debugging:

```powershell
$env:IPHOTO_FACE_MODEL_DIR = "D:\path\to\models"
```

Important packaging rules:

- Include `insightface` and `onnxruntime` in the Nuitka bundle.
- On Windows, `-IncludeOptionalAssets` includes staged `src/extension/models`
  as `extension/models`. Linux/macOS include that directory when it exists.
  Validate the intended offline payload before building.
- Do not rely on installing `insightface` or `onnxruntime` next to an already
  built executable; standalone/no-site builds will not load those packages.
- Exclude `albumentations`, `albucore`, `pydantic`, `pydantic_core`, and
  `typing_inspection`. The People pipeline installs a lightweight
  albumentations stub because InsightFace's mask-rendering import path is not
  used by iPhotron.
- Keep InsightFace limited to local `detection` and `recognition` sessions,
  with downloads disabled as described in the [People runtime](PEOPLE_RECOGNITION_RUNTIME.md).
  People clustering needs only bounding boxes and embeddings. Loading
  landmark/gender-age models in packaged builds has caused asset-level
  failures such as `'NoneType' object has no attribute 'shape'`.

The Windows build script already applies these rules. If writing a manual
Nuitka command, include the equivalent flags:

```bash
--include-package=insightface
--include-package=onnxruntime
# Optional for a fully offline build:
--include-data-dir=src/extension/models=extension/models
--nofollow-import-to=albumentations
--nofollow-import-to=albucore
--nofollow-import-to=pydantic
--nofollow-import-to=pydantic_core
--nofollow-import-to=typing_inspection
```

## Pets Recognition in Release Builds

Pets is a separate optional runtime; including People dependencies alone does
not enable it. Build environments that promise Pets support must install:

```powershell
python -m pip install -e ".[pets-ai]"
```

The standalone bundle must retain `onnxruntime`, `torch`, `torchvision`,
`usearch`, and `certifi`. An offline-ready build must also include the complete
verified model layout:

```text
extension/models/pets/
├── detector/yolox_nano_coco.onnx
└── embedding/dinov2_vits14/
    ├── dinov2_vits14.pt
    └── dinov2_vits14.pt.metadata.json
```

The platform build scripts copy `src/extension/models` to `extension/models`
only when optional assets have been staged. Manual Nuitka builds that enable
Pets should also include the optional runtime explicitly:

```bash
--include-package=onnxruntime
--include-package=torch
--include-package=torchvision
--include-package=usearch
--include-package=certifi
--include-data-dir=src/extension/models=extension/models
```

The current platform build scripts explicitly include the People runtime but do
not yet add `torch`, `torchvision`, or `usearch` flags. Therefore a stock script
build must not be advertised as Pets-enabled merely because the model directory
was copied; add the flags above (or update the script) and perform the Pets smoke
test before release.

If models are intentionally omitted, the official ZIP installs YOLOX and DINOv2
together with the People models. Set `IPHOTO_PET_MODEL_AUTO_DOWNLOAD=0` to suppress
silent installation triggered by the Pets worker. See
[`PETS_RECOGNITION_RUNTIME.md`](PETS_RECOGNITION_RUNTIME.md) for the runtime and
persistence contract.

## Step 1: AOT Compilation

Before packaging with Nuitka, you **must** compile the Numba JIT filters into
a C-extension. This step uses Numba's `pycc` AOT compiler to produce a
platform-specific shared library.

Run the build script:

```bash
python src/iPhoto/core/filters/build_jit.py
```

This will generate a shared object file in `src/iPhoto/core/filters/`:

- Linux: `_jit_compiled.cpython-<version>-<arch>-linux-gnu.so`
- Windows: `_jit_compiled.pyd`
- macOS: `_jit_compiled.cpython-<version>-darwin.so`

### Verify the AOT module

```bash
python -c "from iPhoto.core.filters import _jit_compiled; print('AOT module loaded successfully')"
```

## Step 2: Build with Nuitka

When building with Nuitka, exclude **both** `numba` and `llvmlite` to
completely remove them from the final binary. The application detects the
AOT module at import time and will never attempt to load Numba.

### Recommended Windows build script

Run the maintained script from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_nuitka_windows.ps1 -OutputDir build
```

The script builds `src/entrypoint.py` and writes `entrypoint.dist\entrypoint.exe`
and `nuitka-compilation-report.xml` under the output directory. The default base
package leaves the Maps runtime and People/Pets model weights to extension
installation. Use `-IncludeOptionalAssets` to bundle staged assets for offline
deployments and verify the Python runtimes required by the enabled features.

When `-PythonExe` is omitted, the script selects the first Python 3.12 or newer
interpreter that has Nuitka, PyExifTool, `pillow_heif`, and `_pillow_heif`
installed. It checks the repository `.venv`, the parent directory `.venv`,
`py.exe -3.12`, and finally a real `python.exe` on `PATH`. The Microsoft Store
`WindowsApps\python.exe` execution alias is rejected. To choose the interpreter
explicitly, use:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_nuitka_windows.ps1 `
  -PythonExe "D:\python_code\iPhoto\.venv\Scripts\python.exe" `
  -OutputDir build
```

Windows Explorer's **Run with PowerShell** action is also supported. Relative
output and icon paths are resolved from the repository root, so the default
output remains `<repository>\build` even when Explorer starts PowerShell with a
different working directory. The script also switches the process working
directory to the repository root before probing imports, preventing packages
beside the virtual environment from shadowing `Lib\site-packages`. Running from
an existing PowerShell window is still preferable for troubleshooting because
the console remains open.

The build explicitly freezes the `exiftool` Python package and Pillow-HEIF's
Python/native modules because Nuitka cannot reliably infer these indirect and
lazy imports. The script does not search for, validate, or copy an
`exiftool.exe`; it includes the Python files installed by `PyExifTool`.

The script checks required data paths and language catalog files before
compilation. It puts this checkout's `src` first on `PYTHONPATH` and verifies
the startup profiler's source file and callable `configure` export, including
under Python optimization. Nuitka streams unbuffered progress to the terminal.
The script restores the caller's Python environment after the build attempt.

The maintained base profile already includes `--include-package=OpenGL`,
`--include-package=OpenGL_accelerate`, translation resources, GLSL and QRhi QSB
shaders, and `iPhoto/pets/model_manifest.json`.

With `-IncludeOptionalAssets`, the script performs these map-specific jobs
before invoking Nuitka:

1. it optionally rebuilds the native runtime with
   `tools\osmand_render_helper_native\build_native_widget_msvc.ps1` when
   `-RebuildNativeRuntime` is supplied
2. it copies the required runtime binaries from
   `tools\osmand_render_helper_native\dist-msvc` into
   `src/maps/tiles/extension/bin`
3. it includes `src/maps/tiles` in the standalone bundle so the packaged app
   ships with the extension

The sync step currently requires these files to exist in
`tools\osmand_render_helper_native\dist-msvc`:

- `osmand_render_helper.exe`
- `osmand_native_widget.dll`
- `OsmAndCore_shared.dll`
- `OsmAndCoreTools_shared.dll`

All `*.dll` files in that directory are then copied into
`src/maps/tiles/extension/bin`.

Useful flags:

- `-RebuildNativeRuntime`
  Rebuild the native runtime before packaging.
- `-SkipNativeRuntimeSync`
  Skip the copy into `src/maps/tiles/extension/bin` if you already staged the
  runtime manually.
- `-IncludeOptionalAssets`
  Bundle the staged map extension and recognition models; verify the required AI runtime and complete models before claiming offline support.
- `-ConsoleMode disable|attach|force`
  Select the Windows console mode. `disable` is the default for the packaged GUI.
- `-Jobs <n>`
  Set the parallel build job count.
- `-PythonExe <path>`
  Use a specific Python 3.12+ interpreter with Nuitka installed.
- `-IconPath <path-to.ico>`
  Override the default `docs/picture/logo_new.ico` executable icon.

If you built the runtime in the separate `PySide6-OsmAnd-SDK` checkout, either:

- copy `PySide6-OsmAnd-SDK\tools\osmand_render_helper_native\dist-msvc\*` into
  this repository's `tools\osmand_render_helper_native\dist-msvc\` and let the
  packaging script perform its normal sync, or
- stage `src/maps/tiles/extension/bin` yourself and call
  `scripts\build_nuitka_windows.ps1 -SkipNativeRuntimeSync`

### Recommended Linux standalone build script

For Linux packaging, prefer:

```bash
bash scripts/build_nuitka_fast.sh
```

That script already includes `src/maps/tiles`, so the offline OBF/resources
layout is bundled automatically. The Linux maps runtime is picked up from
`src/maps/tiles/extension/bin`, and the native widget expects Qt's XCB desktop
OpenGL path when it is selected at runtime.

The ignored `src/extension/models` directory is optional. The script adds its
Nuitka data-directory argument only when that directory exists, so a clean
checkout can produce a core standalone build. An offline recognition release
must stage and validate the intended People/Pets artifacts first, then verify
that the resulting `dist/entrypoint.dist/extension/models` tree contains them.

This remains a directory-based standalone build. Nuitka's Linux icon option is
for onefile binaries, so the standalone ELF does not embed an application icon.
Do not switch this build to onefile just to add an icon. AppImage packaging
continues to receive its desktop icon separately through
`scripts/build_appimage.sh --icon <path-to.png>`.
See [`BUILD_APPIMAGE.md`](BUILD_APPIMAGE.md) for the complete AppImage flow and
[`BUILD_FLATPAK.md`](BUILD_FLATPAK.md) for the x86_64 standalone-wrapper
Flatpak flow.

### Recommended macOS runtime sync

Before building a macOS package, build the SDK runtime and sync it into the
iPhotron extension layout:

```bash
QT_ROOT=/opt/homebrew/opt/qt bash ../PySide6-OsmAnd-SDK/tools/osmand_render_helper_native/build_macos.sh
python scripts/sync_macos_map_extension.py --sdk-root ../PySide6-OsmAnd-SDK
bash scripts/build_nuitka_macos.sh
```

The script stages Maps, signs `entrypoint.app`, and records its executable in
`build-manifest.json` under the selected output directory.

The macOS build defaults to `docs/picture/logo_new.ico` and passes it directly
to `--macos-app-icon`. Nuitka converts it to ICNS using `imageio`; install
`imageio` in the selected build interpreter when the preflight reports it
missing. Pass `--icon <path>` to override the default.

The sync script copies `World_basemap_2.obf`, `search/geonames.sqlite3`, OsmAnd
resources, `osmand_render_helper`, `osmand_native_widget.dylib`, recursively
resolved non-system Mach-O dependencies, then patches `install_name`/rpaths and
ad-hoc signs the staged binaries. A manual macOS packaging command must include
`src/maps/tiles` and the QRhi `.qsb` shader files just like the Windows script.

### Maintained Build Profiles

Use the platform scripts for complete flags, QSB assets, translations, optional
model staging, and build-manifest generation. Partial manual Nuitka commands
are not equivalent release procedures. A custom profile must provide the same
required assets and its own truthful build/validation evidence; it must not be
labeled as output from an unchanged maintained script.

The fast profiles use directory-based standalone output and exclude `numba`
and `llvmlite` after AOT. `no_site`, LTO, and compiler selection belong to the
platform script. Keep directory-based output for startup comparisons: onefile
startup adds extraction cost and is a different performance target. Linux
standalone icons are supplied at the AppImage/Flatpak stage.

### Key flags explained

| Flag | Purpose |
|---|---|
| `--nofollow-import-to=numba` | Prevents Nuitka from bundling the `numba` package |
| `--nofollow-import-to=llvmlite` | Prevents Nuitka from bundling the `llvmlite` package (dependency of `numba`) |
| `--nofollow-import-to=pytest` | Prevents Nuitka from bundling `pytest` (only needed for development) |
| `--nofollow-import-to=iPhoto.tests` | Excludes the in-tree test sub-package from the build |
| `--include-package=iPhoto` | Ensures all iPhoto sub-packages (including the AOT `.so`/`.pyd`) are included |
| `--include-package=insightface` | Bundles the InsightFace runtime used by People scanning |
| `--include-package=onnxruntime` | Bundles the ONNX runtime used by InsightFace models |
| `--include-data-dir=src/extension/models=extension/models` | Optional: bundles ignored, explicitly staged People/Pets models for an offline build |
| `--nofollow-import-to=albumentations` and related pydantic packages | Avoids unused InsightFace mask-rendering dependencies that are not needed for People clustering |
| QRhi `.qsb` data files | Required for macOS/Metal and OpenGL QRhi media previews; include `image_viewer_rhi.*`, `image_viewer_overlay.*`, and `video_renderer.*` |

## Step 3: Verify the Distribution

For startup failures or blank playback on Windows, use the
[packaged diagnostic launcher](WINDOWS_NUITKA_TROUBLESHOOTING.md#capture-startup-and-playback-errors).

Verify the distribution on the target OS. The Windows commands below use the
default output directory.

1. Verify the `_jit_compiled` extension in the packaged `iPhoto/core/filters/`
   directory.

   ```bash
   # Linux / macOS
   find dist/ -name "_jit_compiled*"
   # Windows (PowerShell)
   Get-ChildItem -Recurse .\build\entrypoint.dist -Filter "_jit_compiled*"
   ```

2. Check that the distribution omits `numba` and `llvmlite`.

   ```bash
   # Should produce no output
   find dist/ -type d -name "numba"
   find dist/ -type d -name "llvmlite"
   ```

3. With a dedicated test library, launch the packaged application and verify
   photos, videos, Live Photos, and image adjustments in Edit.

4. For a build with bundled Maps, verify the map data and native runtime.

   ```powershell
   Get-ChildItem -Recurse .\build\entrypoint.dist -Filter "World_basemap_2.obf"
   Get-ChildItem -Recurse .\build\entrypoint.dist -Filter "osmand_render_helper.exe"
   Get-ChildItem -Recurse .\build\entrypoint.dist -Filter "osmand_native_widget.dll"
   ```

   ```bash
   find dist/ -name "World_basemap_2.obf"
   find dist/ -name "osmand_render_helper"
   find dist/ -name "osmand_native_widget.so"
   find dist/ -name "osmand_native_widget.dylib"
   ```

5. For a build with bundled Maps, launch the map preview and verify the offline
   search database. Verify the QRhi shaders in every build.

   ```bash
   find dist/ -path "*/maps/tiles/extension/search/geonames.sqlite3"
   find dist/ -name "image_viewer_rhi.frag.qsb"
   find dist/ -name "image_viewer_overlay.frag.qsb"
   find dist/ -name "video_renderer.frag.qsb"
   ```

6. Verify every explicitly staged model in an offline recognition build. A
   clean Linux checkout without `src/extension/models` should produce a build
   without an `extension/models` payload.

   ```powershell
   Get-ChildItem -Recurse .\build\entrypoint.dist -Filter "det_500m.onnx"
   Get-ChildItem -Recurse .\build\entrypoint.dist -Filter "w600k_mbf.onnx"
   Get-ChildItem -Recurse .\build\entrypoint.dist -Filter "yolox_nano_coco.onnx"
   Get-ChildItem -Recurse .\build\entrypoint.dist -Filter "dinov2_vits14.pt"
   ```

7. The People & Pets page can scan a small image folder and create the enabled
   face/pet clusters. Name an identity, set a cover, create a group, and restart
   the packaged app to confirm stable recognition state persists.
   During a diagnostic run, check the app log at
   `%LOCALAPPDATA%\iPhoto\iPhoto.log` for messages such as `Face detection
   failed for ...` or `Face scan failed for asset ...`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Packaged HEIC/HEIF files cannot be opened | The lazy `pillow_heif` import or its `_pillow_heif` native extension was omitted | Use the maintained Nuitka scripts, which explicitly include both modules, and install `pillow-heif` into the selected build interpreter |
| `AOT compiled module not found` in logs | `_jit_compiled` extension missing from distribution | Re-run Step 1 and rebuild; verify the `.so`/`.pyd` file is in `iPhoto/core/filters/` |
| `ImportError` referencing `numba` at runtime | A code path still has an unconditional numba import | All numba imports must use `try/except ImportError` guards |
| Image adjustments produce no visible effect | Kernel not loaded — check logs for error messages | Ensure the AOT module matches the current Python version and platform |
| `Undesirable import of 'pytest'` warning from Nuitka | `iPhoto.tests` sub-package is being compiled into the build | Add `--nofollow-import-to=pytest` and `--nofollow-import-to=iPhoto.tests` to the Nuitka command |
| `The native OsmAnd widget library is not available` | `src/maps/tiles/extension/bin` was not staged correctly, or the platform native runtime is missing | Rebuild the side-project runtime and resync `dist-msvc`/`dist-linux`/`dist-macosx`, or restage `src/maps/tiles/extension/bin` before packaging |
| `OsmAnd helper command not configured` | `osmand_render_helper(.exe)` is missing from the extension `bin/` directory | Ensure the helper exists in the side-project output and is copied into `src/maps/tiles/extension/bin` |
| Linux native maps fail with GLX/XCB startup errors | The session is Wayland-only or missing XWayland/XCB GL integration | Install/enable XWayland and rerun, or set `IPHOTO_PREFER_OSMAND_NATIVE_WIDGET=0` to force the helper-backed Python OBF path |
| macOS Location map tiles are transparent | The legacy map is running through `QOpenGLWidget` inside a transparent top-level window, or the GL surface is not repainting fully | Keep the macOS legacy path on `MapGLWindowWidget`/`QOpenGLWindow`, set `IPHOTO_MAP_GL_DEBUG=1`, and verify full-update repaint behavior |
| Packaged macOS media preview fails before first frame | QRhi shader `.qsb` files were not bundled, or `IPHOTO_RHI_BACKEND` forced an unavailable backend | Include the image/overlay/video `.qsb` files and test both default Metal and `IPHOTO_RHI_BACKEND=opengl` diagnostics |
| `Face scanning paused: name 'Literal' is not defined` | A third-party annotation was evaluated at runtime inside the packaged app | Rebuild with the current People pipeline, which installs runtime typing compatibility before importing InsightFace |
| `Face scanning paused: name 'NDArray' is not defined` | Same runtime annotation issue, usually from numpy typing annotations | Rebuild with the current People pipeline; do not remove the runtime typing compatibility helper |
| `Some assets could not be face scanned and will be retried after a rescan` | Asset-level face detection failed twice; check `%LOCALAPPDATA%\iPhoto\iPhoto.log` for the real traceback | Confirm the packaged build loads the detection and recognition ONNX models, then rescan. Full library rescan resets `retry`/`failed` face statuses |
| Packaged People scan downloads models but never clusters faces | The downloaded model cache is incomplete, unwritable, or an InsightFace dependency failed during scan | Ensure Nuitka includes `insightface` and `onnxruntime`; verify `%LOCALAPPDATA%\iPhoto\extensions\recognition\v1\models` is writable and complete, or rebuild with `-IncludeOptionalAssets`; exclude unused albumentations/pydantic packages and keep InsightFace limited to detection and recognition |
