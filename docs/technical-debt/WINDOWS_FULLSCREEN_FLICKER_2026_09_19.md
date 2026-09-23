# Windows fullscreen black flicker: 2026-09-19 diagnostic findings

Status: user-confirmed visual recovery on the reported machine when windowed
fullscreen is enabled. The 2026-09-20 launcher clarification below identifies why
older ordinary PyCharm launches did not enable it. A general driver root cause
and cross-device validation remain unresolved. Earlier sections are chronology.

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
zoom 1/1.1 within approximately one pixel. However, PR review identified that the
probe used unsupported layered-HWND capture. The earlier inference of proven
stale compositor presentation from these bounds is withdrawn. These PNGs and
their 9/126 counts are only diagnostic artifacts and cannot establish compositor
correctness/failure. They are not direct captures of the black display intervals.

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

## Follow-up: verified border still fails

The returned `probe-border` run verifies the native bit 18 times and records nine
`fullscreen_composition_border(applied=true)` events. It still fails 9/126
samples, all during fullscreen wheel zoom, with the same stale-size bounds as
the other runs, subject to the unsupported-capture limitation above. The user's
independent report of continued visible flicker, rather than that pixel count,
is the basis for retaining border as an ineffective candidate on this machine.

Internet research found a closely related original Qt report: a frameless
OpenGL window flickered at exactly fullscreen dimensions but not when its
geometry differed by one pixel. The reply describes a pseudo-fullscreen
workaround. This is an old Qt 5 report, not confirmation of a Qt 6.10/Intel bug:
[original Qt forum report](https://forum.qt.io/topic/68132/flicker-with-qopenglwidget-when-fullscreen-and-frameless-window).
The linked QTBUG-51093 tracker was unavailable during this investigation; its
resolution/version status has not been verified. Intel's generic flicker
troubleshooting guidance is not evidence of this specific root cause.

The new candidate is **windowed fullscreen**, opt-in via
`IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN=1`. It keeps the original frameless
QWidget/HWND/QRhi hierarchy and uses ordinary window geometry covering the
screen plus one logical pixel of height. It never enters Qt's native fullscreen
state in this mode. Application fullscreen semantics are represented explicitly
so double-click, Escape routing, Playback reconciliation and Edit still work.
Saved normal/maximized state is restored on exit; minimized windows are not
repositioned, and rejected resize attempts are bounded. At DPR 2.5, one logical
pixel becomes approximately 2–3 device pixels. The off-screen strip may extend
onto an adjoining display, which is part of multi-monitor acceptance testing.

This addresses exact-monitor-sized presentation as the next specific hypothesis,
without changing shaders, decoding, GPU API, transparency or swap interval.
The synthetic probe uses the same enter/exit functions as Playback and Edit,
and only evaluates screenshot pixels visible on the selected monitor. Native
fullscreen is expected to be false while logical fullscreen is true. Local Qt
tests confirm repeated geometry/state restoration and native-handle retention;
Windows pixel and taskbar/Alt-Tab validation remains required.

## 2026-09-20 clarification: collector launch vs PyCharm Run

The user clarified that the successful run was specifically launched with
`-Scenario Fullscreen -FullscreenOverscan`. Direct PyCharm Run of the same
interpreter and revision still flickered. In revision `14dcd388`, that switch
sets `IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN=1` in the collector's child environment;
`_use_windowed_fullscreen()` otherwise returns false. An unset PyCharm override
therefore selects a different full-screen implementation even with identical
Python packages and source code. The IDE's actual environment has not been read;
this is a concrete code-level explanation to verify with its strategy log,
not evidence that PyCharm itself breaks rendering.

The production default is now windowed fullscreen for Windows OpenGL.
Unset/empty/`auto` select it; explicit `0` retains native mode for rollback and
comparisons. Other OS/backends are unaffected. The collector's default Fullscreen
scenario follows this policy, and `-NativeFullscreen` selects the prior native
control. Ordinary logs expose the selected strategy without requiring profiler
flags. The independent pixel probe still has an explicit native baseline.

On older revisions, setting `IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN=1` in the actual
PyCharm Run configuration is sufficient to select the same fullscreen path as
the successful collector run. Confirmation should use that Run process rather
than another collector launch. If the logged strategy matches and symptoms
remain different, inspect the remaining IDE process configuration then.

This update does not diagnose or fix the separately documented filmstrip
access-violation incident.

## PR #931 review follow-up

Desktop-region capture replaces layered-HWND capture, with per-screen coordinate
conversion/DPR and explicit capture-error outcomes. The corrected probe needs a
new interactive Windows run; historical automated counts are not a substitute.
External camera evidence and the user's configuration-specific feedback are not
invalidated by the capture bug.

Overscan rejection now terminates after three bounded attempts and falls back
once to native fullscreen. If that request also fails, the saved window state
and chrome are restored. The user explicitly selected native fallback despite
its known flicker risk. Edit now reconciles system-driven exits/maximization and
does not normalize a window the OS just maximized. These changes require native
Windows verification in addition to their local state-machine tests.
