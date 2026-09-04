# Building the Linux Flatpak Bundle

This is the canonical in-tree procedure for producing the x86-64 single-file
Flatpak bundle published with GitHub releases. It wraps the maintained Nuitka
standalone; it is not a Flathub source-build manifest and must not be presented
as satisfying Flathub submission requirements.

## Prerequisites

- Ubuntu 24.04 x86-64 is the supported release-build baseline. Other hosts may
  be used for development, but their standalone manifests are rejected by the
  canonical wrapper.
- `flatpak`, `flatpak-builder`, and GNU `readelf` from binutils.
- The Flathub remote and the `org.freedesktop.Platform` / `org.freedesktop.Sdk`
  25.08 runtime. The build script asks `flatpak-builder` to install missing
  dependencies from Flathub.
- A synchronized `dist/entrypoint.dist` and its `dist/build-manifest.json` from
  the same Ubuntu 24.04 invocation of `scripts/build_nuitka_fast.sh`.
- A valid 256×256 PNG application icon.

The tracked manifest is
`packaging/flatpak/io.github.oliverzhaohaibin.iPhotron.yml` and the application
ID is `io.github.oliverzhaohaibin.iPhotron`. The manifest uses the current `id`
field rather than the deprecated `app-id` compatibility alias.

## Build

```bash
bash scripts/build_nuitka_fast.sh

VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
bash scripts/build_flatpak.sh \
  --standalone-dir dist/entrypoint.dist \
  --standalone-manifest dist/build-manifest.json \
  --icon /absolute/path/to/iphotron-256.png \
  --output "dist/io.github.oliverzhaohaibin.iPhotron-${VERSION}-x86_64.flatpak"
```

Before staging, the builder verifies that the standalone manifest records an
Ubuntu 24.04 x86-64 build and that its artifact hash matches the selected ELF
entry point. The manifest source revision, `project_version` build flag, and
Nuitka build-driver hash must match the current checkout. This prevents an old
standalone from being labeled with the current `pyproject.toml` version.
`artifact_tree_sha256` must also match the complete standalone, including node
type, Unix permission bits, file contents, and symlink targets, so replacing or
changing a Qt, Maps, ONNX Runtime, or other payload node after the Nuitka build
invalidates provenance. It then scans every ELF file using
`readelf --version-info --wide` and verifies an ELF64, little-endian,
`EM_X86_64` header. The Freedesktop 25.08 limits are `GLIBC_2.42`,
`GLIBCXX_3.4.34`, and `CXXABI_1.3.15`; an unreadable file, wrong architecture,
or newer requirement rejects the build.

The builder also validates the PNG structure, CRCs, and exact 256×256 size,
then checks the executable, QRhi shaders, Maps helper, and Qt XCB plugin. It
installs the standalone under `/app/lib/iphotron`, exports `/app/bin/iphoto`,
and removes the temporary context on exit. The bundle is accompanied by an
`.abi-report.json` plus the existing `.build-manifest.json` provenance record.
Because this manifest wraps already-compiled ELF files, it sets
`build-options.no-debuginfo: true`; `flatpak-builder` therefore does not try to
extract another debug extension with `eu-strip`.

The Flatpak ref branch is always `stable`. Application versions such as 6.6.8
belong in the bundle filename, AppStream release metadata, and release manifest,
not in the Flatpak ref. A future beta or nightly channel must use an explicit
separate branch rather than changing this stable update path.

The same preflight can be run without building a bundle:

```bash
SOURCE_REVISION="$(git rev-parse HEAD)"
VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
python3 tools/check_flatpak_elf_abi.py \
  --root dist/entrypoint.dist \
  --entrypoint dist/entrypoint.dist/entrypoint.bin \
  --build-manifest dist/build-manifest.json \
  --expected-source-revision "$SOURCE_REVISION" \
  --expected-project-version "$VERSION" \
  --expected-build-driver scripts/build_nuitka_fast.sh \
  --icon /absolute/path/to/iphotron-256.png \
  --max-glibc 2.42 \
  --max-glibcxx 3.4.34 \
  --max-cxxabi 1.3.15 \
  --output dist/flatpak-abi-report.json
```

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
  "dist/io.github.oliverzhaohaibin.iPhotron-${VERSION}-x86_64.flatpak"
flatpak run io.github.oliverzhaohaibin.iPhotron
```

Test XCB and Wayland separately. Open an arbitrary library folder, Gallery,
Detail, a video, and Maps; confirm that a Wayland session is not forced to XCB.
Also verify the promised recognition posture and the ExifTool warning or
write-back behavior for the exact release payload.

Run the structural checks with:

```bash
.venv/bin/python -m pytest -q \
  tests/test_build_manifest.py \
  tests/test_flatpak_elf_abi.py \
  tests/test_flatpak_packaging.py
```

The path-filtered `Flatpak Smoke` GitHub Actions workflow runs on Ubuntu 24.04
with real `flatpak-builder`: it builds a minimal ELF payload with this manifest,
creates a bundle, installs it in an isolated user Flatpak root, runs it, checks
its output, and uninstalls it. That integration job validates the packaging
lifecycle; the Python test remains the fast orchestration/failure-path layer.

## Migration From The v6.6.8 Legacy ID

The published v6.6.8 asset keeps its historical filename and legacy application
ID, `com.github.OliverZhaohaibin.iPhotron`. Future bundles use
`io.github.oliverzhaohaibin.iPhotron`. The new MetaInfo records the old ID in
both `provides` and `replaces`, but independently distributed single-file
bundles do not share a Flatpak remote and therefore cannot use an automatic
end-of-life rebase.

Before installing a new-ID bundle, uninstall the old application without
`--delete-data`:

```bash
flatpak uninstall --user com.github.OliverZhaohaibin.iPhotron
flatpak install --user \
  "dist/io.github.oliverzhaohaibin.iPhotron-${VERSION}-x86_64.flatpak"
```

Flatpak stores the two IDs under separate `~/.var/app/<id>/` roots. If desired,
copy only the old `config/iPhoto/settings.json` and verified model-cache files
into the equivalent new-ID directories while both applications are stopped.
Do not copy rebuildable shader or thumbnail caches. Library-local `.iPhoto`
state and `.ipo` sidecars remain in the selected library and need no migration.

The manifest/build-bundle structure follows the
[official Flatpak builder workflow](https://docs.flatpak.org/en/latest/first-build.html).
