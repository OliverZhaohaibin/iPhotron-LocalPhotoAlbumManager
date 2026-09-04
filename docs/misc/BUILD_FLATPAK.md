# Building the Linux Flatpak Bundle

This is the canonical in-tree procedure for producing the x86-64 single-file
Flatpak bundle published with GitHub releases. It wraps the maintained Nuitka
standalone; it is not a Flathub source-build manifest and must not be presented
as satisfying Flathub submission requirements.

## Prerequisites

- Linux x86-64.
- `flatpak` and `flatpak-builder`.
- The Flathub remote and the `org.freedesktop.Platform` / `org.freedesktop.Sdk`
  25.08 runtime. The build script asks `flatpak-builder` to install missing
  dependencies from Flathub.
- A synchronized and verified `dist/entrypoint.dist` from
  `scripts/build_nuitka_fast.sh`.
- A PNG application icon.

The tracked manifest is
`packaging/flatpak/com.github.OliverZhaohaibin.iPhotron.yml` and the
application ID is `com.github.OliverZhaohaibin.iPhotron`.

## Build

```bash
bash scripts/build_nuitka_fast.sh

VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
bash scripts/build_flatpak.sh \
  --standalone-dir dist/entrypoint.dist \
  --icon /absolute/path/to/iphotron.png \
  --output "dist/com.github.OliverZhaohaibin.iPhotron-${VERSION}-x86_64.flatpak"
```

The builder validates the executable, QRhi shaders, Maps helper, and Qt XCB
plugin before creating a temporary Flatpak build context. It installs the
standalone under `/app/lib/iphotron`, exports `/app/bin/iphoto`, refuses to
overwrite an existing output, and removes the temporary context on exit. The
bundle is accompanied by a `.build-manifest.json` provenance record.

Recognition models follow the standalone build posture: omitted by default,
or copied from the ignored `src/extension/models` staging directory when that
directory was populated before the Nuitka build. YOLOX can use its verified
network acquisition path; the current DINOv2 manifest has no URL and requires a
pre-provisioned artifact.

## Sandbox Contract

The manifest declares:

- Wayland and fallback X11 sockets plus shared IPC;
- DRI for GPU rendering and PulseAudio for media playback;
- network access for explicitly invoked extension/model acquisition; and
- `--filesystem=host:rw`, because folder-native libraries may be selected
  outside a single XDG media directory.

The package does not inherit executables from the host `PATH`. The 25.08
Freedesktop runtime supplies FFmpeg, but release validation must still exercise
the exact codecs used by iPhotron. ExifTool is optional: to enable metadata
extraction and explicit GPS write-back, stage an executable at
`dist/entrypoint.dist/iPhoto/bin/exiftool` before packaging. Without it, the
application follows its existing recoverable warning/degradation path.

## Verification

```bash
flatpak install --user \
  "dist/com.github.OliverZhaohaibin.iPhotron-${VERSION}-x86_64.flatpak"
flatpak run com.github.OliverZhaohaibin.iPhotron
```

Test XCB and Wayland separately. Open an arbitrary library folder, Gallery,
Detail, a video, and Maps; confirm that a Wayland session is not forced to XCB.
Also verify the promised recognition posture and the ExifTool warning or
write-back behavior for the exact release payload.

Run the structural checks with:

```bash
.venv/bin/python -m pytest -q tests/test_flatpak_packaging.py
```

The manifest/build-bundle structure follows the
[official Flatpak builder workflow](https://docs.flatpak.org/en/latest/first-build.html).
