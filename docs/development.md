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
| `typer` | CLI framework |
| `rich` | Terminal formatting |
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

## Build & Package

### Running the Application

```bash
# Launch the GUI
iphoto-gui

# Or use the CLI
iphoto --help
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
nuitka --standalone \
       --nofollow-import-to=numba \
       --include-package=iPhoto \
       --output-dir=dist \
       src/iPhoto/gui/main.py
```

See [docs/misc/BUILD_EXE.md](misc/BUILD_EXE.md) for detailed troubleshooting.

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

### CLI Debugging

```bash
# Run CLI commands with Python debugger
python -m pdb -m iPhoto.cli scan /path/to/album
```

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
| `iphoto` | `iPhoto.cli:app` | CLI interface |
| `iphoto-gui` | `iPhoto.gui.main:main` | GUI application |
