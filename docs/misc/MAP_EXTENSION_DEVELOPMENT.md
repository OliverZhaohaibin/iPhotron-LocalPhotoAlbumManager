# Maps Extension Development

Current SDK build, staging, and source-checkout diagnostics. For end-user
installation and recovery, see [Map extension installation](MAP_EXTENSION_INSTALLATION.md).
For release assembly, see [Building the executable](BUILD_EXE.md).

## What the maps extension is

`src/maps/tiles/extension/` is the source/release staging layout for a
self-contained offline OsmAnd/OBF runtime. Runtime discovery selects a usable
managed user installation or bundled fallback; it is not limited to that source
directory. `IPHOTO_OSMAND_EXTENSION_ROOT` overrides the managed target and must
point directly to the `extension` directory containing `World_basemap_2.obf`,
not to its parent. The staging layout is:

| Path | Purpose |
|------|---------|
| `src/maps/tiles/extension/World_basemap_2.obf` | Default offline OBF map dataset |
| `src/maps/tiles/extension/misc/` | OsmAnd miscellaneous resources |
| `src/maps/tiles/extension/poi/` | OsmAnd POI resources |
| `src/maps/tiles/extension/rendering_styles/` | OsmAnd style XML files; the default is `snowmobile.render.xml` |
| `src/maps/tiles/extension/routing/` | OsmAnd routing resources |
| `src/maps/tiles/extension/search/geonames.sqlite3` | Offline place search database used by Assign Location |
| `src/maps/tiles/extension/bin/` | Platform-specific helper/native widget binaries and dependent libraries (`.exe`/`.dll` on Windows, ELF binaries/`.so` on Linux, Mach-O binaries/`.dylib`/frameworks on macOS) |

The platform Nuitka scripts consume this staged layout when including map
assets. Windows can instead use the smaller base package and install the map
extension through Settings. See the
[installation guide](MAP_EXTENSION_INSTALLATION.md) for managed targets and
the [architecture](../architecture.md#infrastructure) for installer ownership.

## Platform runtime notes

On Linux, the main desktop application can use both the helper-backed OBF renderer and the native
OsmAnd widget. The native widget currently expects Qt's XCB desktop OpenGL path,
but Maps does not choose the desktop platform. When the user, launcher, or
runtime has already selected `QT_QPA_PLATFORM=xcb`, iPhotron supplies defaults
for:

- `QT_OPENGL=desktop`
- `QT_XCB_GL_INTEGRATION=xcb_glx`

It does not force a Wayland session onto XCB. Native maps currently run best on
X11 or XWayland and may fall back to the helper-backed renderer on native
Wayland. A complete selected extension takes priority. Without one, discovery
also searches `PySide6-OsmAnd-SDK/` inside or beside the repository; Linux SDK
build candidates precede incomplete local staging candidates.

The standalone preview, `python src/maps/main.py`, has a different startup
policy: Linux `auto`/`native` selects XCB when no platform is set, and a frozen
preview forces XCB unless `IPHOTO_ALLOW_PACKAGED_LINUX_WAYLAND=1`. Its `python`
and `legacy` modes preserve Qt platform selection. This override belongs to
the preview only; the main desktop application preserves Wayland without it.
Keep these entry points separate in diagnostics and acceptance reports.

On macOS, the legacy Python/OpenGL map path deliberately uses
`QOpenGLWindow + QWidget.createWindowContainer()` instead of `QOpenGLWidget`.
That keeps map tiles opaque inside the app's transparent, frameless main
window. The native OsmAnd widget can also be discovered from the extension
`bin/` directory or from a sibling SDK checkout when a `dist-macosx` runtime is
available.

Media preview widgets use QRhi backend selection rather than a fixed raw-GL
path. `IPHOTO_RHI_BACKEND=auto` selects Metal on macOS when Qt exposes it, and
OpenGL elsewhere. Use `IPHOTO_RHI_BACKEND=opengl` to force the legacy OpenGL
path for diagnostics. On Windows, `IPHOTO_RHI_BACKEND=d3d11` enables the
Detail-only QRhi/HLSL path for A/B validation. GPU Maps in the same main window
are unsupported in that experiment; follow the
[Windows support matrix](WINDOWS_D3D11_QRHI_STATUS.md).

## Upstream sub-project: `PySide6-OsmAnd-SDK`

The source of truth for building the map extension is the standalone upstream
repository:

- [PySide6-OsmAnd-SDK](https://github.com/OliverZhaohaibin/PySide6-OsmAnd-SDK)

Commands and output paths in the next SDK steps are relative to that separate
checkout. Verify them against the SDK revision being built; this repository's
tests do not validate the external checkout or its current build scripts.

That repository exists specifically to build and validate the OsmAnd runtime
outside of the main iPhotron application. It contains:

- vendored `OsmAnd-core`, `OsmAnd-core-legacy`, and `OsmAnd-resources`
- Windows, Linux, and macOS build scripts/output directories for helper and
  native widget runtimes
- the PySide6/OsmAnd preview app used to validate the runtime independently
- a stable place to iterate on Qt6/PySide6 integration without touching the
  entire iPhotron application

In practice:

- `PySide6-OsmAnd-SDK` builds the runtime
- `iPhotron` vendors the produced runtime into `src/maps/tiles/extension/`
- packaged builds then consume the vendored extension from this repository

## Recommended build strategy

For Windows, Linux, and macOS packaging, the recommended path is:

1. build the runtime in `PySide6-OsmAnd-SDK`
2. copy the resulting map data, OsmAnd resources, and native binaries into
   `iPhotron/src/maps/tiles/extension/`
3. verify the runtime from the iPhotron checkout
4. package with Nuitka from the iPhotron checkout

This keeps the OsmAnd-specific toolchain work in the dedicated side project,
while keeping iPhotron releases self-contained.

## Step 1: Clone and prepare the side project

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

## Step 2: Build the native runtime in the side project

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

For macOS, build the helper/widget runtime into `dist-macosx` from the SDK
checkout:

```bash
QT_ROOT=/opt/homebrew/opt/qt bash tools/osmand_render_helper_native/build_macos.sh
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
- `build_macos.sh`
  Produces the macOS helper and `.dylib` widget runtime under `dist-macosx`.

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
| `tools/osmand_render_helper_native/dist-macosx/osmand_render_helper` | macOS helper-backed Python OBF rendering |
| `tools/osmand_render_helper_native/dist-macosx/osmand_native_widget.dylib` | macOS native Qt/OpenGL OsmAnd widget |
| `plugin/data/geonames.sqlite3` | Offline search database for Assign Location |
| `vendor\osmand\resources\...` | Rendering styles and supporting OsmAnd resources |
| `src\maps\tiles\World_basemap_2.obf` | Default demo OBF dataset used by the extension |

## Step 3: Sync the side-project outputs into `iPhotron`

The safest approach is to copy the side-project outputs into
`src/maps/tiles/extension/` so the iPhotron checkout stays self-contained.

Example PowerShell sync:

```powershell
$sdkRoot = "D:\python_code\iPhoto\PySide6-OsmAnd-SDK"
$repoRoot = "D:\python_code\iPhoto\iPhotron-LocalPhotoAlbumManager"
$extensionRoot = Join-Path $repoRoot "src\maps\tiles\extension"
$binRoot = Join-Path $extensionRoot "bin"
$searchRoot = Join-Path $extensionRoot "search"

New-Item -ItemType Directory -Force -Path $extensionRoot, $binRoot, $searchRoot | Out-Null

Copy-Item -LiteralPath (Join-Path $sdkRoot "src\maps\tiles\World_basemap_2.obf") `
  -Destination $extensionRoot -Force
Copy-Item -LiteralPath (Join-Path $sdkRoot "plugin\data\geonames.sqlite3") `
  -Destination $searchRoot -Force

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
search_root="$extension_root/search"

mkdir -p "$extension_root" "$bin_root" "$search_root"
cp -f "$sdk_root/src/maps/tiles/World_basemap_2.obf" "$extension_root/"
cp -f "$sdk_root/plugin/data/geonames.sqlite3" "$search_root/"
for resource_dir in misc poi rendering_styles routing; do
  rm -rf "$extension_root/$resource_dir"
  cp -a "$sdk_root/vendor/osmand/resources/$resource_dir" "$extension_root/"
done
cp -a "$sdk_root/tools/osmand_render_helper_native/dist-linux/." "$bin_root/"
```

Recommended macOS sync:

```bash
python scripts/sync_macos_map_extension.py \
  --sdk-root "$HOME/python-code/PySide6-OsmAnd-SDK"
```

The macOS sync script copies `World_basemap_2.obf`, `search/geonames.sqlite3`,
the OsmAnd resource directories, `osmand_render_helper`,
`osmand_native_widget.dylib`, recursively resolved non-system Mach-O
dependencies, then patches `install_name`/rpaths and ad-hoc signs copied
binaries.

If you are intentionally using the MinGW path instead of MSVC, replace
`dist-msvc` with `dist`. The helper-backed Python renderer only requires the
helper executable plus its dependent DLLs, but the native widget path also
requires a usable widget DLL in the same `bin/` directory.

A complete selected extension takes priority over SDK widget candidates.
Otherwise Linux tries SDK builds before local candidates, while macOS tries
local candidates before SDK `dist-macosx`. Keep the checkout in sync with the
runtime you actually want to exercise.

## Step 4: Verify the runtime from the iPhotron checkout

After syncing the extension, return to the iPhotron repository and verify the
runtime before packaging:

```powershell
cd D:\python_code\iPhoto\iPhotron-LocalPhotoAlbumManager
python -m pip install -e ".[dev]"
python src\maps\main.py --backend auto
python src\maps\main.py --backend python
python src\maps\main.py --backend native
python src\maps\main.py --backend legacy
```

Recommended additional checks:

```powershell
python -m pytest tests\test_maps_main.py tests\test_photo_map_view.py -q
iphoto-gui
```

What to look for:

- `--backend auto` chooses the native widget when it is healthy
- `--backend python` succeeds with the helper-backed OBF renderer
- `--backend native` loads the native widget library without missing runtime errors
- `--backend legacy` still renders the bundled legacy vector tiles
- the GUI Location view starts without falling back unexpectedly
- on Linux, the native path starts under X11/XWayland rather than failing with missing GLX/XCB support
- on macOS, the legacy GL map reports a `MapGLWindowWidget`/`MapGLWindow`
  diagnostic and does not show transparent tile areas

## Development-time overrides

For experimentation you can override the managed extension root or individual
runtime binaries:

| Environment variable | Purpose |
|----------------------|---------|
| `IPHOTO_OSMAND_EXTENSION_ROOT` | Managed target override: absolute path to `extension` itself, with the files in the layout above |
| `IPHOTO_OSMAND_RENDER_HELPER` | Override the helper executable/command |
| `IPHOTO_OSMAND_NATIVE_WIDGET_LIBRARY` | Override the native widget library path |
| `IPHOTO_PREFER_OSMAND_NATIVE_WIDGET` | Set to `0` to force the Python OBF path in auto mode |
| `IPHOTO_DISABLE_OPENGL` | Set to `1` to force CPU/fallback rendering where supported |
| `IPHOTO_MAP_GL_DEBUG` | Set to `1` to print one-shot map GL surface diagnostics |
| `IPHOTO_OSMAND_GL_PARTIAL_UPDATE` | Set to `1` to allow partial updates on platforms that default to full GL repaint |
| `IPHOTO_RHI_BACKEND` | `auto`, `metal`, `opengl`, or Windows-only `d3d11` for media preview QRhi backend selection |
| `IPHOTO_ALLOW_PACKAGED_LINUX_WAYLAND` | Standalone frozen map preview only: set to `1` to preserve platform selection for `auto`/`native`; not a main-app switch |

Example:

```powershell
$env:IPHOTO_OSMAND_EXTENSION_ROOT = "D:\tmp\iphoto-extension\extension"
$env:IPHOTO_OSMAND_RENDER_HELPER = "D:\python_code\iPhoto\PySide6-OsmAnd-SDK\tools\osmand_render_helper_native\dist-msvc\osmand_render_helper.exe"
$env:IPHOTO_OSMAND_NATIVE_WIDGET_LIBRARY = "D:\python_code\iPhoto\PySide6-OsmAnd-SDK\tools\osmand_render_helper_native\dist-msvc\osmand_native_widget.dll"
iphoto-gui
```

Linux example:

```bash
export IPHOTO_OSMAND_EXTENSION_ROOT="$HOME/tmp/iphoto-extension/extension"
export IPHOTO_OSMAND_RENDER_HELPER="$HOME/python-code/PySide6-OsmAnd-SDK/tools/osmand_render_helper_native/dist-linux/osmand_render_helper"
export IPHOTO_OSMAND_NATIVE_WIDGET_LIBRARY="$HOME/python-code/PySide6-OsmAnd-SDK/tools/osmand_render_helper_native/dist-linux/osmand_native_widget.so"
iphoto-gui
```

macOS example:

```bash
export IPHOTO_OSMAND_EXTENSION_ROOT="$HOME/tmp/iphoto-extension/extension"
export IPHOTO_OSMAND_RENDER_HELPER="$HOME/python-code/PySide6-OsmAnd-SDK/tools/osmand_render_helper_native/dist-macosx/osmand_render_helper"
export IPHOTO_OSMAND_NATIVE_WIDGET_LIBRARY="$HOME/python-code/PySide6-OsmAnd-SDK/tools/osmand_render_helper_native/dist-macosx/osmand_native_widget.dylib"
iphoto-gui
```

This is convenient for debugging, but release builds should still copy the
runtime into `src/maps/tiles/extension/` so the repository and packaged app stay
self-contained.

## Linux packaged native-widget guardrails

The Linux source checkout and the Linux Nuitka bundle must be treated as two
separate runtime targets. A map preview that works from `python src/maps/main.py`
does **not** prove that the packaged GUI will survive opening the map section.
The failure mode that triggered this guidance was:

```text
ERROR: Failed to initialize GLEW: GLX 1.2 and up are not supported
```

That error appeared only after entering the map section in a packaged build.
Current startup preserves the selected desktop platform rather than forcing
Linux X11 globally.

When touching Linux map packaging, keep these rules in place:

- Treat packaged/frozen Linux runs as a dedicated validation target, but do not
  force `QT_QPA_PLATFORM=xcb`. Exercise both the existing Wayland selection and
  an explicitly selected XCB session.
- Only when `QT_QPA_PLATFORM=xcb` is already selected, keep `QT_OPENGL=desktop` and
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

The frozen preview's XCB default is separate from these main-app guardrails.
Changing either entry point requires packaged evidence for that entry point;
a working source preview does not establish main-app or frozen-preview parity.

Focused source checks:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_maps_main.py tests/test_photo_map_view.py tests/test_map_sources.py tests/gui/test_main.py -q
```

Use the [packaged installation matrix](../requirements/MAP_EXTENSION_INSTALL_VALIDATION.md)
for release acceptance.
