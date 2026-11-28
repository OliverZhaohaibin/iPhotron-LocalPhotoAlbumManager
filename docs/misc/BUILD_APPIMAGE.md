# Building the Linux AppImage

This is the canonical procedure for turning the maintained Linux Nuitka
standalone into the AppImage used for release and startup validation.

## Prerequisites

- Linux x86-64 with the project build environment installed.
- A synchronized Maps extension under `src/maps/tiles/extension/`.
- `appimagetool` on `PATH`, or its path supplied with `--appimagetool`.

## Build

Create the directory-based standalone first:

```bash
bash scripts/build_nuitka_fast.sh
```

The maintained output is `dist/entrypoint.dist`, with `entrypoint.bin` or
`entrypoint` as its executable. Package it with:

```bash
bash scripts/build_appimage.sh \
  --standalone-dir dist/entrypoint.dist \
  --output dist/iPhotron-x86_64.AppImage
```

The builder copies the repository's `logo.png` to `iphoto.png` in the AppDir.
Pass `--icon <path-to.png>` to use a different PNG.

The builder refuses to continue unless the standalone contains:

- an executable entry point;
- QRhi `.qsb` shaders;
- `maps/tiles` and the native OsmAnd render helper; and
- Qt's `libqxcb.so` platform plugin;
- the ONNX Runtime native binding and no forbidden Torch/default CUDA payload.

It also refuses to overwrite an existing sibling `iPhotron.AppDir`. The output
is accompanied by `dist/iPhotron-x86_64.AppImage.build-manifest.json`, which
records the source revision, artifact hash, build driver, dependency
fingerprint, native Maps runtime, and required assets. A separate
`.recognition-artifact.json` records measured release/component sizes and the
Torch-free native contract.

The ignored `src/extension/models` staging directory is optional. A clean
checkout produces an AppImage without bundled recognition models. If an
offline-recognition artifact is required, stage and validate the models before
running `build_nuitka_fast.sh` and set `IPHOTO_RECOGNITION_BUNDLE_MODELS` to
the verified v2 root, then verify them inside
`dist/entrypoint.dist/extension/models` before packaging.

## Verification

```bash
chmod +x dist/iPhotron-x86_64.AppImage
dist/iPhotron-x86_64.AppImage
```

On a real Linux host, smoke-test both an explicitly selected XCB session and a
native Wayland session. Confirm that Gallery and Detail open, video playback is
usable, Maps uses the native/helper path when available, and a Wayland session
without XWayland degrades Maps without terminating the main application.
iPhotron must not set `QT_QPA_PLATFORM=xcb` on behalf of a Wayland session.

Run the structural regression test with:

```bash
.venv/bin/python -m pytest -q tests/test_appimage_packaging.py
```

Use [`STARTUP_BENCHMARK_RUNBOOK.md`](../requirements/STARTUP_BENCHMARK_RUNBOOK.md)
and [`STARTUP_MANUAL_VALIDATION_MATRIX.md`](../requirements/STARTUP_MANUAL_VALIDATION_MATRIX.md)
for release performance evidence.
