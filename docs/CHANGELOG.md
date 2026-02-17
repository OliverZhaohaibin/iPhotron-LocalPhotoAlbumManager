# 📋 Changelog

All notable changes to **iPhotron** are documented in this file.

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
