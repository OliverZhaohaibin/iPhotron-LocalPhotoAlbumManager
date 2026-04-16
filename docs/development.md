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

### Dev Dependencies

```bash
pip install -e ".[dev]"
```

Includes: `pytest`, `pytest-mock`, `pytest-qt`, `ruff`, `black`, `mypy`, `types-Pillow`, `types-python-dateutil`.

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
