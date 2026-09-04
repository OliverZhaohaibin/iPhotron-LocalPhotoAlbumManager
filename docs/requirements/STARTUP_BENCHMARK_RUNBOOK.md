# Startup benchmark runbook

This runbook is the long-lived executable evidence protocol for startup
performance and lifecycle regressions. It follows the current cross-platform
QRhi startup contract in [`docs/architecture.md`](../architecture.md). Raw
profiles and process output may contain machine-specific diagnostics, so write
them below `benchmark-output/` and do not commit them.

## Evidence contract

- Choose an accepted release or commit as the baseline and record both exact
  revisions. Baseline and candidate must both implement the unified lifecycle:
  the hidden Detail shell and all QRhi surfaces have final native parents before
  top-level `show()`, while Detail chrome and multimedia complete after first
  paint. A revision with the former platform-split surface lifecycle is not an
  eligible baseline for this gate.
- Build the two revisions in separate worktrees with the same Python, locked
  dependencies, Nuitka version, build flags, native map runtime, and assets.
- Use the build script generated `build-manifest.json` for every packaged run.
  Collection rejects a manifest whose source revision or executable SHA does
  not match the launched command; comparison rejects different environment
  fingerprints.
- The build-manifest generator, profiler schema, and build-script emission must
  be identical in both worktrees. If observability must be backported, limit the
  change to event emission and record the patch; do not alter lifecycle or work
  scheduling behavior.
- Each platform/scenario/revision pair requires 30 cold and 30 hot runs.
- A cold run is formal evidence only when an actual OS file-cache eviction
  method is recorded and `--confirm-controlled-cold-cache` is supplied. Merely
  deleting iPhotron thumbnails is not a cold packaged startup and is forbidden
  for the indexed-library scenario.
- Use a dedicated, backed-up benchmark library with a current index and usable
  thumbnail cache. Never clear or mutate a user's production library.

The profiler writes the canonical `app_created`, `show`, `first_paint`,
`interactive`, `library_ready`, `first_gallery_visible`, and
`first_usable_thumbnail` milestones defined by `tools/startup_benchmark.py`.
Every generation has exactly one terminal event. Sensitive path fields are
replaced by a stable, per-install salted identifier at the JSONL writer
boundary.

Packaged lifecycle validation must also confirm that no QRhi surface is created
or reparented after `show()`. Failure to prepare the pre-show native hierarchy
is terminal for that process; a retry may restart library startup only after a
valid visible shell and must not claim to rebuild the native hierarchy.

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
  -- /absolute/iPhotron.app/Contents/MacOS/iPhotron
```

For the OpenGL compatibility path, use `--graphics-backend opengl`. For
controlled cold samples, perform the approved cache eviction before every run
and add both `--cache-eviction-method METHOD` and
`--confirm-controlled-cold-cache`. The tool records but intentionally does not
elevate privileges or purge caches itself.

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
  --app /absolute/iPhotron.app \
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
Intel. Cross-platform validation must not be claimed until those reports and
the manual native-surface, offline-storage, and map-degradation checks have
been collected on their target platforms.
