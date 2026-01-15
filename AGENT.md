# `AGENT.md` – iPhoto Development Basic Principles

## 1. General Philosophy

* **Album = Folder**: Any folder can be an album; no dependency on a database.
* **Originals Immutable**: **Direct modification of photos/videos is prohibited** (renaming, cropping, writing EXIF, etc.), unless the user explicitly enables "Organize/Repair" mode.
* **Human Decisions in Manifest**: Cover, featured, sorting, tags, and other information are written to sidecar files like `manifest.json`.
* **Disposable Cache**: Files like thumbnails, index (`index.jsonl`), pairing results (`links.json`) can be deleted at any time, and the software must be able to automatically rebuild them.
* **Live Photo Pairing**: Based on `content.identifier` strong matching first, weak matching (same name/time proximity) second; results written to `links.json`.

---

## 2. File and Directory Conventions

* **Marker Files**

  * `.iphoto.album.json`: Complete manifest (recommended)
  * `.iphoto.album`: Minimal marker (empty file, indicating "this is an album")

* **Hidden Working Directory** (Deletable):

  ```
  /<LibraryRoot>/.iphoto/
    global_index.db    # Global SQLite database (metadata for the entire library)
    manifest.json      # Optional manifest location
    links.json         # Live pairing and logical groups
    featured.json      # Featured UI cards
    thumbs/            # Thumbnail cache
    manifest.bak/      # History backups
    locks/             # Concurrency locks
  ```

  **Note:** From V3.00, `index.jsonl` has been replaced by `global_index.db`. The global SQLite database stores asset metadata for all albums.

* **Original Photos/Videos**

  * Kept in the album directory, not moved or renamed.
  * Supports HEIC/JPEG/PNG/MOV/MP4 etc.

---

## 3. Data and Schema

* **Manifest (`album`)**: Authoritative data source, must comply with `schemas/album.schema.json`.
* **Global Index (`global_index.db`)**: Global SQLite database storing all asset metadata; can be rebuilt if deleted, but requires re-scanning.
* **Links (`links.json`)**: Live Photo pairing cache; can be rebuilt if deleted.
* **Featured (`featured.json`)**: Featured photo UI layout (crop box, title, etc.), optional.

**V3.00 Architecture Changes:**
- Migrated from scattered `index.jsonl` files to a single global SQLite database.
- Database located at `.iphoto/global_index.db` in the library root.
- Supports cross-album queries and high-performance indexing.
- WAL mode ensures concurrency safety and crash recovery.

---

## 4. Coding Rules

* **Fixed Directory Structure** (See `src/iPhoto/…`, modules divided into `models/`, `io/`, `core/`, `cache/`, `utils/`).
* **Data Classes**: Uniformly defined using `dataclass` (See `models/types.py`).
* **Error Handling**: Must raise custom errors (See `errors.py`), raw `Exception` is prohibited.
* **File Writing**: Must be atomic operations (`*.tmp` → `replace()`), manifest must be backed up to `.iPhoto/manifest.bak/` before writing.
* **Database Operations**:
  * Use `AssetRepository` for all database CRUD operations.
  * Get singleton instance via `get_global_repository(library_root)`.
  * Use transaction context manager `with repo.transaction():` to ensure atomicity.
  * Use idempotent upsert (INSERT OR REPLACE) for write operations.
* **Locks**: Must check `.iPhoto/locks/` before writing `manifest/links` to avoid concurrency conflicts. The database handles concurrency via WAL mode.

---

## 5. AI Code Generation Principles

* **No Hardcoded Paths**: Always join using `Path`.
* **No Hardcoded JSON**: Must use `jsonschema` validation; provide default values when necessary.
* **No Implicit Modification of Originals**: Writing EXIF/QuickTime metadata is only allowed in `repair.py`, and must be controlled by `write_policy.touch_originals=true`.
* **Output Must Be Runnable**: Complete functions/classes, not fragments.
* **Clear Comments**: State inputs, outputs, and boundary conditions.
* **Cross-Platform**: Must run on Windows/macOS/Linux.
* **External Dependencies**: Only call dependencies declared in `pyproject.toml`. When involving ffmpeg/exiftool, must use wrappers (`utils/ffmpeg.py`, `utils/exiftool.py`).
* **Caching Strategy**:
  * Global database uses idempotent upsert operations (INSERT OR REPLACE).
  * Incremental scanning: Only process new/modified files.
  * Database automatically handles deduplication and updates.
  * Thumbnails and pairing information also support incremental updates.

---

## 6. Module Responsibilities

* **models/**: Data classes + loading and saving of manifest/links.
* **io/**: File system scanning, metadata reading, thumbnail generation, sidecar writing.
* **core/**: Algorithmic logic (pairing, sorting, featured management, image adjustment).
  * `light_resolver.py`: Light adjustment parameter resolution (Brilliance/Exposure/Highlights/Shadows/Brightness/Contrast/BlackPoint)
  * `color_resolver.py`: Color adjustment parameter resolution (Saturation/Vibrance/Cast) + Image statistical analysis
  * `bw_resolver.py`: Black & White parameter resolution (Intensity/Neutrals/Tone/Grain)
  * `filters/`: High-performance image processing (NumPy Vectorization + Numba JIT + QColor Fallback Strategy)
* **cache/**: Global SQLite database management and lock implementation.
  * `index_store/engine.py`: Database connection and transaction management
  * `index_store/migrations.py`: Schema evolution and version management
  * `index_store/recovery.py`: Database auto-repair and recovery
  * `index_store/queries.py`: Parameterized SQL query construction
  * `index_store/repository.py`: High-level CRUD API
  * `lock.py`: File-level lock implementation
* **utils/**: General tools (hash, json, logging, external tool wrappers).
* **schemas/**: JSON Schema.
* **cli.py**: Typer command-line entry point.
* **app.py**: High-level facade, coordinating modules.

---

## 7. Code Style

* Follow **PEP8**, line width 100.
* Type hints must be complete (`Optional[str]`, `list[Path]`, etc.).
* Function naming: Start with a verb (`scan_album`, `pair_live`).
* Class naming: Capitalized (`Album`, `IndexStore`).
* Exception naming: `XxxError`.

---

## 8. Testing and Robustness

* All modules must have `pytest` unit tests.
* Must handle missing/corrupted input files without crashing.
* `index.jsonl`, `links.json` must be automatically rebuilt if they do not exist.
* Multi-device synchronization conflicts handled according to `conflict.strategy` in manifest.

---

## 9. Safety Switches

* Default:

  * Do not modify originals
  * Do not organize directories
  * Do not write EXIF
* When explicitly allowed by user:

  * Use `exiftool`/`ffmpeg` to write back in `repair.py`
  * Must generate `.backup` first

---

## 10. Minimal Command Set

* `iphoto init`: Initialize album
* `iphoto scan`: Generate/Update index
* `iphoto pair`: Generate/Update pairing
* `iphoto cover set`: Set cover
* `iphoto feature add/rm`: Manage featured
* `iphoto report`: Output album statistics and anomalies

---

## 11. Edit System Architecture

### 1. Overview

The editing system provides **non-destructive** image adjustment functions, divided into two main modes:

* **Adjust Mode**: Light / Color / Black & White parameter adjustments
* **Crop Mode**: Perspective correction / Rotation straightening / Crop box adjustment

### 2. Core Components

#### GUI Layer (`src/iPhoto/gui/ui/widgets/`)

| Component | Responsibility |
|-----------|----------------|
| `edit_sidebar.py` | Edit sidebar container, manages Adjust/Crop page switching |
| `edit_light_section.py` | Light adjustment panel (7 sub-sliders + Master slider) |
| `edit_color_section.py` | Color adjustment panel (Saturation/Vibrance/Cast) |
| `edit_bw_section.py` | Black & White adjustment panel (Intensity/Neutrals/Tone/Grain) |
| `edit_perspective_controls.py` | Perspective correction controls (Vertical/Horizontal/Straighten) |
| `thumbnail_strip_slider.py` | Slider component with live thumbnail preview |
| `gl_crop/` | Crop interaction module (Model/Controller/HitTester/Animator/Strategies) |

#### Core Layer (`src/iPhoto/core/`)

| Module | Responsibility |
|--------|----------------|
| `light_resolver.py` | Algorithm mapping Master slider → 7 Light parameters |
| `color_resolver.py` | Master slider → Color parameter mapping + Image color statistical analysis |
| `bw_resolver.py` | Master slider → B&W parameter mapping (Three-anchor Gaussian interpolation) |
| `image_filters.py` | Image adjustment application entry point |
| `filters/` | High-performance image processing executor (Layered Strategy Pattern) |

### 3. Data Flow

```
User drags slider
     ↓
EditSession.set_value(key, value)  # State update
     ↓
valueChanged Signal → Controller
     ↓
GLRenderer.set_uniform(...)  # GPU real-time preview
     ↓
EditSession.save() → .ipo file  # Persistence
```

### 4. Parameter Range Convention

| Category | Parameter | Range | Default |
|----------|-----------|-------|---------|
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
├── hit_tester.py     # Hit Testing (Borders/Corners/Interior)
├── animator.py       # Animation Management (Zoom/Rebound)
├── strategies/       # Interaction Strategies (Drag/Zoom)
└── utils.py          # Utility Functions (CropBoxState/CropHandle)
```

### 6. Development Standards

* **All edit parameters must be read/written via `EditSession`**, direct manipulation of `.ipo` files is prohibited.
* **Slider interactions must emit `interactionStarted/Finished` signals** to pause file monitoring.
* **Thumbnail generation must be performed in background threads** to avoid blocking the UI.
* **Perspective transformation matrix calculation uses logical aspect ratio**, see OpenGL Development Guidelines Section 12, Subsection 5.

---

## 12. OpenGL Development Guidelines

### 1. Involved Files List

Currently, files in the project involving direct OpenGL calls or GL context management are as follows:

* **Core Image Viewer (Pure GL)**

  * `src/iPhoto/gui/ui/widgets/gl_image_viewer/` (GL Image Viewer Module Directory)
    * `widget.py` (Widget Host and Event Handling)
    * `components.py` (GL Rendering Components)
    * `resources.py` (GL Resource Management)
    * `geometry.py` (Geometry Calculations)
    * `input_handler.py` (Input Event Handling)
  * `src/iPhoto/gui/ui/widgets/gl_renderer.py` (GL Rendering Command Encapsulation)
  * `src/iPhoto/gui/ui/widgets/gl_image_viewer.vert` (Vertex Shader)
  * `src/iPhoto/gui/ui/widgets/gl_image_viewer.frag` (Fragment Shader)
  * `src/iPhoto/gui/ui/widgets/gl_crop/` (Crop Tool Module Directory)

* **Map Component (GL Backed)**

  * `maps/map_widget/map_gl_widget.py` (Inherits from `QOpenGLWidget`, but mainly uses `QPainter` hybrid drawing)

---

### 2. GL Version Standard

* **OpenGL Version**: **3.3 Core Profile**
* **GLSL Version**: `#version 330 core`
* **Qt Interface**: Must use `QOpenGLFunctions_3_3_Core` for API calls, fixed pipeline commands are prohibited.
* **Surface Format**

```python
fmt = QSurfaceFormat()
fmt.setVersion(3, 3)
fmt.setProfile(QSurfaceFormat.CoreProfile)
```

---

### 3. Context Development Guidelines

#### ✔ Architectural Separation

* **Widget Layer (`GLImageViewer`)**

  * Responsible for event handling (Mouse, Keyboard, Wheel, Resize).
  * Manages lifecycle (`initializeGL / resizeGL / paintGL`).
  * Ensures `makeCurrent()` / `doneCurrent()` are called before resource creation/destruction.

* **Renderer Layer (`GLRenderer`)**

  * Holds all GL resources (Program / VAO / Buffer / Texture).
  * Does not depend on Qt Widget, only responsible for "issuing GL commands".
  * Creating GL resources in the constructor is prohibited (must be done after Context activation).

#### ✔ Resource Lifecycle

* **Creation**

  * Must be executed within `initializeGL()`.
  * Or explicitly called via `renderer.initialize()` by the Widget after `makeCurrent()`.

* **Destruction**

  * Textures/VAO/program must be deleted while the Context is active (Python GC is unreliable).
  * An explicit `shutdown()` or `destroy_resources()` method is required.

* **Context Safety**

  * All functions involving GL must "assume that the Context might not be created yet".
  * If Context does not exist: skip drawing and print warning (do not crash).

* **Defensive Programming**

  * Check if resources are initialized before every rendering entry:
    `if self._program is None: return`

---

### 4. Coordinate System and Y-Axis Unification

#### ✔ Principle: **Use Top-Left for Logical Layer, Unify Flip in Shader for Rendering Layer**

* **UI Logical Coordinate System (Python Side)**

  * Origin is Top-Left `(0, 0)`.
  * Y-axis points downwards.
  * All Crop / Pan / Zoom operations run in this coordinate system.
  * `CropBoxState` stores normalized coordinates (0~1) also following this system.

* **Texture Upload**

  * `QImage` raw data is uploaded directly.
  * **`mirrored()` on CPU is prohibited** (avoid extra traversal & copy).

* **Flip Handling in Shader (Unified)**

```glsl
// gl_image_viewer.frag
uv.y = 1.0 - uv.y;
```

This ensures the GPU display direction is consistent with the UI logical coordinates, avoiding issues like "inverted / upside down / reverse dragging" caused by Qt / OpenGL Y-axis differences.


**File Location**: `src/iPhoto/gui/ui/widgets/gl_image_viewer.frag`

---


### 5. Crop & Perspective: Coordinate Systems

#### Core Definition: Coordinate Systems

To eliminate ambiguity, the following four coordinate systems and their roles in calculation must be clearly defined:

#### A. Texture Space — **Persistent Storage Space**

* **Definition**: The original pixel space of the image file, an invariant coordinate system.
* **Range**: Normalized coordinates `[0, 1]`, covering the entire source image.
* **Role**:
  * Data Persistence: All crop parameters (`Crop_CX`, `Crop_CY`, `Crop_W`, `Crop_H`) are stored in `.ipo` files using texture coordinates.
  * GPU Texture Sampling: Shader ultimately samples pixels from texture coordinates.
  * Unaffected by Rotation: Even if the user rotates the image, stored texture coordinates remain unchanged.

#### B. Logical Space — **User Interaction Space**

* **Definition**: The coordinate system seen by the user on screen, with rotation (multiples of 90°) applied.
* **Form**: All crop interactions in the Python layer occur in this space.
* **Role**:
  * UI Interaction: All user drag, crop box adjustment operations are in logical space.
  * Perspective Transformation: Perspective distortion (`vertical`, `horizontal`, `straighten`) is applied based on the logical view.
  * **Black Border Detection**: Performed in logical space (or the projected space aligned with it).
* **Coordinate Range**: Normalized to `[0, 1]` interval.
* **Relationship with Texture Space**: Converted via `texture_crop_to_logical()` and `logical_crop_to_texture()` (swapping width/height, rotating coordinates).

#### C. Projected Space — **Black Border Detection Space**

* **Definition**: The space after applying perspective transformation (Perspective/Straighten). This space aligns with **Logical Space** (i.e., based on rotated aspect ratio).
* **Form**: The original rectangle boundary becomes a convex quadrilateral `Q_valid`.
* **Key Role**: **Core space for Black Border Detection**
  * Use **Logical Aspect Ratio** to calculate the perspective matrix.
  * Set `rotate_steps=0` to keep consistency with logical space (i.e., no 90° rotation step included, as it's already aligned to logical direction).
  * The crop box (in logical space) must be completely contained within this quadrilateral to avoid black borders.
* **Implementation Code**:
  ```python
  # Calculate quad in Logical Space (rotate_steps=0, but using logical aspect ratio)
  matrix = build_perspective_matrix(
      new_vertical,
      new_horizontal,
      image_aspect_ratio=logical_aspect_ratio, # Rotated aspect ratio
      straighten_degrees=new_straighten,
      rotate_steps=0,  # Do not apply 90° steps here, we are already in logical frame
      flip_horizontal=new_flip,
  )
  self._perspective_quad = compute_projected_quad(matrix)
  ```

#### D. Viewport/Screen Space

* **Definition**: The final pixel coordinates rendered on the screen component.
* **Role**: **Only used** for handling mouse click, drag, and other interaction events.

---

#### Shader Coordinate Transformation Pipeline (Fragment Shader)

**Architecture**: Fragment Shader receives crop parameters in Logical Space and applies inverse transformation before sampling the texture.

**File**: `src/iPhoto/gui/ui/widgets/gl_image_viewer.frag`

```glsl
void main() {
    // ... viewport to texture coordinate conversion ...
    
    // 1. Y-Axis Flip
    uv.y = 1.0 - uv.y;
    vec2 uv_corrected = uv; // Logical/Screen Space

    // 2. Crop Test
    // Perform crop test in Logical/Screen space.
    // The crop box is defined in Logical Space.

    if (uv_corrected.x < crop_min_x || ... ) {
        discard;
    }

    // 3. Apply Inverse Perspective
    // Maps Logical Space -> Projected Space (Unrotated relative to logical view)
    vec2 uv_perspective = apply_inverse_perspective(uv_corrected);

    // 4. Perspective Boundary Check (Check against valid texture area in Projected Space)
    if (uv_perspective.x < 0.0 || ... ) {
        discard;
    }
    
    // 5. Apply 90° Rotation (Apply discrete rotation steps)
    // Maps Projected Space -> Texture Space
    vec2 uv_tex = apply_rotation_90(uv_perspective, uRotate90);

    // 6. Texture Sampling
    vec4 texel = texture(uTex, uv_tex);

    // 7. Color Adjustments
    vec3 c = texel.rgb;
    // Example: apply gamma correction
    c = pow(c, vec3(1.0 / 2.2));
    // Other color adjustments can be added here (exposure, saturation, etc.)
    // 8. Output Final Color
    FragColor = vec4(c, texel.a);
}
```

**Key Design Decisions**:
* **Logical Alignment**: The perspective matrix (`uPerspectiveMatrix`) is built based on logical aspect ratio, so the result of `apply_inverse_perspective` (`uv_perspective`) is still in a coordinate system aligned with logical space (only perspective distortion removed).
* **Rotation Separation**: Discrete 90° rotation (`uRotate90`) is applied as the last step independently, mapping coordinates back to physical texture space.
* **Python Layer**: When calculating Black Border Detection Quad in Python layer, `rotate_steps=0` and logical aspect ratio are also used, ensuring the generated Quad can be directly compared with the crop box in logical space.

---

#### Black Border Prevention Mechanism

**Core Principle**: Black border detection is performed in **Logical Space**.

1.  **Build Logical Perspective Quadrilateral**:
    *   Use **Logical Aspect Ratio**.
    *   Force `rotate_steps=0` (since logical space is already the rotated baseline).
    *   The resulting quadrilateral `Q_valid` represents the valid image area in the logical view.
   
    ```python
    # src/iPhoto/gui/ui/widgets/gl_crop/model.py
    matrix = build_perspective_matrix(
        ...,
        image_aspect_ratio=logical_aspect_ratio,
        rotate_steps=0,
        ...
    )
    self._perspective_quad = compute_projected_quad(matrix)
    ```

2.  **Inclusion Check**:
    *   Directly check if the crop box `rect` in logical space is inside `Q_valid`.
    *   No coordinate conversion needed as both are in logical space.
    *   Code: `rect_inside_quad(rect, quad)`

3.  **Automatic Scaling**:
    *   When the crop box exceeds the valid area, calculate the minimum scaling factor based on geometric inclusion relationship.

---

#### Development Guidelines

1.  **Coordinate System Consistency Principle**
    *   **Interaction & Validation**: Always in **Logical Space**.
    *   **Storage**: Always in **Texture Space** (`.ipo` file).
    *   **Rendering**: Shader responsible for final Logical -> Texture mapping.

2.  **Aspect Ratio Usage Standard**
    *   When calculating perspective matrix (`build_perspective_matrix`), must use aspect ratio matching the current space.
    *   If calculating in logical space, must use `logical_aspect_ratio` (which is `tex_h/tex_w` when rotated 90°/270°).

**Key Points**:
* **Texture Space**: Persistent storage, unaffected by rotation.
* **Logical Space**: User interaction space, used by Python layer.
* **Projected Space**: Core of black border detection, `rotate_steps=0` when calculating quadrilateral.
* **Shader Pipeline**: Perspective → Crop Test → Rotation → Sampling (Order immutable).
* Mixing coordinate systems will lead to black borders, crop errors, and accumulated coordinate errors.

3.  **Rotation Handling**
    *   Do not manually rotate the crop box in Python layer to match texture space for validation (error-prone).
    *   Instead, build a "Perspective Quadrilateral in Logical Space" for same-space comparison.

---


## 13. Python Performance Optimization Guidelines

### 1. General Principles

* **Performance Priority**: Image processing, array operations, and pixel-level operations that are called frequently must be optimized to the extreme.
* **Priority Order**: NumPy Vectorization > Numba JIT > Pure Python Loops.
* **Memory Efficiency**: Avoid unnecessary copying, modify in-place whenever possible.
* **Measure First**: Optimization must be preceded by measurement to avoid premature optimization.

---

### 2. Numba JIT Acceleration Guidelines

#### ✔ Applicable Scenarios

* **Pixel-level Loops**: Pixel-by-pixel image processing (e.g., color grading, filters).
* **Complex Math Operations**: Branch logic or recursion that cannot be vectorized.
* **Small Dataset Intensive Calculation**: Scenarios with small data volume but intensive calculation.

#### ✔ Usage Guidelines

**Scenarios MUST use `@jit` decoration:**

```python
from numba import jit

@jit(nopython=True, cache=True)
def process_pixels(buffer: np.ndarray, width: int, height: int) -> None:
    """Pixel-level processing loop accelerated by Numba.
    
    - nopython=True: Force pure JIT mode, no fallback to Python
    - cache=True: Cache compilation results to speed up subsequent startups
    """
    for y in range(height):
        for x in range(width):
            # Pixel-level operations
            pixel_offset = y * width * 4 + x * 4
            buffer[pixel_offset] = process_channel(buffer[pixel_offset])
```

**Supported Numba Features:**

* ✅ Numerical operations (add, subtract, multiply, divide, exp, log, trig functions)
* ✅ NumPy array indexing and slicing
* ✅ `for` loops, `while` loops
* ✅ Conditional branches (`if/elif/else`)
* ✅ Math functions (`math.sin`, `math.exp`, `math.log`, etc.)
* ✅ Tuple return (`return (r, g, b)`)

**Unsupported Features (Must Avoid):**

* ❌ String operations
* ❌ Python objects (`dict`, `list`, custom classes)
* ❌ File I/O
* ❌ Qt objects (`QImage.pixelColor()`, etc.)

#### ✔ Inline Small Functions

For frequently called helper functions, use `inline="always"` to force inlining:

```python
@jit(nopython=True, inline="always")
def clamp(value: float, min_val: float, max_val: float) -> float:
    """Small math helper functions must be inlined to eliminate function call overhead."""
    if value < min_val:
        return min_val
    if value > max_val:
        return max_val
    return value
```

#### ✔ Practical Case Reference

See implementation in the project:

* `src/iPhoto/core/filters/algorithms.py`: Core algorithms (Pure Numba, no dependencies)
* `src/iPhoto/core/filters/jit_executor.py`: Image processing executor accelerated by JIT

---

### 3. NumPy Vectorization Guidelines

#### ✔ Applicable Scenarios

* **Full Image Operations**: Brightness, contrast, color adjustments for the entire image.
* **Array Operations**: Operations that can be expressed using broadcasting.
* **Parallelism**: NumPy automatically utilizes SIMD instructions and multi-core.

#### ✔ Usage Guidelines

**Scenarios MUST use NumPy Vectorization:**

```python
import numpy as np

# ❌ Wrong: Pixel-by-pixel loop (Pure Python)
for y in range(height):
    for x in range(width):
        rgb[y, x] = rgb[y, x] * brightness

# ✅ Correct: Vectorized operation (Automatic parallelism)
rgb = rgb * brightness
```

**Common Vectorized Operations:**

```python
# 1. Channel Normalization
rgb = rgb.astype(np.float32) / 255.0

# 2. Color Space Conversion (RGB → Grayscale)
luma = rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722

# 3. Gamma Correction (Vectorized power operation)
rgb = np.power(np.clip(rgb, 0.0, 1.0), gamma)

# 4. Conditional Selection and Blending
mask = luma > 0.5
rgb[mask] = rgb[mask] * 1.2  # Process only highlights

# 5. Broadcasting (Avoid loops)
rgb = rgb * gain[None, None, :]  # gain: [r_gain, g_gain, b_gain]
```

#### ✔ Memory Optimization

**In-place Modification:**

```python
# ❌ Wrong: Create new array (Waste memory)
rgb = np.clip(rgb, 0.0, 1.0)

# ✅ Correct: In-place modification (Save memory)
np.clip(rgb, 0.0, 1.0, out=rgb)

# ✅ Reuse array to avoid allocation
np.power(rgb, gamma, out=rgb)
```

**Avoid Unnecessary Copying:**

```python
# ❌ Wrong: Trigger copy
rgb_copy = rgb.astype(np.float32)
rgb_copy = rgb_copy / 255.0

# ✅ Correct: Reuse type conversion
rgb = rgb.astype(np.float32, copy=False) / 255.0
```

#### ✔ Practical Case Reference

See implementation in the project:

* `src/iPhoto/core/filters/numpy_executor.py`: NumPy vectorized implementation of Black & White effect

---

### 4. Performance Layering Strategy

The project adopts a **three-layer fallback strategy** to ensure maximum compatibility while pursuing extreme performance:

```
┌─────────────────────────────────────────┐
│  1. NumPy Vectorization (Fastest)       │  ← Priority
│     Operate directly on entire array,   │
│     SIMD acceleration                   │
├─────────────────────────────────────────┤
│  2. Numba JIT (Second Fastest)          │  ← Fallback
│     Compile to machine code, suitable   │
│     for complex logic                   │
├─────────────────────────────────────────┤
│  3. QColor Pixel-wise (Slowest, Most    │  ← Final Fallback
│     Compatible)                         │
│     Pure Python + Qt API, guarantees    │
│     execution                           │
└─────────────────────────────────────────┘
```

**Code Pattern:**

```python
def apply_filter(image: QImage, params) -> None:
    """Apply filter (Automatically select optimal path)."""
    
    # 1️⃣ Try NumPy Vectorization
    if _try_numpy_path(image, params):
        return
    
    # 2️⃣ Fallback to Numba JIT
    if _try_numba_path(image, params):
        return
    
    # 3️⃣ Final Fallback to QColor Pixel-wise
    _fallback_qcolor_path(image, params)
```

---

### 5. Code Example Comparison

#### ❌ Negative Example: Pure Python Loop

```python
def adjust_brightness_bad(image: QImage, brightness: float) -> None:
    """Poor performance: Pixel-wise Qt API calls, cannot be optimized."""
    width = image.width()
    height = image.height()
    for y in range(height):
        for x in range(width):
            color = image.pixelColor(x, y)  # Python overhead per call
            r = min(1.0, color.redF() + brightness)
            g = min(1.0, color.greenF() + brightness)
            b = min(1.0, color.blueF() + brightness)
            image.setPixelColor(x, y, QColor.fromRgbF(r, g, b))
```

**Problem:**
* Two Python ↔ C++ boundary crossings per pixel (`pixelColor()` + `setPixelColor()`)
* Cannot be optimized by compiler
* 1920x1080 image requires ~4 million function calls

---

#### ✅ Positive Example 1: NumPy Vectorization

```python
def adjust_brightness_numpy(image: QImage, brightness: float) -> bool:
    """Best performance: Vectorized operation, no loops."""
    try:
        # Get pixel buffer
        buffer = np.frombuffer(image.bits(), dtype=np.uint8)
        pixels = buffer.reshape((image.height(), image.bytesPerLine()))
        rgb = pixels[:, :image.width() * 4].reshape((image.height(), image.width(), 4))
        
        # Vectorized adjustment (Single operation for full image)
        rgb[:, :, :3] = np.clip(
            rgb[:, :, :3].astype(np.float32) + brightness * 255.0,
            0, 255
        ).astype(np.uint8)
        return True
    except Exception:
        return False  # Fallback to Numba or QColor
```

**Advantage:**
* Single operation processes full image (~10ms for 1920x1080)
* Automatic SIMD acceleration
* Contiguous memory access, cache friendly

---

#### ✅ Positive Example 2: Numba JIT (Handling Complex Logic)

```python
@jit(nopython=True, cache=True)
def _adjust_with_tone_curve(
    buffer: np.ndarray,
    width: int,
    height: int,
    brightness: float,
    contrast: float
) -> None:
    """Numba Acceleration: Pixel-level processing supporting branch logic."""
    for y in range(height):
        row_offset = y * width * 4
        for x in range(width):
            pixel_offset = row_offset + x * 4
            r = buffer[pixel_offset + 2] / 255.0
            g = buffer[pixel_offset + 1] / 255.0
            b = buffer[pixel_offset] / 255.0
            
            # Complex tone curve (Cannot be simply vectorized)
            r = _apply_tone_curve(r, brightness, contrast)
            g = _apply_tone_curve(g, brightness, contrast)
            b = _apply_tone_curve(b, brightness, contrast)
            
            buffer[pixel_offset + 2] = int(min(255, max(0, r * 255.0)))
            buffer[pixel_offset + 1] = int(min(255, max(0, g * 255.0)))
            buffer[pixel_offset] = int(min(255, max(0, b * 255.0)))

@jit(nopython=True, inline="always")
def _apply_tone_curve(value: float, brightness: float, contrast: float) -> float:
    """Helper Function: Complex tone curve adjustment."""
    adjusted = value + brightness
    if adjusted > 0.65:
        adjusted += (adjusted - 0.65) * contrast
    elif adjusted < 0.35:
        adjusted -= (0.35 - adjusted) * contrast
    return max(0.0, min(1.0, adjusted))
```

**Advantage:**
* Compiled to machine code (~50ms for 1920x1080, 50-100x faster than pure Python)
* Supports complex branch logic
* Automatic type inference and optimization

---

### 6. Performance Measurement and Verification

**Must measure before and after optimization:**

```python
import time

def benchmark_filter(image: QImage, iterations: int = 10) -> float:
    """Measure average filter execution time (ms)."""
    times = []
    for _ in range(iterations):
        img_copy = image.copy()
        start = time.perf_counter()
        apply_filter(img_copy, params)
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
    return sum(times) / len(times)

# Compare three implementations
print(f"QColor Fallback: {benchmark_filter(image, 'qcolor'):.1f} ms")
print(f"Numba JIT:       {benchmark_filter(image, 'numba'):.1f} ms")
print(f"NumPy Vector:    {benchmark_filter(image, 'numpy'):.1f} ms")
```

**Expected Performance Ratio (1920x1080 Image):**

| Method | Typical Time | Ratio |
|--------|--------------|-------|
| QColor Pixel-wise | 5000 ms | 1× (Baseline) |
| Numba JIT | 50 ms | 100× |
| NumPy Vectorization | 10 ms | 500× |

---

### 7. Best Practices Checklist

#### ✅ DO (Must Do)

* Use `@jit(nopython=True, cache=True)` for all pixel-level loops.
* Prioritize NumPy Vectorization for full-image array operations.
* Use small inlined helper functions (`inline="always"`) in Numba functions.
* Use `np.clip(..., out=array)` for in-place modification to save memory.
* Provide layered fallback strategies (NumPy → Numba → QColor).
* Benchmark performance-critical paths before production.

#### ❌ DON'T (Prohibited)

* Use Python objects (`dict`, `list`, strings) in Numba `nopython=True` mode.
* Call Qt APIs (`QImage.pixelColor()`, etc.) within Numba functions.
* Allocate large temporary arrays in hot loops (use `out=` parameter to reuse).
* Over-optimize for small datasets (< 1000 elements) (compilation overhead > benefit).
* Submit "optimized" code without performance measurement.

---

### 8. Engineering Instance Index

Refer to the following files to learn best practices:

| File | Function | Optimization Technique |
|------|----------|------------------------|
| `src/iPhoto/core/filters/algorithms.py` | Pure Algorithm (No Dependencies) | Numba JIT + Inline Functions |
| `src/iPhoto/core/filters/jit_executor.py` | Image Adjustment Executor | Numba Pixel-level Loops |
| `src/iPhoto/core/filters/numpy_executor.py` | Black & White Effect | NumPy Vectorization |
| `src/iPhoto/core/filters/fallback_executor.py` | Compatibility Fallback | QColor Pixel-wise |
| `src/iPhoto/core/filters/facade.py` | Unified Entry | Layered Strategy Pattern |

---
