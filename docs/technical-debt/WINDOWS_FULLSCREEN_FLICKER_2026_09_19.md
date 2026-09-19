# Windows fullscreen black flicker: 2026-09-19 diagnostic findings

Status: unresolved on the reported Windows compositor. This report narrows the
failure boundary; it does not claim a verified driver defect or a completed fix.

## Evidence

Input: `iPhoto-windows-scan-playback-20260919-003026`, source revision `6adeaf6b`.
Windows 11 build 26200, Qt/PySide 6.10.1, Intel Iris Xe OpenGL 4.6 compatibility
context, Intel driver 32.0.101.7088. The machine also lists an NVIDIA GPU, but the
Qt graphics log identifies Intel as the active renderer. Screen DPR is 2.5:
1536×960 logical, 3840×2400 physical. The main window is translucent/frameless
with an 8-bit alpha buffer and requested/obtained swap interval 1.

Times below use the application's local UTC+02:00 timestamps:

| Time | Observation |
|---|---|
| 00:30:56.082 | Full-resolution 4538×3025 still already presented. |
| 00:30:57.624 | Fullscreen target becomes 3800×2360. |
| 00:30:57.827 | Fullscreen draw logged with normalized GL state and no sampled GL errors. |
| 00:31:02.466 | Next `frameSubmitted`: 4.639 seconds after that draw. |
| 00:31:05.340–05.379 | Three wheel events; zoom advances from 1.2164 to 1.6190. |
| 00:31:05.505 | One coalesced draw using the same texture and target. |
| 00:31:05.524 | Submission about 19 ms after the wheel-triggered draw. |
| 00:31:07.818 | Logs first show windowed state during fullscreen exit. |
| 00:31:10.270 | Windowed fit requested; subsequent draws restore zoom 1.2164. |
| 00:31:11.963 | User's `problem_reproduced` marker, after leaving fullscreen. |

During fullscreen and the wheel sequence, cover stays at 1.396828937393248;
there is no new decode/upload, suppression/reveal transition, resource rebuild,
render failure or upload failure. `level_selected=full` is a demand check, not
evidence of a fresh decode: the full texture was already resident. All 14
recorded GL draw samples have no sampled GL errors and the expected viewport,
write mask and disabled blend/depth/stencil/cull/scissor tests. This does not
prove pixel correctness; the bundle contains no screen or framebuffer pixels.

The long draw/submission gap and GUI heartbeat gap warrant investigation of
Qt/WGL/window composition. A Python stack in `app.exec()` cannot identify the
native blocking function. Do not equate this gap with proven `SwapBuffers`
blocking, or infer an application repaint storm from the user's visible flicker.

## Collection limitations found

`runtime_stacks.log` identifies GUI PID 7276, while all 179 metrics rows and the
marker use PID 18284 (a launcher). Its near-zero CPU, 10 MB memory and absent
main HWND do not describe the GUI and must not be used to rule out stalls.
The resolver previously returned the first live child, overlooking nested
Windows venv launchers. It now traverses descendants and requires either the
GUI runtime header's PID with verified ancestry or a process owning a window;
unresolved identity fails explicitly.

The provided directory lacks `manifest.json` and `windows_application_events.json`.
Treat it as a usable but incomplete collection, not a finalized checked bundle.
Close the application and wait for the collector to print the ZIP path before
sharing the next run.

## Next discriminating checks (OpenGL only)

The remaining leading hypothesis is the fullscreen window/compositor path,
including translucent top-level composition or WGL presentation on the active
Intel adapter. This remains a hypothesis. Qt documents a Windows fullscreen
OpenGL/DWM limitation and a native `WS_BORDER` workaround for top-level window
composition; that documentation does not establish that this exact black flicker
has the same cause: [Qt Windows issues](https://doc.qt.io/qt-6/windows-issues.html#fullscreen-opengl-based-windows).

The independent pixel probe now requests the same default GL version/profile as
production (it previously forced 3.3 core, whereas this report uses compatibility).
Run a matching baseline, then independently vary top-level translucency and
requested swap interval. Commands are in the collection runbook. These options
do not change production defaults. A successful probe does not replace an actual
application reproduction; it uses a smaller widget hierarchy and synthetic media.

- Baseline fails, opaque succeeds: prioritize the translucent top-level/native
  fullscreen combination. Test a scoped composition workaround in the application
  before changing defaults or rebuilding any native surfaces.
- Baseline fails, interval 0 succeeds: prioritize the WGL presentation/vsync path.
  The driver may ignore this request; inspect obtained format and timing.
- All probes pass while the application fails: inspect the actual top-level
  hierarchy/overlays with real compositor sampling and native timing.
- All fail: compare windowed/fullscreen pixels and active GPU before making a
  driver/backend attribution.

No DX backend, driver replacement, or unconditional vsync/transparency change is
introduced by this diagnostic follow-up.

## Follow-up: three probes and external camera recording

All three returned runs (baseline, opaque, requested interval 0) completed
126 pixel samples and failed 9. Every failure is a fullscreen wheel sample;
windowed and pre-wheel idle samples passed. The logs contain no sampled GL
errors. Transparency removal and requested vsync-off were not sufficient fixes.
The old probe only records requested interval, not the driver's effective setting,
so do not conclude vsync was definitively disabled.

The first failed PNG from each run is 3840×2400 with green bounds
`[466,3375) × [110,2291)`. The logged current zoom is 1.0 (expected full-fit
green bounds `[320,3520) × [0,2400)`). Those observed bounds match the preceding
zoom 1/1.1 within approximately one pixel. Thus the screenshot sees stale
presentation despite a newer draw/submission, rather than only a crop-math
error. Failed PNGs contain green content; they are not direct captures of the
black display intervals.

The external-camera recording `IMG_3259.MOV` is 11.67 seconds at 30 fps.
Inspection confirms extended dark-screen intervals interrupted by brief green
content and a windowed desktop near the end. The screen is dark for most of
approximately 0.2–3.8 seconds, with additional dark intervals later. This confirms
the user's visible black flicker independently of screen-grab sampling. It does
not identify the native API blocking point or prove a monitor power/signal fault.

A scoped candidate now implements Qt's documented fullscreen OpenGL `WS_BORDER`
workaround. It is **off by default**, enabled using
`IPHOTO_WINDOWS_FULLSCREEN_BORDER=1`, `-FullscreenBorder` in the collector, or
`--fullscreen-border` in the probe. It modifies only the existing fullscreen
OpenGL HWND's style, reads it back to verify the bit, and lets Qt restore its
saved normal style on exit. It never creates/reparents a surface or changes the
render backend, swap interval or translucency. Verify this candidate on the
reported Windows desktop before promoting it to a production default.
