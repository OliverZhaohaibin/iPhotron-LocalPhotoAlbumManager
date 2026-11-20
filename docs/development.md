# Development Guide

Environment setup, routine commands, debugging, and contribution conventions for
**iPhotron**. [Architecture](architecture.md) owns cross-module runtime and
persistence contracts; focused documents under `misc/` own subsystem details.
[AGENTS.md](../AGENTS.md) is the task-to-reference index. Keep detailed rules in
one owning reference and link to it from this guide.

## Setup

Use Python 3.12 or newer and Git. External ExifTool and FFmpeg/FFprobe binaries
are needed for the workflows that invoke them; their absence follows the
application's warning/degradation paths. Installing `pyexiftool` installs the
Python wrapper, not the ExifTool executable.

```bash
git clone https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager.git
cd iPhotron-LocalPhotoAlbumManager
python -m venv .venv
```

Activate the environment on macOS/Linux:

```bash
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install the core application and development tools:

```bash
python -m pip install -e ".[dev]"
iphoto-gui
```

[pyproject.toml](../pyproject.toml) is the dependency and tool-configuration
source of truth. `dev` includes pytest, Ruff, Black, mypy, and typing stubs;
it does not install the optional recognition runtimes or the Nuitka build tool.
Install optional runtimes in the same environment when needed:

```bash
python -m pip install -e ".[ai-demo]"  # People: InsightFace and ONNX Runtime
python -m pip install -e ".[pets-ai]"  # Pets: ONNX Runtime, Torch, torchvision, USearch
```

Model files are installed separately from Python runtimes. The Settings action
for the People & Pets extension downloads or imports the combined model ZIP;
missing models may also trigger installation after recognition activation.
The ignored `src/extension/models/` directory is optional local/release staging
input, not content supplied by a fresh clone. See the
[shared installation contract](architecture.md#recognition-extension-installation),
[People runtime](misc/PEOPLE_RECOGNITION_RUNTIME.md), and
[Pets runtime](misc/PETS_RECOGNITION_RUNTIME.md) for lookup, recovery, and status.
Core browsing remains usable when an optional runtime or model is unavailable.

## Architecture And Task References

Use `RuntimeContext`, `LibrarySession`, and application/session surfaces for
library behavior. Headless callers use `create_headless_library_session(root)`.
Business workflows belong in their owning application/session service or bounded
context; GUI code owns presentation and Qt transport. Read the
[layer boundaries](architecture.md#layer-boundaries) before changing composition.
The completed vNext migration is [historical material](finished/refactor/vnext-2026-06/README.md).

| Area | Owning reference |
| --- | --- |
| Runtime, library preparation, and state | [Architecture](architecture.md#runtime-contract) |
| Album naming and compatibility | [Album names](architecture.md#album-names-and-compatibility) |
| Scan UI publishing and recognition activation | [Scan guardrails](misc/SCAN_VISIBLE_PUBLISH_GUARDRAILS.md) |
| Collection SQL and visibility flag migration | [Large-library queries](misc/LARGE_LIBRARY_QUERY_GUARDRAILS.md) |
| Gallery/Filmstrip scrolling and thumbnails | [Gallery guardrails](misc/GALLERY_SCROLL_PIPELINE_GUARDRAILS.md) |
| Move/delete/restore planning and UI | [Move/restore](misc/MOVE_RESTORE_OPTIMISTIC_UI_GUARDRAILS.md), [trash state](misc/TRASH_RESTORE_STATE_GUARDRAILS.md) |
| People models, scanning, and stable state | [People runtime](misc/PEOPLE_RECOGNITION_RUNTIME.md) |
| Pets models, clustering, and stable state | [Pets runtime](misc/PETS_RECOGNITION_RUNTIME.md) |
| Maps SDK build, sync, and source diagnostics | [Maps development](misc/MAP_EXTENSION_DEVELOPMENT.md) |
| Map installation and recovery | [Map installation](misc/MAP_EXTENSION_INSTALLATION.md) |
| Windows graphics support and D3D11 experiment | [Windows QRhi matrix](misc/WINDOWS_D3D11_QRHI_STATUS.md) |
| macOS legacy map composition | [macOS map notes](misc/MACOS_MAP_GL_TRANSPARENCY_NOTES.md) |
| Text, popups, menus, and People pickers | [i18n](misc/I18N_UI_TEXT_GUARDRAILS.md), [popup/menu guardrails](misc/PROJECT_POPUP_GUARDRAILS.md) |

## Internationalization Workflow

Wrap new GUI text with the translation helpers and refresh long-lived widgets
through `retranslate_ui()`. The full rules, locale formatters, and focused tests
are in the [i18n guardrail](misc/I18N_UI_TEXT_GUARDRAILS.md). After text changes:

```bash
bash scripts/i18n_extract.sh
bash scripts/i18n_compile.sh
python tools/check_i18n_strings.py src/iPhoto/gui src/maps
```

The [edit terminology glossary](misc/apple_photos_edit_glossary.md) is the
current terminology reference. Material under
[finished i18n requirements](finished/requirements/i18n/) records the completed
rollout and is historical, not another source of current rules.

## Build And Package

Install Nuitka into the selected build interpreter and the optional runtimes
required by the release. The maintained scripts explicitly include the People
runtime, so a core-only development environment is not sufficient for those
builds. Follow [Building the executable](misc/BUILD_EXE.md) for AOT compilation,
platform preflight, assets, and actual recognition capability checks.

| Target | Maintained entry point / procedure |
| --- | --- |
| Windows base standalone | `scripts/build_nuitka_windows.ps1 -OutputDir build` |
| Windows with staged optional assets | Same script with `-IncludeOptionalAssets` |
| macOS app | `bash scripts/build_nuitka_macos.sh` after Maps SDK sync |
| Linux standalone | `bash scripts/build_nuitka_fast.sh` after AOT and Maps staging |
| Linux AppImage | [AppImage guide](misc/BUILD_APPIMAGE.md) |
| Linux Flatpak | [Flatpak guide](misc/BUILD_FLATPAK.md) |
| Manual Debian package | [Debian recipe](misc/BUILD_DEB.md) |

The [Maps development guide](misc/MAP_EXTENSION_DEVELOPMENT.md) owns SDK build
and sync steps. The runtime can use managed user installations; a source staging
directory is not necessarily the runtime installation target.
A copied model directory does not prove that the frozen app contains its Python
AI runtime. Source tests, package-content checks, and native packaged acceptance
are separate evidence. Keep final standalone payloads consistent with their
build manifests; see the [Flatpak provenance contract](misc/BUILD_FLATPAK.md#build).

## Detail And Startup Diagnostics

Read the [Detail/Edit rendering contract](architecture.md#gallery-to-detailedit-rendering)
and [Detail benchmark runbook](requirements/DETAIL_OPEN_BENCHMARK_RUNBOOK.md)
before changing decoders, caches, rendering, or Edit. They own transaction
stages, decoder names, budgets, packaged benchmark commands, and acceptance.

For startup, the native hierarchy is prepared before `show()` and optional work
follows `firstPainted`. The [runtime contract](architecture.md#runtime-contract)
and [startup runbook](requirements/STARTUP_BENCHMARK_RUNBOOK.md) own lifecycle,
import boundaries, profiler fields, and scheduling requirements.
In a startup profile, `main_window.first_paint` should precede
`desktop_coordinator_runtime.started` and `startup.probe.started`.
Enable local checkpoint logging with:

```bash
IPHOTO_STARTUP_PROFILE=1 iphoto-gui
```

On Windows PowerShell:

```powershell
$env:IPHOTO_STARTUP_PROFILE = "1"
iphoto-gui
```

Default startup JSONL paths:

- Windows: `%LOCALAPPDATA%\iPhoto\logs\startup.jsonl`
- macOS: `~/Library/Logs/iPhoto/logs/startup.jsonl`
- Linux: `${XDG_STATE_HOME:-~/.local/state}/iPhoto/logs/startup.jsonl`

Unset the variable for normal launches; disabled profiling creates no log.
Native lifecycle/backend changes also require the
[startup manual matrix](requirements/STARTUP_MANUAL_VALIDATION_MATRIX.md).
Historical smoke reports do not establish acceptance for a new revision.

## Running Tests

Use temporary libraries/settings or isolated copies, and dedicated libraries for
performance runs. Never clear or mutate a user's production library for tests.
Select focused suites from [verification by change type](architecture.md#verification-by-change-type)
and the owning task reference. For boundary changes:

```bash
python3 tools/check_architecture.py
.venv/bin/python -m pytest tests/architecture -q
```

Common commands with the environment activated:

```bash
python -m pytest
python -m pytest tests/application/test_library_session.py -q
python -m pytest -k "test_scan"
```

The project virtual environment can be addressed explicitly as
`.venv/bin/python` on macOS/Linux or `.venv\Scripts\python.exe` on Windows.
The pytest configuration disables the `pytest-qt` plugin; GUI tests remain in
normal discovery and provide their own Qt fixtures/offscreen setup as needed.
CI runs the architecture checker before the broader test suite.

The Gallery real-Qt benchmark is opt-in:

```bash
IPHOTO_RUN_GALLERY_SCROLL_BENCHMARK=1 .venv/bin/python -m pytest tests/performance/test_gallery_scroll_qt_benchmark.py -q
```

Follow the [Gallery guardrail](misc/GALLERY_SCROLL_PIPELINE_GUARDRAILS.md) for
required Windows/Linux runs and the separate macOS baseline. Timing and native
compositor results from one OS do not replace another platform's evidence.
Report commands actually run, results, skips, and remaining validation gaps.

## Debugging

For Qt plugin discovery diagnostics on macOS/Linux:

```bash
QT_DEBUG_PLUGINS=1 iphoto-gui
```

| Issue | Check |
| --- | --- |
| ExifTool or FFmpeg/FFprobe unavailable | Check the executable and its discovery path; Python wrappers alone do not supply the external tools. Flatpak has its own sandbox contract. |
| Graphics initialization or blank media | Inspect the selected media and map backends using the owning platform guide; the application does not impose one universal OpenGL version/profile. |
| Windows Detail repeatedly falls back from WIC | Run `tests/gui/test_detail_decode_backend.py` on Windows and inspect fixed-width COM ABI declarations. |
| AOT module unavailable | Follow the AOT build and distribution checks in the executable guide. |
| macOS legacy map tiles transparent | Follow the macOS map notes; inspect `MapGLWindowWidget` and opt-in `IPHOTO_MAP_GL_DEBUG=1` output. |
| Packaged preview missing shaders | Verify image, overlay, and video QSB files in the final artifact. |
| Recognition paused or models missing | Follow the People/Pets runtime guide and shared extension recovery contract; preserve pending work and durable user state. |

Confirmed implementation gaps live under
[technical debt](technical-debt/README.md); they do not waive current contracts.

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
| `iphoto-gui` | `iPhoto.entrypoint:main` | Lightweight dispatch and startup preparation before `iPhoto.gui.main.main` |
| `iphoto` | `iPhoto.cli:app` | Typer CLI, using headless `LibrarySession` surfaces |

Important runtime entry classes:

| Class / Function | Module | Description |
|------------------|--------|-------------|
| `RuntimeContext` | `iPhoto.bootstrap.runtime_context` | Process composition root and active library lifecycle |
| `LibrarySession` | `iPhoto.bootstrap.library_session` | Library-scoped assets, state, scans, People, Pets, Maps, edit, thumbnails, and location surfaces |
| `create_headless_library_session()` | `iPhoto.bootstrap.library_session` | CLI/non-GUI session construction |
