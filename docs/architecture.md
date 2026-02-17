# 🏗️ Architecture

> Overall architecture, module boundaries, data flow, and key design decisions for **iPhotron**.

---

## High-Level Architecture

iPhotron follows a **layered architecture** based on **MVVM + DDD (Domain-Driven Design)** principles. The codebase is split into a **pure-Python backend** (no GUI dependency) and a **PySide6 GUI layer** that communicates through a facade.

```mermaid
graph TB
    subgraph GUI["GUI Layer (PySide6)"]
        direction TB
        Views["Views / Widgets"]
        ViewModels["ViewModels"]
        Coordinators["Coordinators"]
        GuiFacade["AppFacade (QObject)"]
        Services["Background Services"]
    end

    subgraph Backend["Core Backend (Pure Python)"]
        direction TB
        AppFacade["app.py — Backend Facade"]
        UseCases["Application Use Cases"]
        DomainModels["Domain Models"]
        Repos["Repository Interfaces"]
        Infra["Infrastructure (SQLite, FS)"]
    end

    Views -->|data binding| ViewModels
    Coordinators --> ViewModels
    Coordinators --> GuiFacade
    GuiFacade -->|signals/slots| AppFacade
    Services --> AppFacade

    UseCases --> DomainModels
    UseCases --> Repos
    Infra -->|implements| Repos
    AppFacade --> UseCases
```

---

## Module Boundary Overview

```mermaid
graph TB
    subgraph DomainLayer["1. Domain Layer"]
        Models["models/ — Album, Asset, MediaType, LiveGroup"]
        RepoInterfaces["repositories.py — IAlbumRepository, IAssetRepository"]
    end

    subgraph ApplicationLayer["2. Application Layer"]
        UC_Open["use_cases/open_album.py"]
        UC_Scan["use_cases/scan_album.py"]
        UC_Pair["use_cases/pair_live_photos.py"]
        SvcAlbum["services/album_service.py"]
        SvcAsset["services/asset_service.py"]
        DTOs["dtos.py"]
    end

    subgraph InfraLayer["3. Infrastructure Layer"]
        SQLiteAsset["sqlite_asset_repository.py"]
        SQLiteAlbum["sqlite_album_repository.py"]
        DBPool["db/pool.py"]
    end

    subgraph CoreBackend["4. Core Backend"]
        App["app.py — Facade"]
        IO["io/ — scanner, metadata"]
        Core["core/ — pairing, resolvers, filters"]
        Cache["cache/ — SQLite index_store"]
        DI["di/ — Dependency Injection"]
        Events["events/ — Event Bus"]
        Errors["errors/ — Error Handling"]
    end

    subgraph GUILayer["5. GUI Layer"]
        Main["main.py"]
        AppCtx["appctx.py — AppContext"]
        Facade["facade.py — AppFacade"]
        Coord["coordinators/"]
        VM["viewmodels/"]
        UI["ui/ — windows, controllers, models, widgets"]
        Tasks["ui/tasks/ — QRunnable workers"]
    end

    subgraph MapModule["6. Map Component"]
        MapWidget["map_widget/"]
        StyleResolver["style_resolver.py"]
        TileParser["tile_parser.py"]
    end

    ApplicationLayer --> DomainLayer
    InfraLayer -->|implements| DomainLayer
    CoreBackend --> ApplicationLayer
    CoreBackend --> InfraLayer
    GUILayer --> CoreBackend
    MapModule --> GUILayer
```

---

## Directory Structure

```
src/
├── iPhoto/
│   ├── domain/              # Pure business models & repository interfaces
│   │   ├── models/          # Album, Asset, MediaType, LiveGroup, query.py
│   │   └── repositories.py  # IAlbumRepository, IAssetRepository
│   ├── application/         # Use Cases & Application Services
│   │   ├── use_cases/       # open_album, scan_album, pair_live_photos
│   │   ├── services/        # album_service, asset_service
│   │   ├── interfaces.py    # IMetadataProvider, IThumbnailGenerator
│   │   └── dtos.py          # Data Transfer Objects
│   ├── infrastructure/      # Concrete implementations
│   │   ├── repositories/    # SQLite implementations
│   │   ├── db/              # Connection pool
│   │   └── services/        # Metadata extraction, thumbnails
│   ├── app.py               # Backend Facade
│   ├── models/              # Legacy data structures (Album, LiveGroup)
│   ├── io/                  # File scanning (scanner.py), metadata reading
│   ├── core/                # Algorithms: pairing, resolvers, filters
│   │   ├── light_resolver.py
│   │   ├── color_resolver.py
│   │   ├── bw_resolver.py
│   │   ├── curve_resolver.py
│   │   ├── selective_color_resolver.py
│   │   ├── levels_resolver.py
│   │   └── filters/         # NumPy → Numba JIT → QColor fallback
│   ├── cache/               # Global SQLite database (index_store/)
│   ├── utils/               # Wrappers: exiftool.py, ffmpeg.py
│   ├── schemas/             # JSON Schema (album.schema.json)
│   ├── di/                  # Dependency Injection container
│   ├── events/              # Event bus (publish-subscribe)
│   ├── errors/              # Unified error handling
│   └── gui/                 # PySide6 desktop application
│       ├── main.py          # GUI entry point (iphoto-gui)
│       ├── appctx.py        # AppContext — shared global state
│       ├── facade.py        # AppFacade (QObject) — GUI ↔ Backend bridge
│       ├── coordinators/    # MVVM Coordinators
│       ├── viewmodels/      # ViewModels for data binding
│       ├── services/        # Background operation services
│       ├── background_task_manager.py
│       └── ui/              # Windows, controllers, models, widgets, tasks
└── maps/                    # Semi-independent map rendering module
    ├── map_widget/          # Core map widget & rendering
    ├── style_resolver.py    # MapLibre style sheet parser
    └── tile_parser.py       # .pbf vector tile parser
```

---

## Data Flow

### Album Opening Flow

```mermaid
sequenceDiagram
    participant User
    participant GUI as GUI (View)
    participant VM as ViewModel
    participant Facade as AppFacade
    participant Backend as app.py
    participant UC as OpenAlbumUseCase
    participant Repo as SQLiteRepository
    participant FS as FileSystem

    User->>GUI: Select album folder
    GUI->>VM: Update selected album
    VM->>Facade: openAlbum(path)
    Facade->>Backend: open_album(path)
    Backend->>UC: execute(path)
    UC->>FS: Read .iphoto.album.json
    UC->>Repo: Query assets for album
    Repo->>Repo: SQLite indexed query
    Repo-->>UC: Asset list
    UC-->>Backend: AlbumDTO
    Backend-->>Facade: Result
    Facade-->>VM: Signal: albumOpened
    VM-->>GUI: Update asset grid
```

### Photo Editing Flow

```mermaid
sequenceDiagram
    participant User
    participant EditView as Edit View
    participant Resolver as Resolvers (light, color, bw, curves, levels)
    participant GLRenderer as OpenGL Renderer
    participant IPO as .ipo Sidecar File

    User->>EditView: Adjust slider
    EditView->>Resolver: Compute parameters
    Resolver-->>GLRenderer: Shader uniforms
    GLRenderer-->>EditView: Real-time preview
    User->>EditView: Click "Done"
    EditView->>IPO: Save adjustments to .ipo
```

### Scanning & Indexing Flow

```mermaid
sequenceDiagram
    participant User
    participant GUI as GUI
    participant Scanner as io/scanner.py
    participant Meta as io/metadata.py
    participant DB as SQLite (global_index.db)

    User->>GUI: Trigger scan
    GUI->>Scanner: scan(path)
    Scanner->>Scanner: Walk directory tree
    Scanner->>Meta: Extract metadata (ExifTool)
    Meta-->>Scanner: EXIF, GPS, timestamps
    Scanner->>DB: Upsert assets (idempotent)
    DB-->>GUI: Scan complete
```

---

## Key Design Decisions

### ADR-1: Folder-Native Album Design

**Decision:** Each filesystem folder is treated as an album. Album metadata is stored in `.iphoto.album.json` manifest files within each folder.

**Rationale:** No import step is required. Users keep their existing folder structure. The system is fully rebuildable from the filesystem.

### ADR-2: Non-Destructive Editing with `.ipo` Sidecar Files

**Decision:** All photo edits are stored in `.ipo` (iPhoto Output) JSON sidecar files alongside originals.

**Rationale:** Original files remain 100% untouched. Edits can be reverted, modified, or removed at any time. The sidecar approach avoids database lock-in.

### ADR-3: Global SQLite Database (v3.00+)

**Decision:** All asset metadata is stored in a single SQLite database (`global_index.db`) at the library root, replacing per-album JSON index files.

**Rationale:** TB-level libraries caused freezing with JSON-based indexing. SQLite provides instant queries via multi-column indexes, WAL mode for crash safety, and automatic recovery.

### ADR-4: MVVM + DDD Layered Architecture (v4.00+)

**Decision:** Adopt MVVM for the GUI layer and DDD for the backend, with a clear Facade boundary.

**Rationale:** Separates pure business logic (testable without GUI) from UI presentation. Coordinators manage navigation flow. ViewModels manage state and reduce unnecessary re-renders.

### ADR-5: GPU-Accelerated Preview Rendering

**Decision:** Use OpenGL 3.3 for real-time edit preview with perspective transforms and color grading in shaders.

**Rationale:** CPU-based rendering was too slow for interactive editing. The GPU pipeline delivers instant visual feedback during adjustments.

### ADR-6: Three Coordinate Systems for Crop & Perspective

The crop tool uses three distinct coordinate systems:

| Space | Description | Purpose |
|-------|-------------|---------|
| **A. Original Texture Space** | Raw pixel space `[0, W_src] × [0, H_src]` | Input source for perspective transform |
| **B. Projected Space** | After perspective matrix; original rect → convex quad `Q_valid` | **Core calculation space** — all black-border prevention logic happens here |
| **C. Viewport/Screen Space** | Final pixel coordinates on the screen widget | User interaction only; inverse-transform to B before calculations |

**Key Rule:** Always operate crop logic in **Projected Space (B)**. Screen coordinates are for input only; texture coordinates are for rendering only.

---

## External Dependencies

| Tool | Purpose |
|------|---------|
| **ExifTool** | Reads EXIF, GPS, QuickTime, and Live Photo metadata |
| **FFmpeg / FFprobe** | Generates video thumbnails & parses video info |

Both must be available in the system `PATH`. Python dependencies (e.g., `Pillow`, `reverse-geocoder`, `numba`) are managed via `pyproject.toml`.
