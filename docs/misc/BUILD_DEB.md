# Building a Debian Package (.deb) for Linux

This document describes how to wrap the current Nuitka standalone Linux build
of iPhotron in a Debian package. Do not hard-code the application version in
this guide: the package version must come from `pyproject.toml`.

## Overview

The `.deb` stage is a packaging wrapper around the standalone bundle produced by
`scripts/build_nuitka_fast.sh`. It must preserve the bundle contents rather than
reconstructing Python dependencies independently.

The current Linux standalone build is generated from `src/entrypoint.py` and is
written below `dist/entrypoint.dist/`. The executable is normally one of:

```text
dist/entrypoint.dist/entrypoint.bin
dist/entrypoint.dist/entrypoint
```

The build script also writes `dist/build-manifest.json`.

Linux packages that claim offline Maps or People/Pets support must preserve the
corresponding staged runtime data from the standalone bundle, including
`maps/tiles/extension/` and `extension/models/`.

## Prerequisites

- Debian/Ubuntu/Mint or another environment with `dpkg-deb`
- the project development/build dependencies required by
  `scripts/build_nuitka_fast.sh`
- a successful standalone build

Build the standalone application first:

```bash
bash scripts/build_nuitka_fast.sh
```

## Resolve Version And Executable

Read the release version from `pyproject.toml` instead of duplicating it:

```bash
VERSION="$(python - <<'PY'
import tomllib
from pathlib import Path
print(tomllib.loads(Path('pyproject.toml').read_text())['project']['version'])
PY
)"

APP_DIST="dist/entrypoint.dist"
if [[ -x "$APP_DIST/entrypoint.bin" ]]; then
  APP_EXECUTABLE="entrypoint.bin"
elif [[ -x "$APP_DIST/entrypoint" ]]; then
  APP_EXECUTABLE="entrypoint"
else
  echo "Nuitka entrypoint not found; run scripts/build_nuitka_fast.sh first" >&2
  exit 2
fi

PKG_ROOT="iPhotron_${VERSION}_amd64"
APP_ROOT="$PKG_ROOT/opt/iPhotron"
BIN_ROOT="$PKG_ROOT/usr/local/bin"
```

For another architecture, change the Debian architecture value and package
suffix together; do not publish an `amd64` package built for another CPU.

## Staging Layout

```text
iPhotron_VERSION_amd64/
├── DEBIAN/
│   └── control
├── opt/
│   └── iPhotron/
│       └── ... complete contents of dist/entrypoint.dist/ ...
└── usr/
    └── local/
        └── bin/
            └── iPhotron
```

Stage the standalone bundle without stripping optional feature assets:

```bash
mkdir -p "$PKG_ROOT/DEBIAN" "$APP_ROOT" "$BIN_ROOT"
cp -a "$APP_DIST/." "$APP_ROOT/"
printf '#!/bin/sh\nexec /opt/iPhotron/%s "$@"\n' "$APP_EXECUTABLE" > "$BIN_ROOT/iPhotron"
chmod 755 "$BIN_ROOT/iPhotron" "$PKG_ROOT/DEBIAN"
```

## Debian Control Metadata

Create `"$PKG_ROOT/DEBIAN/control"` with the resolved version:

```bash
cat > "$PKG_ROOT/DEBIAN/control" <<EOF
Package: iphotron
Version: ${VERSION}
Section: graphics
Priority: optional
Architecture: amd64
Maintainer: OliverZhao
Description: Folder-native local photo album manager
 iPhotron is a local-first photo manager with folder-native albums,
 non-destructive editing, optional People/Pets recognition, and offline maps.
EOF
chmod 644 "$PKG_ROOT/DEBIAN/control"
```

Keep Debian's package identifier stable (`iphotron`) even if the product name is
rendered as `iPhotron` in UI/documentation.

## Validate Optional Runtime Payloads

The Nuitka build already stages application data. The `.deb` step must not
remove it.

For Maps-enabled builds:

```bash
find "$APP_ROOT/maps/tiles/extension" -maxdepth 2 -type f | sort
```

Typical Linux map payloads include:

- `maps/tiles/extension/World_basemap_2.obf`
- `maps/tiles/extension/bin/osmand_render_helper`
- `maps/tiles/extension/bin/osmand_native_widget.so`
- `maps/tiles/extension/bin/libOsmAndCore_shared.so`
- `maps/tiles/extension/bin/libOsmAndCoreTools_shared.so`
- `maps/tiles/extension/search/geonames.sqlite3`

For offline-ready People/Pets builds:

```bash
find "$APP_ROOT" -path '*insightface*' -o -path '*onnxruntime*'
find "$APP_ROOT/extension/models" -type f | sort
find "$APP_ROOT" -path '*torch*' -o -path '*torchvision*' -o -path '*usearch*'
```

`src/extension/models/` is a build-staging input, not guaranteed fresh-clone
content. A release that advertises offline recognition must populate and verify
that staging area before running Nuitka.

## Build And Inspect The Package

```bash
dpkg-deb --build "$PKG_ROOT"
dpkg-deb --info "${PKG_ROOT}.deb"
dpkg-deb --contents "${PKG_ROOT}.deb"
```

Useful payload checks:

```bash
dpkg-deb --contents "${PKG_ROOT}.deb" | grep 'maps/tiles/extension' || true
dpkg-deb --contents "${PKG_ROOT}.deb" | grep 'extension/models' || true
```

## Installation

```bash
sudo apt install ./"${PKG_ROOT}.deb"
```

Or:

```bash
sudo dpkg -i "${PKG_ROOT}.deb"
sudo apt-get install -f
```

## Removal

```bash
sudo apt remove iphotron
```

## Release Smoke Test

Validate the package on a clean target machine, not only from the source tree:

1. launch `iPhotron` from `PATH`;
2. open an existing and a small fresh library;
3. verify Gallery -> Detail still/video opening;
4. verify Maps if the package advertises offline Maps support;
5. open People & Pets and confirm enabled recognition runtimes activate on
   feature use rather than during application startup;
6. if offline recognition is advertised, confirm People/Pets can initialize
   with downloads disabled;
7. create durable recognition state (for example a name/cover), restart, and
   verify that it persists.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `dpkg-deb: error: control directory has bad permissions` | `DEBIAN/` mode is wrong | `chmod 755 "$PKG_ROOT/DEBIAN"` |
| launcher exists but app does not start | launcher points at the wrong Nuitka executable | Re-resolve `entrypoint.bin` / `entrypoint` from `dist/entrypoint.dist/` |
| Maps falls back after install | map extension stripped from standalone bundle | Verify `maps/tiles/extension/` inside `/opt/iPhotron/` |
| native Maps fails with XCB/GLX errors | desktop lacks the expected X11/XWayland GL integration | Install/enable XWayland/XCB GL support or use the helper-backed map path |
| People/Pets unavailable offline | optional runtime or model staging was omitted before Nuitka | Rebuild the standalone bundle with the required runtime and verified models |
| package version disagrees with the application | version was duplicated manually | Regenerate `VERSION` from `pyproject.toml` |
