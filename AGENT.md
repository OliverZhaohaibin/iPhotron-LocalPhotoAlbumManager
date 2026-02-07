# `AGENT.md` – iPhoto Development Principles

## 1. Core Philosophy

* **Album = Folder**: Any folder can be an album; no database dependency for structure.
* **Original Files Are Immutable**: **Never modify photos/videos directly** (rename, crop, write EXIF, etc.) unless user explicitly enables "organize/repair" mode.
* **Human Decisions in Manifest**: Cover photos, featured items, sorting, tags, etc. are written to `manifest.json` and other sidecar files.
* **Cache Is Disposable**: Thumbnails, index (`index.jsonl`), pairing results (`links.json`) can be deleted anytime; software must auto-rebuild.
* **Live Photo Pairing**: Strong pairing based on `content.identifier` first, weak pairing (same name/time proximity) second; results written to `links.json`.

---

## 2. File & Directory Conventions

* **Marker Files**

  * `.iphoto.album.json`: Complete manifest (recommended)
  * `.iphoto.album`: Minimal marker (empty file, represents "this is an album")

* **Hidden Work Directory** (deletable):

  ```
  /<LibraryRoot>/.iphoto/
    global_index.db    # Global SQLite database (metadata for entire library)
    manifest.json      # Optional manifest location
    links.json         # Live Photo pairing and logical groups
    featured.json      # Featured UI cards
    thumbs/            # Thumbnail cache
    manifest.bak/      # Historical backups
    locks/             # Concurrency locks
  ```

  **Note:** Since v3.00, `index.jsonl` has been replaced by `global_index.db`. The global SQLite database stores all album asset metadata.

* **Original Photos/Videos**

  * Remain in album directory, not moved or renamed.
  * Supports HEIC/JPEG/PNG/MOV/MP4, etc.

---

## 3. Data & Schema

* **Manifest (`album`)**: Authoritative data source, must comply with `schemas/album.schema.json`.
* **Global Index (`global_index.db`)**: Global SQLite database storing all asset metadata; can be rebuilt if deleted, but requires re-scanning.
* **Links (`links.json`)**: Live Photo pairing cache; can be rebuilt if deleted.
* **Featured (`featured.json`)**: Featured photo UI layout (crop box, titles, etc.), optional.

**v3.00 Architecture Changes:**
- Migrated from distributed `index.jsonl` files to a single global SQLite database
- Database located at library root `.iphoto/global_index.db`
- Supports cross-album queries and high-performance indexing
- WAL mode ensures concurrent safety and crash recovery

---

## 4. Coding Rules

* **Fixed Directory Structure** (see `src/iPhoto/…`, modules divided into `domain/`, `application/`, `infrastructure/`, `models/`, `io/`, `core/`, `cache/`, `utils/`).
* **Data Classes**: Uniformly defined with `dataclass` (see `models/types.py`).
* **Error Handling**: Must throw custom errors (see `errors.py`), no bare `Exception`.
* **File Writing**: Must use atomic operations (`*.tmp` → `replace()`), manifest must be backed up to `.iPhoto/manifest.bak/` before writing.
* **Database Operations**:
  * Use `IAssetRepository` / `IAlbumRepository` interfaces for all database CRUD operations
  * Inject Repository instances via `DependencyContainer`
  * Use transaction context manager `with repo.transaction():` to ensure atomicity
  * Write operations use idempotent upsert (INSERT OR REPLACE)
* **Locks**: Check `.iPhoto/locks/` before writing `manifest/links` to avoid concurrency conflicts. Database concurrency handled via WAL mode.

---

## 5. AI Code Generation Principles

* **No Hardcoded Paths**: Always use `Path` composition.
* **No Hardcoded JSON**: Must use `jsonschema` validation; provide defaults when necessary.
* **No Implicit Original Modification**: Writing EXIF/QuickTime metadata only in `repair.py`, must be controlled by `write_policy.touch_originals=true`.
* **Output Must Be Runnable**: Complete functions/classes, not fragments.
* **Clear Comments**: Document inputs, outputs, boundary conditions.
* **Cross-Platform**: Works on Windows/macOS/Linux.
* **External Dependencies**: Only call dependencies declared in `pyproject.toml`. For ffmpeg/exiftool, must use wrappers (`utils/ffmpeg.py`, `utils/exiftool.py`).
* **Caching Strategy**:
  * Global database uses idempotent upsert operations (INSERT OR REPLACE)
  * Incremental scanning: Only processes new/modified files
  * Database automatically handles deduplication and updates
  * Thumbnails and pairing information also support incremental updates

---

## 6. Architecture Overview (MVVM + DDD)

This project adopts **MVVM + DDD (Domain-Driven Design)** layered architecture:

### 6.1 Domain Layer (`domain/`)
* **`models/`**: Domain entities (`Album`, `Asset`, `MediaType`, `LiveGroup`)
* **`models/query.py`**: Query object pattern (filtering, sorting, pagination)
* **`repositories.py`**: Repository interfaces (`IAlbumRepository`, `IAssetRepository`)

### 6.2 Application Layer (`application/`)
* **`use_cases/`**: Business use case encapsulation
  * `open_album.py`: Open album use case
  * `scan_album.py`: Scan album use case
  * `pair_live_photos.py`: Live Photo pairing use case
* **`services/`**: Application services
  * `album_service.py`: Album business logic
  * `asset_service.py`: Asset business logic
* **`interfaces.py`**: Abstract interfaces (`IMetadataProvider`, `IThumbnailGenerator`)
* **`dtos.py`**: Data Transfer Objects

### 6.3 Infrastructure Layer (`infrastructure/`)
* **`repositories/`**: Repository implementations
  * `sqlite_asset_repository.py`: SQLite asset repository
  * `sqlite_album_repository.py`: SQLite album repository
* **`db/pool.py`**: Thread-safe database connection pool
* **`services/`**: Infrastructure services

### 6.4 GUI Layer (`gui/`)
* **`coordinators/`**: MVVM coordinators
  * `main_coordinator.py`: Main window coordination
  * `navigation_coordinator.py`: Navigation coordination
  * `playback_coordinator.py`: Playback coordination
  * `edit_coordinator.py`: Edit coordination
  * `view_router.py`: View routing
* **`viewmodels/`**: View models
  * `asset_list_viewmodel.py`: Asset list ViewModel
  * `album_viewmodel.py`: Album ViewModel
  * `asset_data_source.py`: Data source abstraction

### 6.5 Core Infrastructure
* **`di/container.py`**: Dependency Injection container
* **`events/bus.py`**: Event bus (publish-subscribe)
* **`errors/handler.py`**: Unified error handling

---

## 7. Module Responsibilities

* **domain/**: Pure domain models and repository interfaces, framework-independent.
* **application/**: Business use cases and application services, coordinating domain logic.
* **infrastructure/**: Concrete implementations (SQLite, ExifTool, etc.).
* **models/**: Legacy data classes + loading and saving of manifest/links.
* **io/**: File system scanning, metadata reading, thumbnail generation, sidecar writing.
* **core/**: Algorithmic logic (pairing, sorting, featured management, image adjustments).
  * `light_resolver.py`: Light adjustment parameter resolution (Brilliance/Exposure/Highlights/Shadows/Brightness/Contrast/BlackPoint)
  * `color_resolver.py`: Color adjustment parameter resolution (Saturation/Vibrance/Cast) + image statistics analysis
  * `bw_resolver.py`: Black & White parameter resolution (Intensity/Neutrals/Tone/Grain)
  * `curve_resolver.py`: Color curve adjustments with Bezier interpolation and LUT generation
  * `selective_color_resolver.py`: Selective color adjustments targeting six hue ranges with HSL processing
  * `levels_resolver.py`: Levels adjustments with 5-handle input-output tone mapping
  * `filters/`: High-performance image processing (NumPy vectorized + Numba JIT + QColor fallback)
* **cache/**: Global SQLite database management and lock implementation.
  * `index_store/engine.py`: Database connection and transaction management
  * `index_store/migrations.py`: Schema evolution and version management
  * `index_store/recovery.py`: Database auto-repair and salvage
  * `index_store/queries.py`: Parameterized SQL query construction
  * `index_store/repository.py`: High-level CRUD API
  * `lock.py`: File-level lock implementation
* **utils/**: General utilities (hash, json, logging, external tool wrappers).
* **schemas/**: JSON Schema definitions.
* **cli.py**: Typer command-line entry point.
* **app.py**: High-level facade coordinating all modules.

---

## 8. Code Style

* Follow **PEP8**, line width 100.
* Type hints must be complete (`Optional[str]`, `list[Path]`, etc.).
* Function naming: Start with verbs (`scan_album`, `pair_live`).
* Class naming: Capitalized (`Album`, `IndexStore`).
* Exception naming: `XxxError`.

---

## 9. Testing & Robustness

* All modules must have `pytest` unit tests.
* Must handle missing/corrupted input files with errors, not crashes.
* `global_index.db`, `links.json` must auto-rebuild if missing.
* Multi-endpoint sync conflicts handled per manifest's `conflict.strategy`.

---

## 10. Safety Switches

* Default:

  * Don't modify originals
  * Don't organize directories
  * Don't write EXIF
* When user explicitly allows:

  * Use `exiftool`/`ffmpeg` in `repair.py` to write back
  * Must generate `.backup` first

---

## 11. Minimal Command Set

* `iphoto init`: Initialize album
* `iphoto scan`: Generate/update index
* `iphoto pair`: Generate/update pairing
* `iphoto cover set`: Set cover
* `iphoto feature add/rm`: Manage featured items
* `iphoto report`: Output album statistics and anomalies

---

## 12. Edit System Architecture

### 1. Overview

The edit system provides **non-destructive** image adjustment capabilities, divided into two modes:

* **Adjust Mode**: Light / Color / Black & White / Curves / Selective Color / Levels parameter adjustments
* **Crop Mode**: Perspective correction / straighten / crop box adjustments

### 2. Core Components

#### GUI Layer (`src/iPhoto/gui/ui/widgets/`)

| Component | Responsibility |
|-----------|---------------|
| `edit_sidebar.py` | Edit sidebar container, manages Adjust/Crop page switching |
| `edit_light_section.py` | Light adjustment panel (7 sub-sliders + Master slider) |
| `edit_color_section.py` | Color adjustment panel (Saturation/Vibrance/Cast) |
| `edit_bw_section.py` | Black & White adjustment panel (Intensity/Neutrals/Tone/Grain) |
| `edit_curve_section.py` | Color curves panel with RGB and per-channel curve editing |
| `edit_selective_color_section.py` | Selective color panel targeting six hue ranges with HSL controls |
| `edit_levels_section.py` | Levels panel with 5-handle tone mapping and histogram display |
| `edit_perspective_controls.py` | Perspective correction controls (Vertical/Horizontal/Straighten) |
| `thumbnail_strip_slider.py` | Slider component with real-time thumbnail preview |
| `gl_crop/` | Crop interaction module (Model/Controller/HitTester/Animator/Strategies) |

#### Core Layer (`src/iPhoto/core/`)

| Module | Responsibility |
|--------|---------------|
| `light_resolver.py` | Master slider → 7 Light parameters mapping algorithm |
| `color_resolver.py` | Master slider → Color parameters mapping + image color statistics analysis |
| `bw_resolver.py` | Master slider → B&W parameters mapping (three-anchor Gaussian interpolation) |
| `curve_resolver.py` | Color curve data structures and LUT generation for GPU-accelerated rendering |
| `selective_color_resolver.py` | HSL-based selective color adjustments with hue-distance masking |
| `levels_resolver.py` | 5-handle tone mapping with smooth interpolation |
| `image_filters.py` | Image adjustment application entry point |
| `filters/` | High-performance image processing executors (layered strategy pattern) |

### 3. Data Flow

```
User drags slider
     ↓
EditSession.set_value(key, value)  # State update
     ↓
valueChanged signal → Controller
     ↓
GLRenderer.set_uniform(...)  # GPU real-time preview
     ↓
EditSession.save() → .ipo file  # Persistence
```

### 4. Parameter Range Conventions

| Category | Parameter | Range | Default |
|----------|-----------|-------|---------|
| Light | Brilliance/Exposure/Highlights/Shadows/Brightness/Contrast/BlackPoint | [-1.0, 1.0] | 0.0 |
| Color | Saturation/Vibrance | [-1.0, 1.0] | 0.0 |
| Color | Cast | [0.0, 1.0] | 0.0 |
| B&W | Intensity/Master | [0.0, 1.0] | 0.5 |
| B&W | Neutrals/Tone/Grain | [0.0, 1.0] | 0.0 |
| Curves | Control Points | [0.0, 1.0] x [0.0, 1.0] | Identity |
| Selective Color | Hue/Saturation/Luminance per range | [-1.0, 1.0] | 0.0 |
| Levels | 5 Handles | [0.0, 1.0] → [0.0, 1.0] | Identity |
| Crop | Perspective_Vertical/Horizontal | [-1.0, 1.0] | 0.0 |
| Crop | Crop_Straighten | [-45.0, 45.0]° | 0.0 |
| Crop | Crop_CX/CY/W/H | [0.0, 1.0] | 0.5/0.5/1.0/1.0 |

### 5. Crop Module Layering

```
gl_crop/
├── model.py          # State model (CropSessionModel)
├── controller.py     # Interaction coordinator (CropInteractionController)
├── hit_tester.py     # Hit testing (edges/corners/interior)
├── animator.py       # Animation management (zoom/bounce)
├── strategies/       # Interaction strategies (drag/zoom)
└── utils.py          # Utility functions (CropBoxState/CropHandle)
```

### 6. Development Standards

* **All edit parameters must be read/written via `EditSession`**, direct `.ipo` file manipulation is prohibited
* **Slider interactions must emit `interactionStarted/Finished` signals**, used to pause file monitoring
* **Thumbnail generation must execute in background threads**, avoid blocking UI
* **Perspective transform matrix calculation uses logical aspect ratio**, see OpenGL Development Standards Section 12.5
* **New edit tools (Curves/Selective Color/Levels) follow the same non-destructive pattern**, all stored in `.ipo` sidecars

---

## 13. OpenGL Development Standards

### 1. File Inventory

Files currently involved with direct OpenGL calls or GL context management:

* **Core Image Viewer (Pure GL)**

  * `src/iPhoto/gui/ui/widgets/gl_image_viewer/` (GL image viewer module directory)
    * `widget.py` (Widget host and event handling)
    * `components.py` (GL rendering components)
    * `resources.py` (GL resource management)
    * `geometry.py` (Geometric calculations)
    * `input_handler.py` (Input event handling)

* **Map Component (GL-accelerated)**

  * `maps/map_widget/map_gl_widget.py` (GL-based map tile rendering)

* **Edit Preview Renderer**

  * `src/iPhoto/gui/ui/widgets/gl_renderer.py` (Core OpenGL renderer for edit preview)
  * Handles texture upload, shader uniforms, and real-time adjustment preview

### 2. OpenGL Version & Profile

* **Target**: OpenGL 3.3 Core Profile
* **Shading Language**: GLSL 3.30
* **Rationale**: Maximum compatibility across Windows/macOS/Linux while supporting modern features

### 3. Resource Management

* **Texture Lifecycle**: 
  * Upload textures on demand
  * Cache frequently used textures
  * Explicit cleanup on widget destruction
  
* **Shader Compilation**:
  * Compile shaders once at initialization
  * Cache compiled programs
  * Validate shader compilation with error reporting

* **Buffer Objects**:
  * Use VBOs for vertex data
  * Reuse buffers when geometry doesn't change
  * Delete buffers in cleanup phase

### 4. Coordinate Systems

See Section 12.4 for detailed coordinate system definitions used in crop and perspective transformations.

### 5. Error Handling

* **GL Error Checking**: Use `glGetError()` in debug builds
* **Shader Compilation**: Check compilation status and log errors
* **Context Loss**: Handle context loss gracefully with resource recreation

---

## 14. Testing Strategy

### 1. Unit Tests

* **Domain Layer**: Test pure business logic without dependencies
* **Application Layer**: Test use cases with mocked repositories
* **Core Algorithms**: Test resolvers, filters, and pairing logic with known inputs/outputs

### 2. Integration Tests

* **Database Operations**: Test repository implementations with real SQLite
* **File Operations**: Test scanner and metadata extraction with sample files
* **Edit Pipeline**: Test end-to-end adjustment application

### 3. GUI Tests

* **ViewModel Logic**: Test state management and data binding
* **Controller Actions**: Test user interaction handling
* **Widget Rendering**: Visual regression testing for critical UI components

---

## 15. Performance Guidelines

### 1. Database Optimization

* **Indexing**: Multi-column indexes on frequently queried fields (`parent_album_path`, `ts`, `media_type`, `is_favorite`)
* **WAL Mode**: Enable Write-Ahead Logging for concurrent read/write
* **Connection Pooling**: Reuse connections across operations
* **Batch Operations**: Use transactions for bulk inserts/updates

### 2. Image Processing

* **Lazy Loading**: Load full images only when needed
* **Thumbnail Generation**: Generate thumbnails asynchronously in background
* **GPU Acceleration**: Offload adjustments to GPU shaders where possible
* **LUT Caching**: Cache lookup tables for curve adjustments

### 3. UI Responsiveness

* **Background Tasks**: Use `QThreadPool` for long-running operations
* **Incremental Rendering**: Render thumbnails progressively
* **Debouncing**: Debounce slider value changes to reduce redundant updates
* **Lazy ViewModel Updates**: Update only visible items in large lists

---

## 16. Security Considerations

### 1. File System Safety

* **Atomic Writes**: Always write to `.tmp` then rename
* **Backup Before Modify**: Back up manifest before modifications
* **Permission Checks**: Verify write permissions before operations
* **Path Validation**: Sanitize and validate all file paths

### 2. Database Safety

* **Prepared Statements**: Use parameterized queries to prevent injection
* **Transaction Rollback**: Roll back on errors to maintain consistency
* **Schema Validation**: Validate schema version before operations
* **Recovery Mechanisms**: Auto-repair corrupted databases when possible

### 3. External Tool Safety

* **Command Injection**: Sanitize arguments passed to exiftool/ffmpeg
* **Output Validation**: Validate external tool outputs before use
* **Error Handling**: Handle tool failures gracefully
* **Version Compatibility**: Check tool versions for compatibility

---

## 17. Dependency Management

### 1. Python Dependencies

Managed via `pyproject.toml`:
* **Core**: PySide6, Pillow, numpy
* **Database**: Built-in sqlite3
* **Processing**: numba (optional JIT acceleration)
* **Utilities**: reverse-geocoder, jsonschema

### 2. External Tools

* **ExifTool**: Metadata extraction and modification
* **FFmpeg/FFprobe**: Video processing and thumbnail generation

### 3. Dependency Updates

* **Semantic Versioning**: Respect version constraints
* **Testing After Updates**: Run full test suite after dependency updates
* **Security Patches**: Apply security patches promptly
* **Compatibility**: Verify cross-platform compatibility after updates

---

## 18. Localization

### 1. String Management

* **UI Strings**: Centralize in translation files
* **Error Messages**: Provide user-friendly localized messages
* **Date/Time Formatting**: Use locale-aware formatting

### 2. Supported Languages

* English (primary)
* Chinese (secondary)
* Extensible for additional languages

---

## 19. Documentation Standards

### 1. Code Documentation

* **Docstrings**: Use Google-style docstrings for all public APIs
* **Type Hints**: Include comprehensive type annotations
* **Comments**: Explain "why", not "what"

### 2. User Documentation

* **Version Docs**: Maintain changelog in `docs/V*.md`
* **README**: Keep README.md updated with current features
* **Architecture Docs**: Document major architectural decisions

### 3. Developer Documentation

* **AGENT.md**: This file - core principles and standards
* **Architecture Diagrams**: Visual representations in `docs/referactor/`
* **API Documentation**: Auto-generated from docstrings

---

## 20. Release Process

### 1. Version Numbering

* **Format**: `vX.YY` (e.g., v4.00)
* **Major Versions**: Significant architectural changes or new major features
* **Minor Updates**: Bug fixes, performance improvements, minor features

### 2. Release Checklist

* [ ] All tests passing
* [ ] Documentation updated
* [ ] Changelog prepared
* [ ] Version number bumped
* [ ] Build artifacts generated
* [ ] Release notes written

### 3. Deployment

* **Binary Distribution**: Packaged executables for Windows/macOS
* **Source Distribution**: Available via GitHub
* **Update Mechanism**: In-app update notifications (future)

---

*This document is the authoritative guide for iPhoto development. All contributors must follow these principles to maintain code quality, consistency, and architectural integrity.*
