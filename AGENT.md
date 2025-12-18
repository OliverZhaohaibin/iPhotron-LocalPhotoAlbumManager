# `AGENT.md` – iPhoto Development Principles

## 1. Core Philosophy

*   **Album = Folder**: Any folder can be an album; the structure relies on the filesystem.
*   **Originals are Immutable**: **Never modify photos/videos directly** (no renaming, cropping, or writing EXIF), unless the user explicitly enables "Repair/Organize" mode.
*   **Human Decisions in Manifest**: Cover, featured photos, sort order, and tags are written to sidecar files like `.iphoto.album.json`.
*   **Disposable Cache**: Thumbnails, indices, and pairing results are caches. The software must be able to rebuild them automatically if deleted.
    *   **v3.00 Update**: High-performance metadata and pairing data are now stored in **SQLite**, replacing the legacy `index.jsonl` and `links.json`.
*   **Live Photo Pairing**: Priority based on `content.identifier`. Fallback to weak pairing (same name / time proximity). Results are stored in the SQLite database.

---

## 2. File & Directory Conventions

*   **Marker Files**

    *   `.iphoto.album.json`: Full manifest (Recommended).
    *   `.iphoto.album`: Minimal marker (Empty file, indicates "This is an album").

*   **Hidden Work Directory** (Rebuildable):

    ```
    /<Album>/.iphoto/
      manifest.json      # Optional manifest location
      featured.json      # Featured UI cards (crop box, title, etc.)
      manifest.bak/      # History backups of manifests
      locks/             # Concurrency locks
    ```
    *Note: `index.jsonl` and `links.json` are deprecated in v3.00.*
    *Note: `thumbs/` is deprecated in v3.00 in favor of a central unified cache.*

*   **Centralized Storage (v3.00)**

    *   **Database**: `iPhoto.db` (SQLite) stores all asset metadata and relationship indices.
    *   **Thumbnails**: Unified thumbnail cache directory to reduce disk fragmentation.

*   **Original Photos/Videos**

    *   Keep in the album directory. Do not move or rename.
    *   Support HEIC/JPEG/PNG/MOV/MP4, etc.

---

## 3. Data & Schema

*   **Manifest (`album`)**: Authoritative source for user data. Must update `schemas/album.schema.json`.
*   **Index (SQLite)**: Snapshot of assets. Rebuildable via scanning. Replaces `index.jsonl`.
*   **Links (SQLite)**: Live Photo pairing cache. Rebuildable. Replaces `links.json`.
*   **Featured (`featured.json`)**: Featured photo UI layout (crop box, title, etc.). Optional.

---

## 4. Coding Rules

*   **Fixed Directory Structure**: See `src/iPhoto/…`. Modules: `models/`, `io/`, `core/`, `cache/`, `utils/`.
*   **Data Classes**: Use `dataclass` for definitions (see `models/types.py`).
*   **Error Handling**: Must raise custom errors (see `errors.py`). No bare `Exception`.
*   **File Writing**: Must be atomic (`*.tmp` → `replace()`). Manifests must be backed up to `.iphoto/manifest.bak/` before writing.
*   **Concurrency**: Database access must use proper transactions. File writes must check `.iphoto/locks/`.

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
*   **io/**: Filesystem scanning, metadata reading, thumbnail generation.
*   **core/**: Business logic (Pairing, Sorting, Management, Image Adjustment).
    *   `light_resolver.py`: Light adjustment parameter resolution (Brilliance/Exposure/Highlights/Shadows/Brightness/Contrast/BlackPoint).
    *   `color_resolver.py`: Color adjustment parameter resolution (Saturation/Vibrance/Cast) + Image statistics analysis.
    *   `bw_resolver.py`: Black & White parameter resolution (Intensity/Neutrals/Tone/Grain).
    *   `filters/`: High-performance image processing (NumPy Vectorization + Numba JIT + QColor Fallback).
*   **cache/**: SQLite backend interface and locking mechanisms.
*   **utils/**: General utilities (hash, json, logging, external tool wrappers).
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
*   **Crop Mode**: Perspective Correction / Rotate & Straighten / Crop.

### 2. Core Components

#### GUI Layer (`src/iPhoto/gui/ui/widgets/`)

| Component | Responsibility |
|---|---|
| `edit_sidebar.py` | Sidebar container, manages Adjust/Crop page switching. |
| `edit_light_section.py` | Light panel (7 sub-sliders + Master slider). |
| `edit_color_section.py` | Color panel (Saturation/Vibrance/Cast). |
| `edit_bw_section.py` | Black & White panel (Intensity/Neutrals/Tone/Grain). |
| `edit_perspective_controls.py` | Perspective controls (Vertical/Horizontal/Straighten). |
| `thumbnail_strip_slider.py` | Slider component with real-time thumbnail preview. |
| `gl_crop/` | Crop interaction module (Model/Controller/HitTester/Animator/Strategies). |

#### Core Layer (`src/iPhoto/core/`)

| Module | Responsibility |
|---|---|
| `light_resolver.py` | Maps Master slider to 7 Light parameters. |
| `color_resolver.py` | Maps Master slider to Color parameters + Image color statistics. |
| `bw_resolver.py` | Maps Master slider to B&W parameters (3-anchor Gaussian interpolation). |
| `image_filters.py` | Entry point for applying image adjustments. |
| `filters/` | High-performance image processing executors (Layered Strategy Pattern). |

### 3. Data Flow

```
User Drags Slider
     ↓
EditSession.set_value(key, value)  # State Update
     ↓
valueChanged Signal → Controller
     ↓
GLRenderer.set_uniform(...)  # GPU Real-time Preview
     ↓
EditSession.save() → .ipo File  # Persistence
```

### 4. Parameter Ranges

| Category | Parameter | Range | Default |
|---|---|---|---|
| Light | Brilliance/Exposure/Highlights/Shadows/Brightness/Contrast/BlackPoint | [-1.0, 1.0] | 0.0 |
| Color | Saturation/Vibrance | [-1.0, 1.0] | 0.0 |
| Color | Cast | [0.0, 1.0] | 0.0 |
| B&W | Intensity/Master | [0.0, 1.0] | 0.5 |
| B&W | Neutrals/Tone/Grain | [0.0, 1.0] | 0.0 |
| Crop | Perspective_Vertical/Horizontal | [-1.0, 1.0] | 0.0 |
| Crop | Crop_Straighten | [-45.0, 45.0]° | 0.0 |
| Crop | Crop_CX/CY/W/H | [0.0, 1.0] | 0.5/0.5/1.0/1.0 |

### 5. Crop Module Layering

```
gl_crop/
├── model.py          # State Model (CropSessionModel)
├── controller.py     # Interaction Coordinator (CropInteractionController)
├── hit_tester.py     # Hit Detection (Border/Corner/Inside)
├── animator.py       # Animation Manager (Zoom/Rebound)
├── strategies/       # Interaction Strategies (Drag/Scale)
└── utils.py          # Utilities (CropBoxState/CropHandle)
```

### 6. Development Specs

*   **EditSession Access**: All edit parameters must be read/written via `EditSession`. Do not touch `.ipo` files directly.
*   **Interaction Signals**: Sliders must emit `interactionStarted/Finished` signals to pause file monitoring.
*   **Async Thumbnails**: Thumbnail generation must run in background threads to avoid blocking UI.
*   **Perspective Matrix**: Must use **logical aspect ratio** for calculation (see OpenGL Dev Specs Section 4.5).

---

## 12. OpenGL Development Guidelines

### 1. File Inventory

Files involved in direct OpenGL calls or context management:

*   **Core Image Viewer (Pure GL)**
    *   `src/iPhoto/gui/ui/widgets/gl_image_viewer/`
        *   `widget.py` (Host Widget & Event Handling)
        *   `components.py` (Rendering Components)
        *   `resources.py` (GL Resource Management)
        *   `geometry.py` (Geometry Calculations)
        *   `input_handler.py` (Input Handling)
    *   `src/iPhoto/gui/ui/widgets/gl_renderer.py` (GL Command Wrapper)
    *   `src/iPhoto/gui/ui/widgets/gl_image_viewer.vert` (Vertex Shader)
    *   `src/iPhoto/gui/ui/widgets/gl_image_viewer.frag` (Fragment Shader)
    *   `src/iPhoto/gui/ui/widgets/gl_crop/` (Crop Tool Module)

*   **Map Component (GL Backed)**
    *   `maps/map_widget/map_gl_widget.py` (Inherits `QOpenGLWidget`, mixed with `QPainter`)

---

### 2. GL Version Standards

*   **OpenGL Version**: **3.3 Core Profile**
*   **GLSL Version**: `#version 330 core`
*   **Qt Interface**: Must use `QOpenGLFunctions_3_3_Core`. No Fixed Pipeline.
*   **Surface Format**:
    ```python
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    ```

---

### 3. Context Development Specs

#### ✔ Architecture Separation

*   **Widget Layer (`GLImageViewer`)**
    *   Handles events (Mouse, Keyboard, Wheel, Resize).
    *   Manages Lifecycle (`initializeGL / resizeGL / paintGL`).
    *   Ensures `makeCurrent()` / `doneCurrent()` are called before resource creation/destruction.

*   **Renderer Layer (`GLRenderer`)**
    *   Holds all GL Resources (Program / VAO / Buffer / Texture).
    *   Independent of Qt Widget; responsible only for issuing GL commands.
    *   **Prohibited**: Creating GL resources in `__init__` (Must be done after Context activation).

#### ✔ Resource Lifecycle

*   **Creation**: Inside `initializeGL()` or explicitly via `renderer.initialize()` after `makeCurrent()`.
*   **Destruction**: Must delete Texture/VAO/Program while Context is active. Need explicit `shutdown()` or `destroy_resources()`.
*   **Context Safety**: All GL functions must assume Context might not be created. Check `if self._program is None: return`.

---

### 4. Coordinate Systems & Y-Axis Unification

#### ✔ Principle: Logical Layer uses Top-Left, Renderer Layer Flips in Shader

*   **UI Logical Coordinates (Python)**
    *   Origin: Top-Left `(0, 0)`.
    *   Y-Axis: Down.
    *   All Crop / Pan / Zoom operations operate in this system.
    *   `CropBoxState` stores normalized coordinates (0~1) following this system.

*   **Texture Upload**
    *   `QImage` raw data upload.
    *   **Prohibited**: `mirrored()` on CPU (Avoid extra copy/traversal).

*   **Shader Flip (Unified)**
    ```glsl
    // gl_image_viewer.frag
    uv.y = 1.0 - uv.y;
    ```
    Ensures GPU display matches UI logic, preventing "Inverted / Upside-down / Reverse Drag" issues.

---

### 5. Crop & Perspective: Coordinate Systems

#### Core Definitions

To avoid ambiguity, four coordinate systems are defined:

**A. Texture Space (Persistent Storage)**
*   **Definition**: Raw pixel space of the source file. Unchanged by rotation.
*   **Range**: Normalized `[0, 1]`.
*   **Use Case**: Storage in `.ipo` files (Crop_CX, CY, W, H).

**B. Logical Space (User Interaction)**
*   **Definition**: User-visible space, applied rotation (90° steps).
*   **Use Case**: Python UI interaction (Drag, Resize).
*   **Range**: Normalized `[0, 1]`.

**C. Projected Space (Black Border Detection)**
*   **Definition**: Space after Perspective Transform. Aligned with **Logical Space**.
*   **Crucial Role**: Black border detection.
    *   Use **Logical Aspect Ratio** to build perspective matrix.
    *   Set `rotate_steps=0` (Already aligned).
    *   Check if Crop Box (Logical) is inside Quad (Projected).

**D. Viewport Space (Screen)**
*   **Use Case**: Mouse input events only.

#### Shader Pipeline
Order in Fragment Shader:
Perspective → Crop Test → Rotation → Texture Sampling.

---

## 13. Python Performance Optimization

### 1. General Principles

*   **Priority**: NumPy Vectorization > Numba JIT > Pure Python.
*   **Memory**: Avoid unnecessary copies; prefer in-place operations.
*   **Measurement**: Measure before optimizing.

---

### 2. Numba JIT Specs

#### ✔ Scenarios
*   Pixel-level loops.
*   Complex math / branching logic.

#### ✔ Rules
*   Use `@jit(nopython=True, cache=True)`.
*   **Supported**: Math, NumPy indexing, loops, branches.
*   **Prohibited**: Strings, Python Objects (`dict`, `list`), Qt Objects, I/O.
*   **Inline**: Use `inline="always"` for small helper functions.

---

### 3. NumPy Vectorization Specs

#### ✔ Scenarios
*   Full image adjustments (Brightness, Contrast).
*   Array broadcasting.

#### ✔ Rules
*   **In-Place**: `np.clip(..., out=arr)`, `np.power(..., out=arr)`.
*   **Avoid Copy**: `astype(..., copy=False)`.

---

### 4. Layered Fallback Strategy

```python
def apply_filter(image, params):
    if _try_numpy_path(image, params): return
    if _try_numba_path(image, params): return
    _fallback_qcolor_path(image, params)
```

*   **Layer 1**: NumPy Vectorization (Fastest, ~10ms).
*   **Layer 2**: Numba JIT (Fast, ~50ms).
*   **Layer 3**: QColor Fallback (Slow, Compatibility, ~5000ms).

---

### 5. Benchmarking

Must measure using `time.perf_counter()` before submitting optimizations.
Expected gain: 50x - 500x vs Pure Python.
