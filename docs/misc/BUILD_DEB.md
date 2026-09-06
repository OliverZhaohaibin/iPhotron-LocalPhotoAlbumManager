# Building a Debian Package (.deb) for Linux

This document describes how to build a Debian package (`.deb`) for iPhotron on Linux.

## Overview

A `.deb` package allows easy installation and removal on Debian-based
distributions (Ubuntu, Mint, etc.) using standard package management tools
such as `apt` and `dpkg`.

For iPhotron, Linux packaging should preserve the standalone application bundle
and the offline maps extension together. The Location view's native Linux maps
runtime depends on the helper binary plus the shared libraries under
`maps/tiles/extension/bin/`.

Builds that ship People/Pets recognition must also preserve the selected AI
runtimes from the standalone bundle. People needs `insightface` and
`onnxruntime`; Pets needs `onnxruntime`, `torch`, `torchvision`, `usearch`, and
`certifi`. Offline builds also retain any explicitly staged
`extension/models` artifacts.
These are added at the Nuitka stage described in
[`BUILD_EXE.md`](BUILD_EXE.md); the `.deb` stage must not strip them from
`/opt/iPhotron/`.

## Prerequisites

- A Debian-based Linux distribution (Ubuntu, Debian, Mint, …)
- `dpkg-deb` (usually pre-installed on Debian/Ubuntu systems)
- A working standalone build of iPhotron (see [`BUILD_EXE.md`](BUILD_EXE.md))

## Directory Structure

Create a staging directory that keeps the standalone bundle intact and exposes
an `iPhotron` launcher on `PATH`:

```
iPhotron_VERSION_amd64/
├── DEBIAN/
│   └── control
├── opt/
│   └── iPhotron/                 ← standalone app bundle copied here
│       ├── entrypoint.bin       ← default Linux Nuitka executable
│       └── maps/
│           └── tiles/
│               └── extension/
│                   ├── World_basemap_2.obf
│                   ├── misc/
│                   ├── poi/
│                   ├── rendering_styles/
│                   ├── routing/
│                   ├── search/
│                   │   └── geonames.sqlite3
│                   └── bin/
└── usr/
    └── local/
        └── bin/
            └── iPhotron          ← launcher script
```

## The `control` File

Read the release version from `pyproject.toml` once and use the expanded value
for both the staging directory and package metadata:

```bash
VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
PKG_ROOT="iPhotron_${VERSION}_amd64"
```

The `DEBIAN/control` file contains the package metadata. Create it with the
expanded value of `VERSION` rather than a hard-coded release number. The
`${VERSION}` marker below means the value printed by the command above; do not
write the marker literally into the final control file.

```
Package: iPhotron
Version: ${VERSION}
Section: graphics
Priority: optional
Architecture: amd64
Maintainer: OliverZhao
Description: Folder-native local photo album manager
 iPhotron is a folder-native photo manager inspired by macOS Photos.
 It organizes media using lightweight JSON manifests and provides rich
 album functionality while keeping destructive edits out of original media.
```

> **Fields explained**
>
> | Field | Value | Notes |
> |-------|-------|-------|
> | `Package` | `iPhotron` | Binary package name |
> | `Version` | `${VERSION}` | Exact upstream version read from `pyproject.toml` |
> | `Architecture` | `amd64` | Target CPU architecture (x86-64) |
> | `Maintainer` | `OliverZhao` | Name (and optionally email) of the package maintainer |
> | `Description` | short + long | First line is the synopsis; indented lines form the long description |

## Build Steps

1. **Prepare the staging tree** — copy the maintained Linux standalone into the correct location inside the staging directory:

   ```bash
   VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
   PKG_ROOT="iPhotron_${VERSION}_amd64"
   APP_ROOT="$PKG_ROOT/opt/iPhotron"
   BIN_ROOT="$PKG_ROOT/usr/local/bin"
   APP_DIST=dist/entrypoint.dist
   APP_EXECUTABLE=""

   for candidate in entrypoint.bin entrypoint; do
     if [ -x "$APP_DIST/$candidate" ]; then
       APP_EXECUTABLE="$candidate"
       break
     fi
   done
   test -n "$APP_EXECUTABLE" || {
     echo "entrypoint.bin/entrypoint not found in $APP_DIST" >&2
     exit 2
   }

   mkdir -p "$PKG_ROOT/DEBIAN" "$APP_ROOT" "$BIN_ROOT"
   cp -a "$APP_DIST/." "$APP_ROOT/"
   printf '#!/bin/sh\nexec /opt/iPhotron/%s "$@"\n' "$APP_EXECUTABLE" > "$BIN_ROOT/iPhotron"
   chmod 755 "$BIN_ROOT/iPhotron"
   ```

   `scripts/build_nuitka_fast.sh` produces `dist/entrypoint.dist`; the loop
   accepts the two executable names used by supported Nuitka versions.

   Before continuing, verify that the maps extension is still present inside
   the staged app bundle:

   ```bash
   find "$APP_ROOT/maps/tiles/extension" -maxdepth 2 -type f | sort
   ```

   At minimum, Linux native maps should retain:

   - `maps/tiles/extension/World_basemap_2.obf`
   - `maps/tiles/extension/bin/osmand_render_helper`
   - `maps/tiles/extension/bin/osmand_native_widget.so`
   - `maps/tiles/extension/bin/libOsmAndCore_shared.so`
   - `maps/tiles/extension/bin/libOsmAndCoreTools_shared.so`
   - `maps/tiles/extension/search/geonames.sqlite3`

   If this release includes offline-ready People/Pets scanning, also verify the
   recognition runtime payload from the Nuitka bundle:

   ```bash
   find "$APP_ROOT" -path '*insightface*' -o -path '*onnxruntime*'
   find "$APP_ROOT/extension/models" -name 'det_500m.onnx' -o -name 'w600k_mbf.onnx'
   find "$APP_ROOT" -path '*torch*' -o -path '*torchvision*' -o -path '*usearch*'
   find "$APP_ROOT/extension/models/pets" -name 'yolox_nano_coco.onnx' -o -name 'dinov2_vits14.pt'
   ```

2. **Create the `control` file** — save the content from the section above to `"$PKG_ROOT/DEBIAN/control"` and ensure it is not world-writable:

   ```bash
   chmod 644 "$PKG_ROOT/DEBIAN/control"
   ```

3. **Build the package**:

   ```bash
   dpkg-deb --build "$PKG_ROOT"
   ```

   This produces `"${PKG_ROOT}.deb"` in the current directory.

4. **Verify the package**:

   ```bash
   dpkg-deb --info "${PKG_ROOT}.deb"
   dpkg-deb --contents "${PKG_ROOT}.deb"
   dpkg-deb --contents "${PKG_ROOT}.deb" | grep 'maps/tiles/extension'
   # If this build ships offline-ready People/Pets scanning:
   dpkg-deb --contents "${PKG_ROOT}.deb" | grep 'extension/models'
   ```

   After installing on a clean test machine, open a small image library and
   verify that the People & Pets page can create each enabled cluster type. For
   a fuller smoke test, name an identity, set a cover, create a group, restart
   iPhotron, and confirm those user decisions persist.

## Installation

Install the generated package with:

```bash
sudo apt install ./"${PKG_ROOT}.deb"
```

If you open a new shell before installing, replace `${PKG_ROOT}` with the real
package directory name you used in Step 1.

Or using `dpkg` directly (and then resolving any missing dependencies):

```bash
sudo dpkg -i "${PKG_ROOT}.deb"
sudo apt-get install -f   # fix missing dependencies if any
```

## Removal

```bash
sudo apt remove iPhotron
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `dpkg-deb: error: control directory has bad permissions` | `DEBIAN/` directory not mode 755 | `chmod 755 "$PKG_ROOT/DEBIAN"` |
| `dpkg: dependency problems` after install | Missing runtime libraries | Add `Depends:` line to `control` listing required packages |
| Binary not found after install | Wrong install path in staging tree, or launcher points to the wrong standalone executable | Ensure the launcher under `usr/local/bin/` points to the executable copied into `/opt/iPhotron/` |
| Location view falls back unexpectedly after install | `maps/tiles/extension/` was not included in the package | Re-stage the standalone bundle and verify the `.deb` contents include `World_basemap_2.obf`, resources, and Linux map binaries |
| Native maps fail with GLX/XCB startup errors | The runtime was installed correctly, but the desktop session lacks XWayland/XCB GL integration | Install/enable XWayland and rerun, or set `IPHOTO_PREFER_OSMAND_NATIVE_WIDGET=0` to force the helper-backed Python OBF path |
| People scan is unavailable in the installed app | The standalone build was produced without the optional face runtime or offline model staging | Rebuild with `insightface` and `onnxruntime`; if offline People support is promised, explicitly stage `src/extension/models` before building the standalone |
| People scan starts but never creates clusters | The model cache or an InsightFace submodel/dependency is missing from `/opt/iPhotron/` | Verify `extension/models`, exclude unused `albumentations`/`pydantic` packages at the Nuitka stage, and keep InsightFace limited to detection and recognition |
| Pets scan is unavailable in the installed app | The standalone build omitted `pets-ai` packages or `extension/models/pets` | Rebuild the standalone app with `onnxruntime`, `torch`, `torchvision`, `usearch`, `certifi`, and both Pets model files before staging the `.deb` |
