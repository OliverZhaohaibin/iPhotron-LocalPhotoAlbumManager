# Building the Executable with Nuitka and AOT

This document outlines the process for building the iPhotron executable, including the mandatory Ahead-Of-Time (AOT) compilation step for Numba filters.

## Overview

The application uses **Numba** for JIT-compiled image processing kernels at
development time. For release builds the kernels are compiled ahead-of-time
(AOT) into a native C-extension so that the heavy `numba` and `llvmlite`
packages can be completely excluded from the final distribution.

All Numba imports across the codebase use **conditional (try/except)** import
guards. When the AOT-compiled extension (`_jit_compiled`) is present the
application loads it directly; when it is absent and Numba is installed it
falls back to runtime JIT; otherwise it uses a pure-NumPy implementation.
This means the executable works correctly without `numba` or `llvmlite`
installed as long as the AOT module has been built.

## Prerequisites

1. Install the development dependencies (Numba is required for the AOT
   compilation step):

   ```bash
   pip install .[dev]
   ```

2. Ensure **Nuitka** is installed:

   ```bash
   pip install nuitka
   ```

## Maps Extension in Release Builds

Besides the AOT-compiled filter module, release builds that include offline
maps rely on a self-contained runtime under `src/maps/tiles/extension/`.

Expected layout:

| Path | Purpose |
|---|---|
| `src/maps/tiles/extension/World_basemap_2.obf` | Default offline OBF dataset |
| `src/maps/tiles/extension/misc/` | OsmAnd miscellaneous resources |
| `src/maps/tiles/extension/poi/` | OsmAnd POI resources |
| `src/maps/tiles/extension/rendering_styles/` | OsmAnd style XML files |
| `src/maps/tiles/extension/routing/` | OsmAnd routing resources |
| `src/maps/tiles/extension/search/geonames.sqlite3` | Offline place search database used by Assign Location |
| `src/maps/tiles/extension/bin/` | Platform-specific helper/native widget binaries plus dependent libraries (`.exe`/`.dll` on Windows, ELF binaries/`.so` on Linux, Mach-O binaries/`.dylib`/frameworks on macOS) |

The upstream source of truth for producing those files is the standalone
[`PySide6-OsmAnd-SDK`](https://github.com/OliverZhaohaibin/PySide6-OsmAnd-SDK)
repository. Build the runtime there first, then sync the outputs into the
iPhotron checkout before packaging.

Recommended Windows command in `PySide6-OsmAnd-SDK`:

```powershell
powershell -ExecutionPolicy Bypass -File tools\osmand_render_helper_native\build_native_widget_msvc.ps1 -BuildType Release
```

Recommended Linux command in `PySide6-OsmAnd-SDK`:

```bash
bash tools/osmand_render_helper_native/build_linux.sh
```

Recommended macOS command in `PySide6-OsmAnd-SDK`:

```bash
QT_ROOT=/opt/homebrew/opt/qt bash tools/osmand_render_helper_native/build_macos.sh
```

The relevant runtime outputs are mirrored under:

- `tools\osmand_render_helper_native\dist-msvc\osmand_render_helper.exe`
- `tools\osmand_render_helper_native\dist-msvc\osmand_native_widget.dll`
- `tools\osmand_render_helper_native\dist-msvc\OsmAndCore_shared.dll`
- `tools\osmand_render_helper_native\dist-msvc\OsmAndCoreTools_shared.dll`
- `tools\osmand_render_helper_native\dist-msvc\Qt6*.dll`
- `tools/osmand_render_helper_native/dist-linux/osmand_render_helper`
- `tools/osmand_render_helper_native/dist-linux/osmand_native_widget.so`
- `tools/osmand_render_helper_native/dist-linux/libOsmAndCore_shared.so`
- `tools/osmand_render_helper_native/dist-linux/libOsmAndCoreTools_shared.so`
- `tools/osmand_render_helper_native/dist-macosx/osmand_render_helper`
- `tools/osmand_render_helper_native/dist-macosx/osmand_native_widget.dylib`
- non-system macOS Mach-O dependencies copied into `src/maps/tiles/extension/bin`

You also need:

- `vendor\osmand\resources\misc`
- `vendor\osmand\resources\poi`
- `vendor\osmand\resources\rendering_styles`
- `vendor\osmand\resources\routing`
- `plugin\data\geonames.sqlite3`
- `src\maps\tiles\World_basemap_2.obf`

For the full end-to-end sync workflow, see [docs/development.md](../development.md).

## Face Recognition in Release Builds

The People feature uses InsightFace for face detection and embeddings. Release
builds must treat this as a small AI runtime rather than as a normal optional
import, because Nuitka's standalone/no-site output will not read packages from
the user's Python environment after packaging.

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

The app may download InsightFace models at runtime when the model cache is
missing, but release builds should still bundle the checked-in model cache when
available. The expected source layout is:

| Path | Purpose |
|---|---|
| `src/extension/models/buffalo_s/` | InsightFace `buffalo_s` ONNX model files |
| `src/extension/models/buffalo_s.zip` | Optional upstream model pack archive |

At runtime the default model root is resolved from the installed package as
`extension/models`. It can be overridden for debugging with:

```powershell
$env:IPHOTO_FACE_MODEL_DIR = "D:\path\to\models"
```

Important packaging rules:

- Include `insightface` and `onnxruntime` in the Nuitka bundle.
- Include `src/extension/models` as `extension/models`.
- Do not rely on installing `insightface` or `onnxruntime` next to an already
  built executable; standalone/no-site builds will not load those packages.
- Exclude `albumentations`, `albucore`, `pydantic`, `pydantic_core`, and
  `typing_inspection`. The People pipeline installs a lightweight
  albumentations stub because InsightFace's mask-rendering import path is not
  used by iPhotron.
- Keep InsightFace limited to `allowed_modules=["detection", "recognition"]`.
  People clustering needs only bounding boxes and embeddings. Loading
  landmark/gender-age models in packaged builds has caused asset-level
  failures such as `'NoneType' object has no attribute 'shape'`.

The Windows build script already applies these rules. If writing a manual
Nuitka command, include the equivalent flags:

```bash
--include-package=insightface
--include-package=onnxruntime
--include-data-dir=src/extension/models=extension/models
--nofollow-import-to=albumentations
--nofollow-import-to=albucore
--nofollow-import-to=pydantic
--nofollow-import-to=pydantic_core
--nofollow-import-to=typing_inspection
```

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

For real Windows release builds, prefer:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_nuitka_windows.ps1 -OutputDir build
```

That script does three important map-extension-specific jobs before invoking
Nuitka:

1. it optionally rebuilds the native runtime with
   `tools\osmand_render_helper_native\build_native_widget_msvc.ps1` when
   `-RebuildNativeRuntime` is supplied
2. it copies the required runtime binaries from
   `tools\osmand_render_helper_native\dist-msvc` into
   `src/maps/tiles/extension/bin`
3. it includes `src/maps/tiles` in the standalone bundle so the packaged app
   ships with the extension
4. it includes the QRhi shader source/QSB files used by the image, overlay, and
   video preview widgets

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
- `-ConsoleMode disable|attach|force`
  Control the Windows console mode.
- `-Jobs <n>`
  Set the parallel build job count.

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

### Recommended macOS runtime sync

Before building a macOS package, build the SDK runtime and sync it into the
iPhotron extension layout:

```bash
QT_ROOT=/opt/homebrew/opt/qt bash ../PySide6-OsmAnd-SDK/tools/osmand_render_helper_native/build_macos.sh
python scripts/sync_macos_map_extension.py --sdk-root ../PySide6-OsmAnd-SDK
```

The sync script copies `World_basemap_2.obf`, `search/geonames.sqlite3`, OsmAnd
resources, `osmand_render_helper`, `osmand_native_widget.dylib`, recursively
resolved non-system Mach-O dependencies, then patches `install_name`/rpaths and
ad-hoc signs the staged binaries. A manual macOS packaging command must include
`src/maps/tiles` and the QRhi `.qsb` shader files just like the Windows script.

Example Nuitka command (adjust paths for your platform):

> **Note:** The entry point `src/iPhoto/gui/main.py` is used as an example.
> Verify and adjust this path to match your project's actual entry point if
> it differs.

```bash
nuitka --standalone \
    --nofollow-import-to=numba \
    --nofollow-import-to=llvmlite \
    --nofollow-import-to=albumentations \
    --nofollow-import-to=albucore \
    --nofollow-import-to=pydantic \
    --nofollow-import-to=pydantic_core \
    --nofollow-import-to=typing_inspection \
    --nofollow-import-to=pytest \
    --nofollow-import-to=iPhoto.tests \
    --include-package=iPhoto \
    --include-package=insightface \
    --include-package=onnxruntime \
    --include-data-dir=src/extension/models=extension/models \
    --output-dir=dist \
    src/iPhoto/gui/main.py
```

### Startup-speed optimized build profile (recommended)

If launch latency is the top priority, prefer a **directory-based standalone build**
instead of onefile packaging. Onefile executables must unpack at process start,
which can dominate cold-start time on slower disks.

```bash
nuitka --standalone \
    --python-flag=no_site \
    --lto=yes \
    --clang \
    --follow-imports \
    --nofollow-import-to=numba \
    --nofollow-import-to=llvmlite \
    --nofollow-import-to=albumentations \
    --nofollow-import-to=albucore \
    --nofollow-import-to=pydantic \
    --nofollow-import-to=pydantic_core \
    --nofollow-import-to=typing_inspection \
    --nofollow-import-to=pytest \
    --nofollow-import-to=iPhoto.tests \
    --include-package=iPhoto \
    --include-package=insightface \
    --include-package=onnxruntime \
    --include-data-dir=src/extension/models=extension/models \
    --assume-yes-for-downloads \
    --output-dir=dist \
    src/iPhoto/gui/main.py
```

Notes:

- `--python-flag=no_site` skips importing `site` at startup, reducing process init overhead.
- `--lto=yes` + `--clang` can improve generated binary performance (build time increases).
- For fastest startup, **do not add `--onefile`**.

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
| `--include-data-dir=src/extension/models=extension/models` | Bundles the shared face model cache |
| `--nofollow-import-to=albumentations` and related pydantic packages | Avoids unused InsightFace mask-rendering dependencies that are not needed for People clustering |
| QRhi `.qsb` data files | Required for macOS/Metal and OpenGL QRhi media previews; include `image_viewer_rhi.*`, `image_viewer_overlay.*`, and `video_renderer.*` |

## Step 3: Verify the Distribution

After building, confirm that:

1. The `_jit_compiled` extension exists inside the packaged
   `iPhoto/core/filters/` directory:

   ```bash
   # Linux / macOS
   find dist/ -name "_jit_compiled*"
   # Windows (PowerShell)
   Get-ChildItem -Recurse dist/ -Filter "_jit_compiled*"
   ```

2. Neither `numba` nor `llvmlite` are present in the distribution:

   ```bash
   # Should produce no output
   find dist/ -type d -name "numba"
   find dist/ -type d -name "llvmlite"
   ```

3. The application starts and image adjustments work correctly.

4. The packaged output includes the maps extension:

   ```powershell
   Get-ChildItem -Recurse dist\ -Filter "World_basemap_2.obf"
   Get-ChildItem -Recurse dist\ -Filter "osmand_render_helper.exe"
   Get-ChildItem -Recurse dist\ -Filter "osmand_native_widget.dll"
   ```

   ```bash
   find dist/ -name "World_basemap_2.obf"
   find dist/ -name "osmand_render_helper"
   find dist/ -name "osmand_native_widget.so"
   find dist/ -name "osmand_native_widget.dylib"
   ```

5. The packaged application can launch the map preview and the main GUI without
   map-runtime errors.

   Also verify the offline search database and QRhi shaders are present:

   ```bash
   find dist/ -path "*/maps/tiles/extension/search/geonames.sqlite3"
   find dist/ -name "image_viewer_rhi.frag.qsb"
   find dist/ -name "image_viewer_overlay.frag.qsb"
   find dist/ -name "video_renderer.frag.qsb"
   ```

6. The packaged output includes the face model cache when it is intended to be
   shipped offline:

   ```powershell
   Get-ChildItem -Recurse dist\ -Filter "det_500m.onnx"
   Get-ChildItem -Recurse dist\ -Filter "w600k_mbf.onnx"
   ```

7. The People page can scan a small image folder and create face clusters.
   Name a person, set a cover, create a group with at least two people, and
   restart the packaged app to confirm the stable People state persists.
   During a diagnostic run, check the app log at
   `%LOCALAPPDATA%\iPhoto\iPhoto.log` for messages such as `Face detection
   failed for ...` or `Face scan failed for asset ...`.

## Windows Installer Notes

The Inno Setup script `tools/v4.50.iss` supports an **optional downloadable map
extension package**. At install time it downloads
`iPhotos-maps-extension-win-msvc-package.zip` and extracts it into
`{app}\maps\tiles`, expecting the archive to contain an `extension\...` root.

That means the archive should unpack to:

- `{app}\maps\tiles\extension\World_basemap_2.obf`
- `{app}\maps\tiles\extension\misc\...`
- `{app}\maps\tiles\extension\poi\...`
- `{app}\maps\tiles\extension\rendering_styles\...`
- `{app}\maps\tiles\extension\routing\...`
- `{app}\maps\tiles\extension\bin\...`

If you regenerate the optional archive for a release, keep the root folder name
as `extension` so the install script lands the files in the correct location.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `AOT compiled module not found` in logs | `_jit_compiled` extension missing from distribution | Re-run Step 1 and rebuild; verify the `.so`/`.pyd` file is in `iPhoto/core/filters/` |
| `ImportError` referencing `numba` at runtime | A code path still has an unconditional numba import | All numba imports must use `try/except ImportError` guards |
| Image adjustments produce no visible effect | Kernel not loaded — check logs for error messages | Ensure the AOT module matches the current Python version and platform |
| `Undesirable import of 'pytest'` warning from Nuitka | `iPhoto.tests` sub-package is being compiled into the build | Add `--nofollow-import-to=pytest` and `--nofollow-import-to=iPhoto.tests` to the Nuitka command |
| `The native OsmAnd widget library is not available` | `src/maps/tiles/extension/bin` was not staged correctly, or the platform native runtime is missing | Rebuild the side-project runtime and resync `dist-msvc`/`dist-linux`/`dist-macosx`, or restage `src/maps/tiles/extension/bin` before packaging |
| `OsmAnd helper command not configured` | `osmand_render_helper(.exe)` is missing from the extension `bin/` directory | Ensure the helper exists in the side-project output and is copied into `src/maps/tiles/extension/bin` |
| Linux native maps fail with GLX/XCB startup errors | The session is Wayland-only or missing XWayland/XCB GL integration | Install/enable XWayland and rerun, or set `IPHOTO_PREFER_OSMAND_NATIVE_WIDGET=0` to force the helper-backed Python OBF path |
| macOS Location map tiles are transparent | The legacy map is running through `QOpenGLWidget` inside a transparent top-level window, or the GL surface is not repainting fully | Keep the macOS legacy path on `MapGLWindowWidget`/`QOpenGLWindow`, set `IPHOTO_MAP_GL_DEBUG=1`, and verify full-update repaint behavior |
| Packaged macOS media preview fails before first frame | QRhi shader `.qsb` files were not bundled, or `IPHOTO_RHI_BACKEND` forced an unavailable backend | Include the image/overlay/video `.qsb` files and test both default Metal and `IPHOTO_RHI_BACKEND=opengl` diagnostics |
| Installer downloads the optional package but the map is still unavailable | The ZIP root does not contain `extension\...` or the expected OBF file is missing | Recreate the archive with the `extension` root and verify `extension\World_basemap_2.obf` exists before publishing |
| `Face scanning paused: name 'Literal' is not defined` | A third-party annotation was evaluated at runtime inside the packaged app | Rebuild with the current People pipeline, which installs runtime typing compatibility before importing InsightFace |
| `Face scanning paused: name 'NDArray' is not defined` | Same runtime annotation issue, usually from numpy typing annotations | Rebuild with the current People pipeline; do not remove the runtime typing compatibility helper |
| `Some assets could not be face scanned and will be retried after a rescan` | Asset-level face detection failed twice; check `%LOCALAPPDATA%\iPhoto\iPhoto.log` for the real traceback | Confirm the packaged build uses `allowed_modules=["detection", "recognition"]`, then rescan. Full library rescan resets `retry`/`failed` face statuses |
| Packaged People scan downloads models but never clusters faces | The model cache is available, but an InsightFace submodel or dependency failed during scan | Ensure Nuitka includes `insightface`, `onnxruntime`, and `extension/models`; exclude unused albumentations/pydantic packages; keep InsightFace limited to detection and recognition |
