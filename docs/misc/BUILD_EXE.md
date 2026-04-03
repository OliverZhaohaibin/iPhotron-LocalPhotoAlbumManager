# Building the Executable with Nuitka and AOT

This document outlines the process for building the iPhoto executable, including the mandatory Ahead-Of-Time (AOT) compilation step for Numba filters.

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

Besides the AOT-compiled filter module, Windows release builds also rely on a
self-contained offline map runtime under `src/maps/tiles/extension/`.

Expected layout:

| Path | Purpose |
|---|---|
| `src/maps/tiles/extension/World_basemap_2.obf` | Default offline OBF dataset |
| `src/maps/tiles/extension/misc/` | OsmAnd miscellaneous resources |
| `src/maps/tiles/extension/poi/` | OsmAnd POI resources |
| `src/maps/tiles/extension/rendering_styles/` | OsmAnd style XML files |
| `src/maps/tiles/extension/routing/` | OsmAnd routing resources |
| `src/maps/tiles/extension/bin/` | Helper EXE, native widget DLL, OsmAnd DLLs, and dependent Qt DLLs |

The upstream source of truth for producing those files is the standalone
[`PySide6-OsmAnd-SDK`](https://github.com/OliverZhaohaibin/PySide6-OsmAnd-SDK)
repository. Build the runtime there first, then sync the outputs into the
iPhotron checkout before packaging.

Recommended Windows command in `PySide6-OsmAnd-SDK`:

```powershell
powershell -ExecutionPolicy Bypass -File tools\osmand_render_helper_native\build_native_widget_msvc.ps1 -BuildType Release
```

That build produces the runtime mirrored under:

- `tools\osmand_render_helper_native\dist-msvc\osmand_render_helper.exe`
- `tools\osmand_render_helper_native\dist-msvc\osmand_native_widget.dll`
- `tools\osmand_render_helper_native\dist-msvc\OsmAndCore_shared.dll`
- `tools\osmand_render_helper_native\dist-msvc\OsmAndCoreTools_shared.dll`
- `tools\osmand_render_helper_native\dist-msvc\Qt6*.dll`

You also need:

- `vendor\osmand\resources\misc`
- `vendor\osmand\resources\poi`
- `vendor\osmand\resources\rendering_styles`
- `vendor\osmand\resources\routing`
- `src\maps\tiles\World_basemap_2.obf`

For the full end-to-end sync workflow, see [docs/development.md](../development.md).

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

Example Nuitka command (adjust paths for your platform):

> **Note:** The entry point `src/iPhoto/gui/main.py` is used as an example.
> Verify and adjust this path to match your project's actual entry point if
> it differs.

```bash
nuitka --standalone \
    --nofollow-import-to=numba \
    --nofollow-import-to=llvmlite \
    --nofollow-import-to=pytest \
    --nofollow-import-to=iPhoto.tests \
    --include-package=iPhoto \
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
    --nofollow-import-to=pytest \
    --nofollow-import-to=iPhoto.tests \
    --include-package=iPhoto \
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

5. The packaged application can launch the map preview and the main GUI without
   map-runtime errors.

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
| `The native OsmAnd widget DLL is not available` | `src/maps/tiles/extension/bin` was not staged correctly | Rebuild the side-project runtime and resync `dist-msvc`, or rerun `scripts\build_nuitka_windows.ps1` without `-SkipNativeRuntimeSync` |
| `OsmAnd helper command not configured` | `osmand_render_helper.exe` is missing from the extension `bin/` directory | Ensure the helper exists in the side-project output and is copied into `src/maps/tiles/extension/bin` |
| Installer downloads the optional package but the map is still unavailable | The ZIP root does not contain `extension\...` or the expected OBF file is missing | Recreate the archive with the `extension` root and verify `extension\World_basemap_2.obf` exists before publishing |
