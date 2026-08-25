# Startup benchmark runbook

This runbook is the executable evidence protocol for the remaining Phase 2/3
startup gates. Raw profiles and process output may contain machine-specific
diagnostics, so write them below `benchmark-output/` and do not commit them.

## Evidence contract

- The pre-optimization baseline is commit `6ff592f7`; the candidate is the
  current startup optimization branch.
- Build the two revisions in separate worktrees with the same Python, locked
  dependencies, Nuitka version, build flags, native map runtime, and assets.
- Use the build script generated `build-manifest.json` for every packaged run.
  Collection rejects a manifest whose source revision or executable SHA does
  not match the launched command; comparison rejects different environment
  fingerprints.
- Apply only the startup-profile observability changes to the baseline. Do not
  backport the startup queue, probe, coordinator, or lazy-loading changes.
  The build-manifest generator and build-script emission are evidence tooling
  and must be identical in both worktrees.
- Each platform/scenario/revision pair requires 30 cold and 30 hot runs.
- A cold run is formal evidence only when an actual OS file-cache eviction
  method is recorded and `--confirm-controlled-cold-cache` is supplied. Merely
  deleting iPhotron thumbnails is not a cold packaged startup and is forbidden
  for the indexed-library scenario.
- Use a dedicated, backed-up benchmark library with a current index and usable
  thumbnail cache. Never clear or mutate a user's production library.

The profiler writes canonical milestones for application creation, window
show, interactivity/degradation, library readiness, first Gallery publication,
and the first thumbnail update published to the Qt model. Every generation has
exactly one terminal event. Sensitive path fields are replaced by a stable,
per-install salted identifier at the JSONL writer boundary.

## Collection

The command to launch the application follows `--`. Point packaged macOS runs
at the executable inside the app bundle rather than using `open`, so the runner
can provide an isolated environment and wait for normal benchmark shutdown.

Apple Silicon, default Metal path:

```bash
.venv/bin/python tools/startup_benchmark.py collect \
  --revision CURRENT_SHA \
  --scenario local-ssd-indexed \
  --runtime packaged \
  --build-manifest dist/build-manifest.json \
  --qt-backend cocoa \
  --graphics-backend metal \
  --cache-state hot \
  --samples 30 \
  --library /absolute/path/to/benchmark-library \
  --confirm-dedicated-library \
  --output-dir benchmark-output/macos-arm64/candidate/metal/hot \
  -- dist/startup-optimized-low-memory-v4/entrypoint.app/Contents/MacOS/iPhotron
```

For the OpenGL compatibility path, use `--graphics-backend opengl`. For
controlled cold samples, perform the approved cache eviction before every run
and add both `--cache-eviction-method METHOD` and
`--confirm-controlled-cold-cache`. The tool records but intentionally does not
elevate privileges or purge caches itself.

### Recognition idle-start evidence

Use the same candidate commit for both arms. The default is the automatic
policy; add `--set-env IPHOTO_STARTUP_RECOGNITION_AUTO_START=0` for the
feature-scoped baseline. Explicit People-page activation remains enabled. Keep
the application alive long enough to observe five seconds after recognition
activation and enable child-process resource sampling:

```bash
.venv/bin/python tools/startup_benchmark.py collect \
  --revision CURRENT_SHA \
  --scenario recognition-auto-models-present \
  --runtime source \
  --qt-backend cocoa \
  --graphics-backend metal \
  --cache-state hot \
  --samples 30 \
  --auto-exit-delay-ms 10000 \
  --timeout-seconds 30 \
  --sample-resources \
  --resource-sample-interval-ms 100 \
  --library /absolute/path/to/dedicated-recognition-library \
  --confirm-dedicated-library \
  --output-dir benchmark-output/recognition/candidate/models-present \
  -- .venv/bin/python -m iPhoto.gui.main
```

Repeat with scenarios `recognition-auto-missing-models` (empty isolated model
root plus `--set-env IPHOTO_PET_MODEL_AUTO_DOWNLOAD=0`),
`recognition-auto-50k-pending` (prepared 50k
status backlog), and `recognition-quick-close` (`--auto-exit-delay-ms 250`).
The JSON/Markdown summary records CPU time, RSS, read bytes, and write bytes at
interactive, recognition activation, activation +1.5 s, and activation +5 s.
First-gallery/thumbnail P50 and P95 must not regress, post-interactive GUI jobs
remain below 100 ms, and quick-close must have no recognition worker start or
late QThread diagnostics.

Windows packaged runs use the same CLI under PowerShell, with the Nuitka `.exe`
after `--`. Keep Defender enabled. Collect separate `local-ssd-indexed`,
`offline-removable`, and `delayed-smb` scenarios and verify after the batch that
no library-probe child remains.

Linux uses the AppImage executable directly and separate XCB, Wayland, and
Wayland-without-XWayland sessions. Build the delivery artifact after the Nuitka
standalone build:

```bash
bash scripts/build_nuitka_fast.sh
bash scripts/build_appimage.sh \
  --standalone-dir dist/entrypoint.dist \
  --icon /absolute/path/to/iphoto.png \
  --output dist/iPhotron-x86_64.AppImage
```

Use `dist/iPhotron-x86_64.AppImage.build-manifest.json` with `collect` for the
AppImage artifact. The maintained macOS and Windows Nuitka scripts write
`build-manifest.json` below their selected output directory.

The AppImage builder refuses bundles without QSB shaders or `maps/tiles` and
refuses to overwrite an existing AppDir. A real Linux host must perform the
XCB/Wayland smoke and performance runs; macOS structure checks are not evidence.

## Aggregation and A/B gate

`collect` writes one JSONL plus stdout/stderr logs per run and creates
`summary.json` and `summary.md`. Existing profiles can be aggregated again:

```bash
.venv/bin/python tools/startup_benchmark.py summarize \
  --output-dir benchmark-output/macos-arm64/candidate/metal/hot \
  benchmark-output/macos-arm64/candidate/metal/hot/run-*.jsonl
```

Generate the macOS package/host detection report after local structure checks
and any available benchmark batches. The report writes JSON and Markdown and
keeps unperformed platform checks as `pending_manual_validation`:

```bash
.venv/bin/python tools/macos_detection_report.py \
  --app dist/startup-optimized-low-memory-v4/entrypoint.app \
  --summary benchmark-output/macos-arm64/candidate/metal/hot/summary.json \
  --output-dir benchmark-output/macos-report
```

For an A/B gate, pass the matching baseline and candidate summaries with
`--baseline` and `--candidate`. A source smoke run is diagnostic only and is
not formal packaged evidence.

Compare matching baseline and candidate summaries:

```bash
.venv/bin/python tools/startup_benchmark.py compare \
  --baseline benchmark-output/macos-arm64/baseline/metal/hot/summary.json \
  --candidate benchmark-output/macos-arm64/candidate/metal/hot/summary.json \
  --output-dir benchmark-output/macos-arm64/comparison/metal/hot
```

The comparison exits nonzero unless all candidate samples are valid and
eligible, `show -> interactive/degraded` is at most 2 seconds, post-interactive
GUI jobs are at most 100 ms, Gallery/thumbnail P50 improves by at least 30%, and
their P95 does not regress. P95 uses nearest-rank calculation.

## Platform completion status

Reports generated on Apple Silicon explicitly retain
`pending_manual_validation` entries for Windows packaged, Linux AppImage, and macOS
Intel. Phase 2/3 must not be marked complete until those reports and the manual
offline-storage/map-degradation checks have been collected on their target
platforms.
