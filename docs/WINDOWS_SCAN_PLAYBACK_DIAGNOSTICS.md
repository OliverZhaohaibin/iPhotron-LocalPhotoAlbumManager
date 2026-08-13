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
