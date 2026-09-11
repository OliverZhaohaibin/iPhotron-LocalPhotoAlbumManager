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

For the cropped-still cold/warm comparison, start a fresh process, open the
target once, and press `F` after it is visible. Navigate to another asset,
reopen the target without exiting, and press `W`. After the collector creates
its ZIP, compare the two first-frame transactions with:

```powershell
python .\tools\analyze_windows_crop_framing.py `
  "$HOME\Desktop\iPhoto-windows-scan-playback-<timestamp>.zip"
```

The analyzer requires matching target size, cover, zoom and effective scale;
pan may differ by at most one device pixel and each crop-center error must be
within one logical pixel.

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

Fullscreen traces must keep one transaction id from
`fullscreen_enter_requested` through `fullscreen_native_state_confirmed` and
`fullscreen_updates_resumed`. Updates must resume as soon as the synchronous
chrome/layout/backdrop mutation and `showFullScreen()` call return; they must
not remain disabled while Windows completes the asynchronous native state
transition. After `fullscreen_native_state_confirmed`, the trace must record
`fullscreen_first_frame_requested` and then either a matching active image/video
`fullscreen_first_frame_submitted`, or `fullscreen_first_frame_timeout` after
the bounded 500 ms fail-open. That terminal releases the fullscreen LOD gate
and only then starts the playback-resume delay. `fullscreen_update_requested`
remains useful supporting evidence but is not a media-frame fence and does not
finish the transaction. A preflight failure resumes suspended playback and
re-raises without creating a window transaction; an in-transaction
preparation/native failure instead emits `fullscreen_enter_rollback`, restores
painting, and cancels the gate.

Also test a playing video with `enter→exit→enter→exit`, keeping every interval
below 120 ms. Stale resume callbacks must not play during an intermediate state;
the newest callback must restore playback exactly once after the final exit.

For fullscreen still zoom, correlate `still_zoom_changed` with actual LOD
threshold crossings. A promotion must record
`lod_upgrade_requested→lod_upgrade_staging→lod_upgrade_resident→lod_upgrade_activated`
and only reach `lod_upgrade_presented` after the matching window submission.
During staging the old LOD remains active; a failed or stale promotion may add
an inactive resident entry but must not change the active key or render-session
surface. Run unedited, exposure-only, curve-only, crop, straighten, perspective,
and crop-plus-colour samples at 100%, 125%, and 150% DPI.
`lod_upgrade_cancelled` must include the cancelled phase and reason. A live edit
made between staging and activation must remain visible after activation; an
allocation failure or cancelled submission must leave the controller surface,
session surface, and decode level on the last matching submitted LOD.
Wheel input must record `lod_activation_held` for a queued/staging/resident
promotion and release it only after final idle planning. If the promotion is
already activating, `lod_plan_waiting_for_submission` must be followed by its
matching presentation and `lod_superseded_after_submit`; the normal path must
not visibly return to the previous LOD. A 250 ms missing-submission fallback may
restore the last composed key, but `lod_rollback_frame_submitted` must precede
new staging. `lod_rollback_failed` must preserve the currently drawable texture
and freeze further LOD work instead of exposing the backdrop.
The fullscreen gate must prevent decode, staging, and activation until the
committed active surface has submitted once at the authoritative fullscreen
target. Its release schedules a 16 ms resize evaluation; wheel zoom uses a
180 ms idle evaluation, and an already pending zoom intent is not replaced by
resize. `lod_plan_reused`, `lod_plan_cancelled`, and `lod_plan_submitted` show
whether desired-key planning avoided a redundant generation or rollback.
Initial cropped still traces must include
`still_first_frame_transform_committed` before their first matching submission;
there must be no post-submit framing correction. Cold GPU miss and warm resident
reopen must report the same cover, zoom, effective scale and pan. The matching
`gpu_upload` must preserve the pre-draw cover/effective scale; any
`still_first_frame_cover_drift` is a failed run.

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
