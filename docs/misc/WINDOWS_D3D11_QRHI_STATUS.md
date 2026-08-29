# Windows D3D11 QRhi Status and Roadmap

> **Status: experimental Detail-only A/B path.**
>
> Windows production behavior remains `IPHOTO_RHI_BACKEND=auto`, which currently
> selects OpenGL. `IPHOTO_RHI_BACKEND=d3d11` does **not** mean that the entire
> application supports D3D11. In particular, GPU Maps in the main window are not
> compatible with the current D3D11 experiment.

This document records what the Windows D3D11 QRhi path can do today, what is
deliberately unsupported, and what must be completed before D3D11 can become a
product backend. It is a maintenance contract, not an announcement of complete
Windows D3D11 support.

## Current production contract

The supported Windows configuration is:

```text
IPHOTO_RHI_BACKEND=auto
    -> media Detail QRhi backend: OpenGL
    -> MainWindow graphics contract: OpenGL-compatible
    -> Location GPU paths: MapGLWidget / native OsmAnd OpenGL allowed
```

The explicit `opengl` override selects the same media graphics API and is useful
for diagnostics:

```powershell
$env:IPHOTO_RHI_BACKEND = "opengl"
python .\src\entrypoint.py
```

The transparent, frameless Windows main window requests an 8-bit alpha buffer
for the global OpenGL surface format. After the first main-window paint, startup
logs a `Main-window graphics contract` entry containing the selected backend,
actual `QSurface` type, actual alpha bits, and the translucent/frameless flags.

## What the D3D11 experiment currently provides

The following pieces exist and are covered by code-level contracts:

- `IPHOTO_RHI_BACKEND=d3d11` selects `QRhiWidget.Api.Direct3D11` on Windows.
- Startup recognizes and validates a `QSurface::Direct3DSurface` top-level.
- Detail still, adjusted-video, and native-video QRhi paths have non-OpenGL
  renderer implementations.
- Image, overlay, and video QSB assets contain HLSL Shader Model 5.0 variants.
- First-media reveal is content-identity based and remains covered through the
  Windows post-submit composition frame.
- Transition epochs reject delayed submissions from an older media request,
  resource generation, or raw/adjusted surface transition.
- The Detail cover is persistent for the page lifetime and does not require
  widget deletion/recreation between media transitions.

This is sufficient for focused Detail compositor A/B experiments. It is not a
complete application graphics contract.

## Unsupported and unsafe scenarios

### GPU Maps in the same MainWindow

The Location feature can lazily create either of these OpenGL-backed paths:

- `MapGLWidget(QOpenGLWidget)` for the Python GPU renderer;
- a native OsmAnd widget that hosts a native C++ OpenGL widget.

Location is inserted into the same `MainWindow` widget hierarchy as the Detail
QRhi widgets. A D3D11-owned top-level and an OpenGL composited child therefore
do not form a supported single-window graphics contract.

Until backend-aware Maps negotiation is implemented, do not treat this flow as
safe under the D3D11 experiment:

```text
Detail (D3D11) -> Location (OpenGL) -> Detail (D3D11)
```

The current code does not yet prevent this navigation or automatically force
the CPU `MapWidget`. Developers using `d3d11` must avoid GPU Maps and must not
ship that configuration to users.

### Global Desktop OpenGL initialization

Windows startup still configures shared/Desktop OpenGL defaults because the
production Maps architecture uses OpenGL. That means the current experiment can
report:

```text
media QRhi selection = D3D11
global map/OpenGL initialization = Desktop OpenGL
```

This is an intentional incomplete boundary, not the final design. Backend
selection is not yet the single source of truth for all graphics initialization.

### No runtime API fallback

There is no supported hot fallback from a failed D3D11 `renderFailed` signal to
OpenGL. A QRhi API affects the containing top-level window and must be selected
before the native hierarchy is shown. Recreating or changing that contract at
runtime would invalidate the startup and resource-lifecycle guarantees.

To recover, close the application and restart with `auto` or `opengl`.

### Validation gaps

The repository has unit and offscreen state-machine contracts, but it does not
yet establish production parity for all of the following on a real Windows
compositor:

- first still, plain video, adjusted video, and Live Photo after a cold launch;
- rapid image/video navigation and paused raw/adjusted switching;
- minimize/restore, resize, fullscreen, and Edit;
- Location navigation and every map backend;
- packaged/Nuitka builds across Intel, AMD, NVIDIA, and software adapters.

## Support matrix

| Platform/configuration | Detail media | GPU Maps in MainWindow | Status |
|---|---|---|---|
| Windows `auto` | OpenGL QRhi | Supported existing OpenGL paths | Production/default |
| Windows `opengl` | OpenGL QRhi | Supported existing OpenGL paths | Supported diagnostic override |
| Windows `d3d11` | D3D11 QRhi/HLSL | **Unsupported** | Developer Detail-only A/B experiment |
| macOS `auto` | Metal QRhi | Existing platform-specific map contract | Unchanged |
| Linux `auto` | OpenGL QRhi | Existing OpenGL/native paths | Unchanged |

## Running a focused A/B experiment

Always start a fresh process so no previous Detail surface or top-level graphics
contract is reused.

```powershell
# Production baseline
$env:IPHOTO_RHI_BACKEND = "opengl"
powershell -ExecutionPolicy Bypass -File .\tools\collect_windows_scan_playback_diagnostics.ps1

# Experimental Detail-only path; do not open GPU Maps
$env:IPHOTO_RHI_BACKEND = "d3d11"
powershell -ExecutionPolicy Bypass -File .\tools\collect_windows_scan_playback_diagnostics.ps1
```

The collector enables `qt.qpa.gl`, `qt.rhi.*`, multimedia, and application
runtime diagnostics. Inspect `stderr.log` for `Main-window graphics contract`:

- OpenGL should report an OpenGL-compatible surface and alpha bits greater than
  zero for the translucent main window.
- D3D11 should report `Direct3DSurface` and the Direct3D11 media backend.
- Any pre-show surface contract failure, `renderFailed`, permanent cover, blank
  media, or unexpected native-window recreation invalidates that run.

The full reproduction procedure and privacy notes are in
[WINDOWS_SCAN_PLAYBACK_DIAGNOSTICS.md](../WINDOWS_SCAN_PLAYBACK_DIAGNOSTICS.md).

Return to the production path with either command:

```powershell
$env:IPHOTO_RHI_BACKEND = "opengl"
# or
Remove-Item Env:IPHOTO_RHI_BACKEND -ErrorAction SilentlyContinue
```

## Required work before full D3D11 support

Complete these items in order:

1. Introduce one graphics-backend capability policy used by Detail, Maps, and
   startup configuration instead of reading backend/environment state in
   separate subsystems.
2. Under D3D11, force Location to a pure QWidget/CPU `MapWidget` and prevent
   creation of `MapGLWidget`, native OsmAnd OpenGL widgets, and other OpenGL map
   children in the D3D11-owned `MainWindow`.
3. Make global Desktop OpenGL initialization backend-aware. D3D11 mode must not
   configure OpenGL as though it owned the main top-level; any retained OpenGL
   use must have an explicitly isolated and validated native contract.
4. Add fail-fast capability reporting so the UI explains that GPU Maps are
   unavailable in D3D11 mode rather than discovering the conflict during lazy
   Location construction.
5. Run the real Windows navigation matrix for OpenGL and D3D11, including at
   least 30 clean launches per first-media category, Maps, Edit, fullscreen,
   resource recreation, and packaged builds.
6. Promote D3D11 to `auto` only after the complete matrix passes and the
   documentation, diagnostics, packaging, and fallback policy agree on one
   application-wide graphics contract.

Longer-term alternatives, such as a D3D/QRhi map renderer or a separately owned
native map window, require their own design and validation. They are not implied
by the current experimental override.

## Source-of-truth locations

- Media QRhi selection: `src/iPhoto/gui/render_backend.py`
- Top-level surface validation and diagnostics: `src/iPhoto/gui/main.py`
- Detail still/video QRhi widgets: `src/iPhoto/gui/ui/widgets/`
- Python OpenGL map widget: `src/maps/map_widget/map_gl_widget.py`
- Native OsmAnd OpenGL host: `src/maps/map_widget/native_osmand_widget.py`
- Location map construction: `src/iPhoto/gui/ui/widgets/photo_map_view.py`

When these files change, update this document if the support matrix or any
experimental restriction changes.
