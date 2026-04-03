# 📋 Changelog

All notable changes to **iPhotron** are documented in this file.

---

## 🚀 v4.6.0 — Windows Maps Extension & Offline OsmAnd Runtime

🗺️ *A new Windows-only maps extension brings the offline OsmAnd/OBF runtime into iPhotron, with clearer packaging, installer integration, and a documented upstream build workflow.*

### Key Updates

#### 🗺️ Windows Maps Extension
- Added a self-contained **maps extension** rooted at `src/maps/tiles/extension/` for the offline OBF map runtime.
- The bundled extension now carries `World_basemap_2.obf`, OsmAnd resources, and native runtime binaries in one predictable layout.
- Windows builds can use the native OsmAnd widget runtime for a fuller offline map experience while keeping the repository self-contained.

#### ⚙️ Runtime Selection & Fallback Behavior
- Improved map backend startup so iPhotron can prefer the native Windows widget when the runtime is healthy.
- Preserved the Python/helper-backed OBF renderer as a practical fallback path.
- Linux and macOS continue using the existing Python / legacy map path while the native maps extension remains Windows only.

#### 📦 Packaging & Installer Integration
- Aligned local development, Nuitka packaging, and the Windows installer around the same extension directory contract.
- Documented how the extension is synchronized into packaged builds and optional installer assets.
- Made Windows release work more reproducible by standardizing which runtime artifacts ship with the application.

#### 🧰 Upstream Build Workflow
- Split the OsmAnd runtime build pipeline into the dedicated
  [PySide6-OsmAnd-SDK](https://github.com/OliverZhaohaibin/PySide6-OsmAnd-SDK) side project.
- Added clearer developer documentation for building, syncing, and validating the maps extension from the upstream workspace.
- Improved the handoff between runtime experimentation in the side project and release packaging in the main iPhotron repository.

---

## 🚀 v4.5.0 — Color Grading Expansion & Video Compatibility Improvements

🎨 *A richer color grading workflow, new creative tools, stronger video compatibility, and more native desktop window behavior.*

### Key Updates

#### 🎨 Expanded Color Grading Workflow
- Further refined the color grading experience for smoother, more precise adjustment workflows.
- Added new editing tools including **Definition**, **Noise Reduction**, **Sharpen**, and **Vignette**.
- **Sharpen** includes dedicated `Intensity`, `Edges`, and `Falloff` controls, while **Vignette** adds `Strength`, `Radius`, and `Softness` adjustments.
- Improved the overall editing flow to make advanced adjustments feel more consistent and intuitive.

#### 🎬 Video Preview & Playback Fixes
- **Fixed preview black borders:** Videos now render correctly in preview mode without unwanted letterboxing artifacts.
- **Fixed HEVC and HDR display issues:** Improved compatibility for modern video formats to ensure more reliable playback and preview rendering.
- Better overall media presentation consistency across different codecs and dynamic-range formats.

#### 🐧 Linux Video Thumbnail Reliability
- **Fixed incorrect thumbnail orientation on Linux:** Resolved an intermittent issue that could generate video thumbnails with the wrong rotation.
- Improved thumbnail generation stability for rotated and metadata-sensitive video sources on Linux systems.

#### 🪟 Native Window Snapping
- Added support for native window snapping behavior to better match each platform's built-in desktop experience.
- Window management now feels more natural and integrated across supported operating systems.

---

## 🚀 v4.3.0 — Linux Alpha, RAW Support & Crop Refinements

📸 *Linux enters Alpha testing, RAW workflows arrive, and cropping becomes more precise and familiar.*

### Key Updates

#### 🐧 Linux Version Enters Alpha Testing
- The **Linux version is now officially in Alpha testing**, bringing the iPhotron experience to a whole new platform.
- Early Linux builds extend photo management workflows beyond Windows and macOS while broader compatibility work continues.

#### 📷 Native RAW Image Support
- Added support for **RAW format images**.
- You can now seamlessly import, view, and manage uncompressed, high-quality RAW photos directly inside your library.

#### ✂️ Aspect Ratio Constraints for Cropping
- Added aspect ratio constraint options to the crop tool.
- The cropping workflow now feels closer to the native macOS Photos experience, making edits more intuitive, precise, and familiar.

#### 🐛 Fullscreen and General Bug Fixes
- Fixed a bug affecting fullscreen mode to ensure a more seamless and reliable viewing experience.
- Resolved a range of smaller issues under the hood to improve overall stability.

---

## 🚀 v4.1.0 — MVVM Refinement & Major Scrolling Performance Boost

📸 *A more complete MVVM foundation with dramatically smoother scrolling and more stable large-library browsing.*

### Key Updates

#### 🏗️ MVVM Architecture — More Complete, More Stable State-Driven UI
- **Stronger MVVM boundaries:** Clearer responsibilities across View / ViewModel / Model reduce cross-layer coupling and implicit dependencies.
- **Upgraded state management:** Standardized UI State (`Loading / Content / Empty / Error`) helps prevent edge-case rendering divergence.
- **More consistent unidirectional data flow:** The View only subscribes to ViewModel outputs, while all mutations enter through the ViewModel.
- **Better testability:** Critical logic moved into ViewModel plus UseCase/Service layers for finer unit testing and safer regression coverage.
- **Lifecycle & resource governance:** Subscriptions and async tasks are properly scoped and disposed with lifecycle events to reduce leaks and background overhead.

#### ⚡ Scrolling Performance Boost — Dramatically Smoother Browsing
- **Lighter rendering pipeline:** Reduced unnecessary re-renders and layout recalculations for steadier high FPS while scrolling.
- **Enhanced virtualization for lists and grids:** Improved visible-range computation and reuse strategy to lower UI workload on large datasets.
- **Smarter thumbnail loading:** Prefetching and prioritization now focus on on-screen items, with progressive loading and better decode scheduling.
- **Cache improvements:** Multi-level caching (`memory + disk`) with smarter eviction stabilizes hit rate and reduces redundant decoding.
- **Async task coordination:** Better debouncing and coalescing for rapid scroll events helps avoid main-thread contention and request storms.
- **Lower memory churn:** Fewer transient allocations during fast scrolling reduce GC/ARC pressure and micro-stutters.

---

## 🚀 v4.00 — MVVM Architecture & Advanced Editing

📸 *MVVM architecture for smooth performance, color curves support, and cluster-based map browsing.*

### Key Updates

#### 🏗️ MVVM Architecture — Dramatically Improved Performance
- Complete architectural refactoring to **Model-View-ViewModel (MVVM)** design pattern.
- Clear separation between UI presentation, business logic, and data management layers.
- Reactive UI updates — ViewModel efficiently manages state changes and automatically updates the View.
- Significantly lower UI freezing and lag during photo browsing, editing, and library management.
- Improved memory usage and CPU efficiency through proper data binding and lifecycle management.

#### 🎨 Advanced Color Grading Tools

- **White Balance:** Dedicated panel with Neutral Gray / Skin Tone / Temp & Tint modes; eyedropper sampler for automatic reference white point estimation; Warmth slider with gradient track.
- **Color Curves:** RGB Master curve + individual R/G/B channel curves; interactive editor with draggable control points; Bezier interpolation; histogram overlay.
- **Selective Color:** Six hue-range targets (Red/Yellow/Green/Cyan/Blue/Magenta); independent Hue/Saturation/Luminance controls; feathered hue-distance masking.
- **Levels:** 5-handle input-output tone mapping; per-channel control (RGB/R/G/B); histogram backdrop; smooth interpolation.

#### 🗺️ Cluster-Based Map Browsing
- Smart clustering: automatically groups nearby photos based on GPS coordinates.
- Dynamic cluster sizing adapts to zoom level and photo density.
- Efficient rendering of thousands of GPS-tagged photos.

---

## 🚀 v3.00 — Performance Overhaul

⚡ *Migration to SQLite with global database architecture, optimized for TB-level libraries.*

### Key Updates

#### ⚡ Backend Migration to SQLite with Global Database Architecture
- Complete backend rewrite from JSON-based indexing to **SQLite-powered global database**.
- Single database design — all metadata in one high-performance SQLite database at library root.
- Massive scalability for TB-level photo libraries with hundreds of thousands of files.
- Smart indexing on `parent_album_path`, `ts`, `media_type`, and `is_favorite`.

#### 🏗️ Modular Architecture Refactoring
- 1100+ line monolithic index store split into 5 focused modules: `engine.py`, `migrations.py`, `recovery.py`, `queries.py`, `repository.py`.
- 100% backward compatible.

#### 🛡️ Enhanced Robustness & Efficiency
- Reduced RAM and CPU footprint.
- Automatic recovery with graded repair strategies (REINDEX → Salvage → Reset).
- WAL mode for better concurrency and crash recovery.

#### 💾 Unified Global Cache System
- Single global database replaces scattered `.iphoto/index.jsonl` files.
- Centralized management for easier backup and sync.

---

## 🌓 v2.3.0 — Dark Mode

📸 *Seamlessly switch between Light and Dark themes.*

### Key Updates

#### 🌓 Comprehensive Dark Mode Support
- Three theme options: System Default, Light Mode, Dark Mode.
- Intelligent theme application across the entire UI.
- Edit mode automatically switches to dark theme for optimal color grading.
- Instant theme switching — no restart required.
- Theme-aware components: sidebar, asset grid, detail viewer, info panel, edit panels, context menus.

#### Additional Improvements
- Enhanced edit mode experience with consistent dark theme.
- Refined color palette with improved accessibility contrast ratios.
- Performance optimizations for faster theme switching.
- Native detection of macOS and Windows system theme preferences.

---

## 🐛 v2.1.1 — Bug Fixes and UI Improvements

### Key Updates

#### 🐛 Bug Fixes
- **Fixed thumbnail synchronization:** After editing photos, thumbnails in aggregated albums now sync properly.
- **Fixed gallery grid auto-sizing:** Grid view dynamically responds to window resizing.

#### 🎨 UI Improvements
- Refined album interface to more closely replicate the macOS Photos experience.
- Improved visual consistency, layout spacing, transitions, and animations.

---

## 🚀 v2.00 — Non-Destructive Photo Editing

📸 *Comprehensive non-destructive editing suite with Adjust and Crop modes.*

### Key Updates

#### 🎨 Non-Destructive Photo Editing
- **Adjust Mode:** Light adjustments (Brilliance, Exposure, Highlights, Shadows, Brightness, Contrast, Black Point), Color adjustments (Saturation, Vibrance, Cast), Black & White mode (Intensity, Neutrals, Tone, Grain).
- **Crop Mode:** Perspective correction, Straighten tool (±45°), horizontal flip, interactive crop box with edge snapping.
- All edits stored in `.ipo` sidecar files — originals remain untouched.
- GPU-accelerated preview with real-time OpenGL 3.3 rendering.

#### 💾 Export System
- Export selected photos or all edited photos.
- Configurable export destination (Basic Library or Ask Every Time).

---

## 🚀 v1.00 — First Stable Release

📸 *A modern, folder-native photo manager for Windows and macOS.*

### Key Features
- **🎥 Live Photo Support:** Auto-pairs HEIC/JPG + MOV files by content-ID or timestamp.
- **🗺 Interactive Map View:** GPS metadata visualization on an interactive map.
- **🗂 Folder = Album:** Each folder becomes an album via `.iphoto.album.json`.
- **🧠 Smart Albums:** Library, All Photos, Videos, Favorites, Recently Deleted.
- **🖼 Immersive Detail Viewer:** Filmstrip navigation and floating playback controls.
- **ℹ️ Floating Metadata Panel:** EXIF, camera/lens info, exposure, aperture, file size.
- **⚙️ Rich Interactions:** Drag-and-drop, context menus, incremental scanning, async thumbnail loading.
