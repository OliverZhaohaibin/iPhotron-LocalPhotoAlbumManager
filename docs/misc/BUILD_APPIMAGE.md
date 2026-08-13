# Building an AppImage for Linux

This guide documents the current AppImage wrapper in
`scripts/build_appimage.sh`. The AppImage stage consumes an existing Nuitka
standalone directory; it does not compile iPhotron itself.

## Prerequisites

- Linux
- a successful standalone Nuitka build
- `appimagetool` in `PATH`, or an explicit `--appimagetool PATH`
- a PNG application icon

Build the standalone application first:

```bash
bash scripts/build_nuitka_fast.sh
```

The default standalone directory is:

```text
dist/entrypoint.dist/
```

## Required Standalone Payload

Before creating the AppImage, `scripts/build_appimage.sh` validates that the
standalone bundle contains:

- an executable named `entrypoint.bin`, `entrypoint`, `main.bin`, or `main`;
- at least one compiled Qt shader (`*.qsb`);
- `maps/tiles/`;
- a native OsmAnd render helper under `maps/tiles/extension/bin/`;
- Qt's XCB platform plugin (`platforms/libqxcb.so`).

The script intentionally fails instead of producing an AppImage that advertises
an incomplete Linux runtime.

If the release advertises offline People/Pets recognition, populate and verify
the model staging input before the Nuitka step. `src/extension/models/` is a
build-staging location and is not guaranteed to exist in a fresh source clone.

## Build Command

Resolve the project version from `pyproject.toml`:

```bash
VERSION="$(python - <<'PY'
import tomllib
from pathlib import Path
print(tomllib.loads(Path('pyproject.toml').read_text())['project']['version'])
PY
)"
```

Then run:

```bash
scripts/build_appimage.sh \
  --standalone-dir dist/entrypoint.dist \
  --icon path/to/iphoto.png \
  --output "dist/iPhotron-${VERSION}-x86_64.AppImage"
```

Use `--appimagetool /absolute/path/to/appimagetool` if it is not in `PATH`.
The environment variable `APPIMAGETOOL` is also supported.

For a non-default Python executable used by the build-manifest step:

```bash
PYTHON_BIN=/path/to/python3 scripts/build_appimage.sh ...
```

## AppDir Layout

The wrapper creates a temporary sibling directory named `iPhotron.AppDir`:

```text
iPhotron.AppDir/
├── AppRun
├── iphoto.desktop
├── iphoto.png
└── usr/
    └── bin/
        └── ... complete Nuitka standalone bundle ...
```

`AppRun` and the desktop file come from `packaging/appimage/`.

The script refuses to overwrite an existing `iPhotron.AppDir`. Remove or archive
an old staging directory before rebuilding.

## Output And Build Manifest

The requested AppImage is written exactly to `--output`. A companion build
manifest is generated next to it:

```text
<output>.build-manifest.json
```

The manifest records the AppImage artifact, build driver, architecture, native
Maps runtime, map assets, and i18n resources.

## Verification

Make the artifact executable if required and launch it on a clean Linux target:

```bash
chmod +x "dist/iPhotron-${VERSION}-x86_64.AppImage"
"dist/iPhotron-${VERSION}-x86_64.AppImage"
```

At minimum verify:

1. first window and Gallery become usable without startup crashes;
2. Gallery -> Detail opens stills and videos;
3. Qt XCB startup works on the supported X11/XWayland environment;
4. offline Maps loads when advertised;
5. People & Pets recognition does not initialize merely because the app starts;
6. opening the recognition feature activates the enabled recognition runtime;
7. an offline-recognition release can initialize with model auto-downloads
   disabled;
8. translations and QSB-backed rendering assets load from the packaged bundle.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `appimagetool not found` | tool is not installed/in `PATH` | install it or pass `--appimagetool PATH` |
| entrypoint validation fails | wrong `--standalone-dir` or failed Nuitka build | point to `dist/entrypoint.dist` from a successful build |
| QSB validation fails | shader data was omitted by Nuitka | rebuild with the current `scripts/build_nuitka_fast.sh` |
| Maps validation fails | standalone Maps payload incomplete | restore `src/maps/tiles` staging and rebuild Nuitka |
| XCB validation fails | Qt platform plugins were stripped | rebuild with the current PySide6/Nuitka plugin options |
| script refuses `iPhotron.AppDir` | previous staging directory exists | remove/archive it before rerunning |
| People/Pets unavailable offline | optional AI runtime/models were not staged before Nuitka | rebuild with the required optional dependencies and verified models |
