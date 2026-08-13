# 📸 iPhotron

> A macOS Photos-inspired, folder-native photo manager for Windows, macOS, and Linux.

**Languages:** [English](README.md) · [简体中文](docs/readme/README_zh-CN.md) · [Deutsch](docs/readme/README_de.md)

## Release and development status

**The downloads below are the published v6.6.8 binaries.** The feature overview
also describes the current `edit-base` development branch and may include
Unreleased work. A development feature is not automatically present in v6.6.8.
See [`docs/CHANGELOG.md`](docs/CHANGELOG.md) for current branch changes.

## v6.6.8 downloads

| Platform | Artifact |
| --- | --- |
| Windows | [`v6.68-x86-setup.exe`](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v6.6.8/v6.68-x86-setup.exe) |
| Debian | [`iphotron_6.6.8_amd64.deb`](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v6.6.8/iphotron_6.6.8_amd64.deb) |
| AppImage | [`iPhotron-6.6.8-x86_64.AppImage`](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v6.6.8/iPhotron-6.6.8-x86_64.AppImage) |
| Flatpak | [`com.github.OliverZhaohaibin.iPhotron-6.6.8-x86_64.flatpak`](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v6.6.8/com.github.OliverZhaohaibin.iPhotron-6.6.8-x86_64.flatpak) |

`v6.68-x86-setup.exe` is the actual published Windows asset name.

The v6.6.8 Flatpak file is a published release artifact, but this development
branch currently has no maintained in-repository Flatpak build recipe. See
[`BUILD_FLATPAK.md`](docs/misc/BUILD_FLATPAK.md). Current reproducible Linux
packaging guides are [Debian](docs/misc/BUILD_DEB.md) and
[AppImage](docs/misc/BUILD_APPIMAGE.md).

## Run from source

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
iphoto-gui
```

The installed GUI entry point is `iPhoto.entrypoint:main`.

## Current development highlights

- Folder-native albums with no import step.
- SQLite-backed large-library browsing with sparse asynchronous Gallery windows.
- Demand-driven thumbnail loading and generation-safe scrolling.
- Live Photo pairing and playback.
- Optional offline Maps/OsmAnd runtime.
- Optional People recognition with names, covers, groups, hidden state, and
  manual faces.
- Optional Pets recognition for cats/dogs with durable names/covers/state.
- GPU-first Detail rendering with shared Detail/Edit render sessions.
- Non-destructive `.ipo` sidecar editing.
- Assign Location with local persistence and best-effort GPS metadata write-back.

![Main interface](docs/picture/mainview.png)

## People & Pets

People and Pets are separate optional bounded contexts. They keep independent
runtime indexes and durable state while the UI can compose them into cards,
groups, Gallery queries, and Detail annotations.

The current Pets identity pipeline is `species-bounded-single-link-v3`:
species stay separate, cannot-link constraints are respected, and cluster
diameter is bounded to prevent uncontrolled chaining.

People/Pets conflict filtering is not an unconditional “People always wins”
rule. Strong face overlap normally suppresses a pet candidate, while a much
larger plausible pet-body detection containing a smaller face can be preserved
by the runtime size/image-coverage exception.

Recognition inference is feature-driven: scanning is activated only after the
People surface has been opened and its first viewport is ready. Normal app
startup does not independently start People/Pets inference.

DINOv2 production loading uses a prebuilt TorchScript artifact verified by the
model manifest. The current manifest has `torchscript_url: null`, so the DINOv2
artifact must currently be packaged or explicitly staged. `src/extension/models`
is a packaging/staging convention, not guaranteed fresh-clone content.

See [`PETS_RECOGNITION_RUNTIME.md`](docs/misc/PETS_RECOGNITION_RUNTIME.md).

## Architecture and packaging

`DesktopCoordinatorRuntime` is the production desktop composition root;
`main_coordinator.py` is a compatibility import only.

Current documentation:

- [`AGENT.md`](AGENT.md)
- [`docs/architecture.md`](docs/architecture.md)
- [`docs/development.md`](docs/development.md)
- [`docs/security.md`](docs/security.md)
- [`docs/requirements/README.md`](docs/requirements/README.md)

| Package target | Current branch status |
| --- | --- |
| Windows / Nuitka | documented |
| Debian | reproducible in-repo guide |
| AppImage | reproducible in-repo guide |
| Flatpak | v6.6.8 download exists; current in-repo recipe is absent |

## License

MIT — see [`LICENSE`](LICENSE).
