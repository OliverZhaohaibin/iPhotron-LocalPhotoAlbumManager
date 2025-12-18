# `AGENT.md` – iPhoto Development Principles

## 1. Core Philosophy

*   **Album = Folder**: Any folder can be an album; the structure relies on the filesystem.
*   **Originals are Immutable**: **Never modify photos/videos directly** (no renaming, cropping, or writing EXIF), unless the user explicitly enables "Repair/Organize" mode.
*   **Human Decisions in Manifest**: Cover, featured photos, sort order, and tags are written to sidecar files like `manifest.json`.
*   **Disposable Cache**: Thumbnails, indices, and pairing results are caches. The software must be able to rebuild them automatically if deleted.
*   **Live Photo Pairing**: Priority based on `content.identifier`. Fallback to weak pairing (same name / time proximity). Results are stored in the database.

---

## 2. File & Directory Conventions

*   **Marker Files**
    *   `.iphoto.album.json`: Full manifest (Recommended).
    *   `.iphoto.album`: Minimal marker (Empty file, indicates "This is an album").

*   **Hidden Work Directory** (Rebuildable):
    ```
    /<Album>/.iphoto/
      manifest.json      # Optional manifest location
      featured.json      # Featured UI cards
      manifest.bak/      # History backups
      locks/             # Concurrency locks
    ```

*   **Centralized Storage (v3.00+)**:
    *   **Index**: Moved to a high-performance **SQLite** backend (`iPhoto.db`). No longer uses `index.jsonl`.
    *   **Thumbnails**: Unified thumbnail cache folder. No longer scattered in every album's `.iphoto/thumbs/`.

*   **Original Photos/Videos**
    *   Keep in the album directory. Do not move or rename.
    *   Support HEIC/JPEG/PNG/MOV/MP4, etc.

---

## 3. Data & Schema

*   **Manifest (`album`)**: Authoritative source for user data. Must update `schemas/album.schema.json`.
*   **Index (SQLite)**: Snapshot of assets. Rebuildable via scanning.
*   **Links (SQLite)**: Live Photo pairing cache. Rebuildable.
*   **Featured (`featured.json`)**: Featured photo UI layout (crop box, title, etc.). Optional.

---

## 4. Coding Rules

*   **Fixed Directory Structure**: See `src/iPhoto/…`. Modules: `models/`, `io/`, `core/`, `cache/`, `utils/`.
*   **Data Classes**: Use `dataclass` for definitions (see `models/types.py`).
*   **Error Handling**: Must raise custom errors (see `errors.py`). No bare `Exception`.
*   **File Writing**: Must be atomic (`*.tmp` → `replace()`). Manifests must be backed up to `.iphoto/manifest.bak/` before writing.
*   **Concurrency**: Database access must use proper transactions. File writes must check locks.

---

## 5. AI Code Generation Principles

*   **No Hardcoded Paths**: Always use `Path` join/concatenation.
*   **No Hardcoded JSON**: Must validate with `jsonschema`; provide defaults when necessary.
*   **No Implicit Modification**: Writing EXIF/QuickTime metadata is restricted to `repair.py` and guarded by `write_policy.touch_originals=true`.
*   **Runnable Output**: Generate complete functions/classes, not fragments.
*   **Clear Comments**: Document inputs, outputs, and edge cases.
*   **Cross-Platform**: Must work on Windows/macOS/Linux.
*   **External Dependencies**: Only use dependencies declared in `pyproject.toml`. Use wrappers for ffmpeg/exiftool (`utils/ffmpeg.py`, `utils/exiftool.py`).
*   **Cache Strategy**: Detect and incrementally update indices/thumbnails. Do not overwrite fully.

---

## 6. Module Responsibilities

*   **models/**: Data classes + Manifest loading/saving.
*   **io/**: Filesystem scanning, metadata reading.
*   **core/**: Business logic (Pairing, Sorting, Management, Image Adjustment).
    *   `light_resolver.py`: Light adjustment parameter resolution.
    *   `color_resolver.py`: Color adjustment parameter resolution + Image statistics.
    *   `bw_resolver.py`: Black & White parameter resolution.
    *   `filters/`: High-performance image processing (NumPy Vectorization + Numba JIT + QColor Fallback).
*   **cache/**: SQLite backend interface and locking.
*   **utils/**: General utilities (hash, logging, external tool wrappers).
*   **schemas/**: JSON Schemas.
*   **cli.py**: Typer CLI entry point.
*   **app.py**: High-level Facade coordinating all modules.

---

## 7. Code Style

*   Follow **PEP8**, line width 100.
*   Type hints are mandatory (`Optional[str]`, `list[Path]`, etc.).
*   Function naming: Verb first (`scan_album`, `pair_live`).
*   Class naming: PascalCase (`Album`, `IndexStore`).
*   Exception naming: `XxxError`.

---

## 8. Testing & Robustness

*   All modules must have `pytest` unit tests.
*   Handle missing/corrupt files gracefully without crashing.
*   Auto-rebuild database/indices if missing.
*   Handle sync conflicts according to `conflict.strategy`.

---

## 9. Safety Switches

*   **Default**:
    *   Do not modify originals.
    *   Do not reorganize directories.
    *   Do not write EXIF.
*   **User Explicit Permission**:
    *   Use `exiftool`/`ffmpeg` to write back in `repair.py`.
    *   Must generate `.backup` first.

---

## 10. Minimal Command Set

*   `iphoto init`: Initialize album.
*   `iphoto scan`: Generate/Update index (SQLite).
*   `iphoto pair`: Generate/Update pairing (SQLite).
*   `iphoto cover set`: Set cover.
*   `iphoto feature add/rm`: Manage featured photos.
*   `iphoto report`: Output statistics and anomalies.

---

## 11. Edit System Architecture

### 1. Overview

The edit system provides **Non-Destructive** image adjustments in two modes:
*   **Adjust Mode**: Light / Color / Black & White parameters.
*   **Crop Mode**: Perspective / Rotate / Crop.

### 2. Core Components

#### GUI Layer (`src/iPhoto/gui/ui/widgets/`)
*   `edit_sidebar.py`: Container for Adjust/Crop pages.
*   `edit_light_section.py`: Light panel (7 sliders + Master).
*   `edit_color_section.py`: Color panel (Sat/Vib/Cast).
*   `edit_bw_section.py`: B&W panel (Intensity/Neutrals/Tone/Grain).
*   `edit_perspective_controls.py`: Perspective controls.
*   `thumbnail_strip_slider.py`: Slider with real-time preview.
*   `gl_crop/`: Crop interaction module.

#### Core Layer (`src/iPhoto/core/`)
*   `light_resolver.py`: Maps Master slider to 7 parameters.
*   `color_resolver.py`: Maps Master slider to Color params.
*   `bw_resolver.py`: Maps Master slider to B&W params.
*   `image_filters.py`: Adjustment application entry point.
*   `filters/`: Processing executors.

### 3. Data Flow
User Drag -> `EditSession.set_value` -> Signal `valueChanged` -> Controller -> `GLRenderer.set_uniform` (GPU Preview) -> `EditSession.save` (.ipo file).

### 4. Parameter Ranges
*   Light/Color: [-1.0, 1.0] usually.
*   B&W Intensity: [0.0, 1.0].
*   Crop Perspective: [-1.0, 1.0].
*   Crop Straighten: [-45.0, 45.0] degrees.

### 5. Crop Module Layering
*   `gl_crop/model.py`: State model.
*   `gl_crop/controller.py`: Interaction coordinator.
*   `gl_crop/hit_tester.py`: Hit detection.

### 6. Development Specs
*   **EditSession**: Access point for all parameters. Do not touch `.ipo` directly.
*   **Interaction Signals**: Must emit started/finished signals to pause file monitoring.
*   **Async Thumbnails**: Generate in background threads.

---

## 12. OpenGL Development Guidelines

### 1. Files
*   **Viewer**: `src/iPhoto/gui/ui/widgets/gl_image_viewer/`
*   **Shaders**: `.vert`, `.frag` files in widget dir.
*   **Maps**: `maps/map_widget/map_gl_widget.py`.

### 2. Standards
*   **Version**: OpenGL 3.3 Core Profile.
*   **Qt API**: `QOpenGLFunctions_3_3_Core`.

### 3. Context Management
*   **Widget**: Handles events, lifecycle (`initializeGL`, `paintGL`).
*   **Renderer**: Holds resources (VAO, Texture, Program).
*   **Safety**: Assume context might not be created. Check `_program is None`.
*   **Cleanup**: Must delete resources while Context is current.

### 4. Coordinate Systems & Y-Axis
*   **Logical (Python)**: Top-Left (0,0), Y-Down.
*   **Texture Upload**: No CPU mirroring.
*   **Shader Flip**: `uv.y = 1.0 - uv.y;` in Fragment Shader.
*   **Consistency**: Ensures UI logic (Crop/Pan) matches visual render.

### 5. Crop & Perspective Coordinates
*   **A. Texture Space**: Persistent storage. [0,1]. Unchanged by rotation.
*   **B. Logical Space**: User interaction. Rotated. [0,1].
*   **C. Projected Space**: Black border detection. Aligned with Logical Space (`rotate_steps=0`).
*   **D. Viewport Space**: Screen pixels. Input events only.

**Rules**:
*   Black border check: `rect_inside_quad` in **Projected Space**.
*   Storage: **Texture Space**.

---

## 13. Python Performance Optimization

### 1. Principles
*   **Priority**: NumPy Vectorization > Numba JIT > Pure Python.
*   **In-Place**: Avoid copying large arrays.

### 2. Numba JIT
*   Use `@jit(nopython=True, cache=True)` for pixel loops.
*   Use `inline="always"` for small helper functions.
*   **No** Python objects or Qt API calls inside JIT functions.

### 3. NumPy Vectorization
*   Use for full-image operations (Brightness, Contrast).
*   Use broadcasting and boolean masks.
*   Use `out=` argument for in-place modification.

### 4. Fallback Strategy
1.  Try **NumPy**.
2.  Fallback to **Numba**.
3.  Final fallback to **QColor** (Slow, but safe).

### 5. Benchmarking
*   Always measure time before/after optimization (`time.perf_counter`).
*   Target: < 10ms for 1080p via NumPy.
