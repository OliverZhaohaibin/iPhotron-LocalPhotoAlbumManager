# Flatpak Build Contract

This note separates the Flatpak **release artifact** from the current
**development build contract**.

## Current status

The v6.6.8 release contains a downloadable Flatpak bundle. The current
`edit-base` tree, however, does not contain a maintained Flatpak manifest,
Flatpak build driver, or Flatpak CI job. `packaging/` currently defines the
AppImage path, while the reproducible Debian path wraps the Linux Nuitka
standalone bundle.

Therefore:

- README files may keep a link to an existing published Flatpak bundle.
- Developer documentation must not imply that this branch can reproduce that
  bundle from source.
- AppImage or Debian instructions are not substitutes for a Flatpak manifest.

## Requirements for first-class Flatpak support

Flatpak becomes a current reproducible target only when the repository contains:

1. a checked-in Flatpak manifest and application id;
2. an explicit Flatpak runtime/SDK version;
3. a documented command that builds from a clean checkout;
4. Qt/QML/QSB resource packaging;
5. an explicit Maps-extension packaging/fallback policy;
6. an explicit People/Pets optional-runtime and model-artifact policy;
7. smoke tests for launch, Gallery, Detail, and enabled optional features;
8. CI or release validation for the manifest and generated bundle.

A recommended future layout is:

```text
packaging/flatpak/
scripts/build_flatpak.sh
```

The exact names are not normative; having a reviewable in-repository build
contract is.

## README parity rule

English, Simplified Chinese, and German README files must all communicate the
same distinction: a published Flatpak bundle can be downloaded, while the
current branch does not yet provide a reproducible in-repo Flatpak build path.

For Linux packaging paths that are currently reproducible from this repository,
see `BUILD_APPIMAGE.md` and `BUILD_DEB.md`.
