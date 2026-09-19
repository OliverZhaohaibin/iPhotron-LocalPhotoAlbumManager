# Windows scan-time still playback diagnostics

Use this collector when scanning eventually makes all still photos blank while videos
continue to play, or when Edit/fullscreen stops responding for photos.

The collector does not copy photos, thumbnails, the SQLite index, settings, or credentials.
It records privacy-safe render/scan events, periodic all-thread Python stacks, GUI resource
counts, process memory/handle counts, GPU/OS versions, and relevant Windows application events.
Performance path fields are replaced with stable session-only hashes. Known user, application,
repository, and optional library roots are redacted before the ZIP is created.
Individual filenames can still appear in application diagnostics, so review the ZIP before
sharing it if filenames are sensitive.

## Run from the current source checkout

Close every running iPhoto instance, open PowerShell in the repository, and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\collect_windows_scan_playback_diagnostics.ps1 `
  -LibraryRootToRedact "D:\Path\To\Your\PhotoLibrary"
```

The script prefers `.venv\Scripts\python.exe`, ensuring the latest diagnostic probes in the
checkout are active. Pass `-PythonExe` when the environment is elsewhere:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\collect_windows_scan_playback_diagnostics.ps1 `
  -PythonExe "D:\Python\iPhoto\.venv\Scripts\python.exe" `
  -LibraryRootToRedact "D:\Photos"
```

## Run a newly built packaged executable

Pass the executable explicitly. To obtain persistent runtime stack traces, the executable
must be built from a revision containing this collector.

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\collect_windows_scan_playback_diagnostics.ps1 `
  -AppPath ".\build\entrypoint.dist\entrypoint.exe" `
  -LibraryRootToRedact "D:\Photos"
```

## Reproduction flow

1. Let the collector launch iPhoto.
2. Start the same scan and keep selecting normal JPG/PNG photos until the failure appears.
3. While a blank still photo and the unresponsive Edit behavior are visible, return to the
   PowerShell window and press `R` once. This writes a precise timestamp marker.
4. Test fullscreen once, then close iPhoto normally. If it cannot close, press `Q` in the
   collector window to stop it.
5. Send back the single ZIP path printed in green. By default it is created on the Desktop.

## Fullscreen crop/straighten offset and flicker (OpenGL only)

This scenario keeps Windows on QRhi/OpenGL. It does not run the experimental
D3D11 comparison described in the separate section below.

From the repository root, using the updated source checkout:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\collect_windows_scan_playback_diagnostics.ps1 -Scenario Fullscreen
```

For an updated packaged application, add `-AppPath "C:\path\to\entrypoint.exe"`.
The scenario sets OpenGL, the Windows Qt platform and `IPHOTO_FULLSCREEN_DIAG=1`
for the launched process, then restores the inherited environment. No original
media, sidecars or screenshots are copied into this bundle.

Use three stills: no adjustments, asymmetric crop only, and asymmetric crop plus
straightening. For each, leave the window idle, enter fullscreen, leave it idle
again, zoom using several wheel steps, then double-click to exit. Repeat at least
20 fullscreen round trips; also exercise Esc/native exit, Edit, ordinary video
and Live Photo. Repeat on available 100%, 125%, 150% and 200% display scales,
including moving between monitors with different scales. Record each observed
offset/flicker with `R`, then close the application normally.

`detail_events.jsonl` adds:

- `fullscreen_environment`: Qt/PySide version, backend, screen sizes, DPR and
  refresh rates; GPU/driver and application revision remain in `system.json`.
- `fullscreen_trace`: event, viewer identity, sequence/submission counts, window
  fullscreen state, logical widget and physical render-target dimensions,
  texture dimensions, crop, cover, zoom, pan, pending reset/upload and content
  revision. The existing GPU/LOD events provide decode and asset generations.
- `gl_entry`/`draw` samples: GL viewport, framebuffer/program binding, color
  mask, blend/depth/stencil/cull/scissor enables and bounded error codes. Sampling
  covers 120 submitted frames after interaction and then at most one sample per
  event per second. These GL queries are disabled in normal operation.

Compare the same media and actions before/after the fix. A historical executable
without the new instrumentation still provides the collector's system/runtime
logs, but cannot provide the new geometry events. Preserve that limitation when
interpreting baseline results. Stable geometry plus visible flashing requires
further compositor investigation; `frameSubmitted` is not proof of DWM scan-out.

### Synthetic visible-window pixel probe

Run this separately with the source environment on an unlocked, interactive
Windows desktop; keep its window unobscured and disable display sleep:

```powershell
.\.venv\Scripts\python.exe .\tools\windows_fullscreen_probe.py --cycles 20 --output .\fullscreen-probe
.\.venv\Scripts\python.exe .\tools\windows_fullscreen_probe.py --cycles 20 --poison-gl-state --output .\fullscreen-probe-gl-state
```

The probe uses the production viewer in a translucent frameless Qt window and
generated green images. It cycles plain/cropped/straightened images through
fullscreen, windowed, idle and wheel zoom states. It reads compositor pixels
with `QScreen.grabWindow`, never `grabFramebuffer` (which forces a fresh draw),
and compares visible bounds with the CPU transform. The second run deliberately
pollutes GL state before the renderer establishes its own state. Results go to
`result.json` and `detail_events.jsonl`; only failing synthetic-window captures
are saved. Keep other windows away from the probe to avoid false failures or
unrelated content in failure captures. Exit code 0 means all sampled checks
passed; this sampling does not rule out every transient between captures.

This probe isolates the rendering/compositor contract. It does **not** replace
the real application's fullscreen window-manager, Edit, video, multi-monitor or
packaged acceptance checks above. Local/offscreen tests cannot validate those
Windows paths. Acceptance requires no accumulated crop offset, LOD-induced
scale jumps, alternating blank/content frames or desktop leakage, and no
unbounded idle redraw loop after the transitions settle.

For a fullscreen flicker that persists with stable texture/geometry and no GL
errors, run these three **separate** probe processes. Keep the same screen and
DPI (including 250% on the reported device), and keep each window unobscured:

```powershell
.\.venv\Scripts\python.exe .\tools\windows_fullscreen_probe.py --cycles 3 --output .\probe-baseline
.\.venv\Scripts\python.exe .\tools\windows_fullscreen_probe.py --cycles 3 --opaque-window --output .\probe-opaque
.\.venv\Scripts\python.exe .\tools\windows_fullscreen_probe.py --cycles 3 --swap-interval 0 --output .\probe-no-vsync
```

The baseline uses the application's default GL version/profile request. The
opaque comparison only disables the host's translucent attribute; the no-vsync
comparison only changes requested swap interval (which the driver may ignore).
Neither changes production defaults. Share `result.json` and `detail_events.jsonl`
from each directory, plus any failed synthetic captures. Report visible flicker
even if sampled pixel checks pass. The three-cycle runs triage the cause; repeat
the existing 20-cycle acceptance matrix after a candidate fix.

Source-process collection now resolves nested Windows Python launchers using the
runtime diagnostic PID or a descendant with a main HWND. If the GUI cannot be
identified, collection fails instead of silently reporting launcher-only metrics.

### Candidate fullscreen composition workaround

The reported three-way comparison reproduced fullscreen wheel failures in all
configurations. For that machine, run this single follow-up with the updated code:

```powershell
python .\tools\windows_fullscreen_probe.py --cycles 3 --fullscreen-border --output .\probe-border
```

This opt-in candidate uses Qt's documented native `WS_BORDER` workaround for
Windows fullscreen OpenGL composition. A one-pixel native border may be visible.
It preserves the existing HWND, QRhi session, translucency and swap interval;
Qt restores its saved normal style on exit. The probe must record a nonzero
`fullscreen_border_verifications` in `result.json`; an unapplied workaround
cannot count as a passing comparison. Share the whole `probe-border` directory
and the visual result. The previous three groups need not be repeated.

If that probe is stable, verify the **real application** with the same candidate:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\collect_windows_scan_playback_diagnostics.ps1 -Scenario Fullscreen -FullscreenBorder
```

Check plain/cropped/straightened stills, wheel zoom, repeated fullscreen entry,
double-click/Esc exit, Edit preview, minimize/restore and multiple DPI scales.
`fullscreen_composition_border` must show `applied=true`; failures report false
and leave normal Qt window handling active. The candidate remains disabled in
ordinary launches. These tools do not automatically change production defaults.
New traces record actual top-level translucency and `fullscreen_gl_context`
(GL version/profile, renderer and Qt context-reported swap interval). The latter
does not override or prove a driver's effective vsync policy.

## First-media QRhi submission A/B

For the Windows-only first-open leak, run from a fresh process so no Detail
QRhi surface has already been submitted. Test both configurations:

```powershell
$env:IPHOTO_RHI_BACKEND = "opengl"
powershell -ExecutionPolicy Bypass -File .\tools\collect_windows_scan_playback_diagnostics.ps1

$env:IPHOTO_RHI_BACKEND = "d3d11"
powershell -ExecutionPolicy Bypass -File .\tools\collect_windows_scan_playback_diagnostics.ps1
```

For each backend, repeat first still, plain video, adjusted video, and Live
Photo opening from 30 clean launches. Exercise Edit, fullscreen, and paused
raw/adjusted switching in both modes. Only the supported OpenGL baseline should
exercise opening/closing Maps. The experimental D3D11 run is Detail-only and
must not enter Location or create a GPU map widget.

`stderr.log` must contain the `Main-window graphics contract` entry. OpenGL
runs should report an alpha buffer greater than zero; a missing/zero value is a
failed graphics contract, not a successful reproduction run.

The extra active-surface `frameSubmitted` is a QRhi submission heuristic, not a
DXGI/DWM presentation fence. It verifies the application state machine and an
additional Qt composition submission. The reveal wait is bounded: after the
matching current-generation media submission, a missing extra composition may
end with `post_submit_deadline` followed by `surface_revealed` with
`reason=deadline`. A stale transition must instead record
`post_submit_discarded` and must not reveal its media. Only the real Windows
repetition matrix can determine whether this preserves the user-visible
first-frame leak fix.

Transition traces must show `presentation_suppressed` before the exposed
surface's `video_surface_blank_requested`/blank submission and
`presentation_resumed` only after matching new content is installed. A rapid
A→B switch must record `post_submit_discarded`
for any scheduled or armed A barrier, including `reason`, `state`, and the old
epoch. During the suppressed interval a real compositor screenshot must contain
the opaque Detail background, never A's pixels or media-specific overlays.
When async preparation changes the active video renderer, the trace must add a
second `video_surface_blank_requested` with `reason=surface_switch`, the current
Detail generation, the current media generation, and `surface=adjusted|native`.
Its matching `presentation_resumed` must follow successful frame installation.

Run the real-pixel contracts manually from a normal interactive Windows
desktop. Setting the platform explicitly is required because the unit-test
fixtures otherwise default to `offscreen`:

```powershell
$env:QT_QPA_PLATFORM = "windows"
$env:IPHOTO_RHI_BACKEND = "opengl"
$env:IPHOTO_WINDOWS_COMPOSITOR_CYCLES = "100"
$env:IPHOTO_WINDOWS_COMPOSITOR_FORCE_UPLOAD_FAILURE = "1"
$tests = @(
  "tests/ui/widgets/test_gl_image_viewer_post_load_signal.py",
  "tests/ui/widgets/test_video_area.py"
)
1..30 | ForEach-Object {
  python -m pytest -q -m windows_compositor $tests
  if ($LASTEXITCODE -ne 0) { throw "Compositor run $_ failed" }
}
```

The video pixel contract drives `VideoArea.begin_load()` followed by
`PlayerViewController.begin_video_transition()`; it must not request the blank
frame directly from the tested QRhi child.
Forced first-upload failures must record `video_gpu_upload_retry`, remain on the
opaque background, and emit `presentation_resumed` only after the retained
current-generation frame uploads and draws successfully on retry.

The default timeout is 30 minutes. Override it with `-MaxMinutes 60` if the scan takes longer.
The expanded directory is retained beside the ZIP so its contents can be reviewed before
sharing.

## Bundle contents

- `detail_events.jsonl`: decode, presentation, render-session, Edit/fullscreen, and selection events.
- `stderr.log`: privacy-safe performance events and Qt graphics/multimedia diagnostics.
- `runtime_stacks.log`: all Python thread stacks every five seconds.
- `process_metrics.csv`: memory, handles, threads, GDI/USER objects, and hung-window state.
- `reproduction_markers.jsonl`: application lifecycle and the user-entered `R` marker.
- `system.json`: OS, GPU driver, memory, launcher mode, application hash, and source revision.
- `windows_application_events.json`: bounded warning/error events generated during the run.
- `app-logs/`: normal rotating iPhoto logs redirected into this session.

Before sharing, the ZIP may be opened and reviewed. Do not edit files in the expanded directory
after the ZIP is created unless the ZIP is regenerated, because `manifest.json` records hashes.
