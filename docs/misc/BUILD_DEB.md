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

Builds that ship the People page with face scanning enabled must also preserve
the packaged AI runtime from the standalone bundle: `insightface`,
`onnxruntime`, and the shared `extension/models` model cache. These are added at
the Nuitka stage described in [`BUILD_EXE.md`](BUILD_EXE.md); the `.deb` stage
must not strip them from `/opt/iPhotron/`.

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
│       ├── iPhotron             ← main executable (name may vary by build)
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

The `DEBIAN/control` file contains the package metadata. Create it with content
like the following:

```
Package: iPhotron
Version: 5.00
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
> | `Version` | `5.00` | Upstream version; update to match your release |
> | `Architecture` | `amd64` | Target CPU architecture (x86-64) |
> | `Maintainer` | `OliverZhao` | Name (and optionally email) of the package maintainer |
> | `Description` | short + long | First line is the synopsis; indented lines form the long description |

## Build Steps

1. **Prepare the staging tree** — copy your compiled iPhotron binary into the correct location inside the staging directory:

   ```bash
   PKG_ROOT=iPhotron_5.00_amd64
   APP_ROOT="$PKG_ROOT/opt/iPhotron"
   BIN_ROOT="$PKG_ROOT/usr/local/bin"
   APP_DIST=dist/YOUR_STANDALONE_DIR
   APP_EXECUTABLE=YOUR_EXECUTABLE_NAME

   mkdir -p "$PKG_ROOT/DEBIAN" "$APP_ROOT" "$BIN_ROOT"
   cp -a "$APP_DIST/." "$APP_ROOT/"
   printf '#!/bin/sh\nexec /opt/iPhotron/%s "$@"\n' "$APP_EXECUTABLE" > "$BIN_ROOT/iPhotron"
   chmod 755 "$BIN_ROOT/iPhotron"
   ```

   Replace `YOUR_STANDALONE_DIR` and `YOUR_EXECUTABLE_NAME` with the actual
   Nuitka output names produced by your Linux build.

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

   If this release includes offline-ready People scanning, also verify the face
   runtime payload from the Nuitka bundle:

   ```bash
   find "$APP_ROOT" -path '*insightface*' -o -path '*onnxruntime*'
   find "$APP_ROOT/extension/models" -name 'det_500m.onnx' -o -name 'w600k_mbf.onnx'
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
   # If this build ships offline-ready People scanning:
   dpkg-deb --contents "${PKG_ROOT}.deb" | grep 'extension/models'
   ```

   After installing on a clean test machine, open a small image library and
   verify that the People page can create face clusters. For a fuller smoke
   test, name a person, set a cover, create a group, restart iPhotron, and
   confirm those user decisions persist.

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
| People scan is unavailable in the installed app | The standalone build was produced without the optional face runtime | Rebuild the standalone app with `insightface`, `onnxruntime`, and `src/extension/models` included before staging the `.deb` |
| People scan starts but never creates clusters | The model cache or an InsightFace submodel/dependency is missing from `/opt/iPhotron/` | Verify `extension/models`, exclude unused `albumentations`/`pydantic` packages at the Nuitka stage, and keep InsightFace limited to detection and recognition |
