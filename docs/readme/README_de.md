# 📸 iPhotron

> Ordnernativer Foto-Manager für Windows, macOS und Linux, inspiriert von macOS Fotos.

**Sprachen:** [English](../../README.md) · [简体中文](README_zh-CN.md) · [Deutsch](README_de.md)

## Release und Entwicklung

**Die Downloads sind die veröffentlichten Dateien von v6.6.8.** Die
Funktionsübersicht beschreibt zusätzlich den aktuellen `edit-base`-Zweig und
kann Unreleased-Funktionen enthalten. Entwicklungsfunktionen sind deshalb nicht
automatisch Bestandteil von v6.6.8. Siehe [`CHANGELOG.md`](../CHANGELOG.md).

## v6.6.8 Downloads

- Windows: [`v6.68-x86-setup.exe`](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v6.6.8/v6.68-x86-setup.exe)
- Debian: [`iphotron_6.6.8_amd64.deb`](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v6.6.8/iphotron_6.6.8_amd64.deb)
- AppImage: [`iPhotron-6.6.8-x86_64.AppImage`](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v6.6.8/iPhotron-6.6.8-x86_64.AppImage)
- Flatpak: [`com.github.OliverZhaohaibin.iPhotron-6.6.8-x86_64.flatpak`](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v6.6.8/com.github.OliverZhaohaibin.iPhotron-6.6.8-x86_64.flatpak)

`v6.68-x86-setup.exe` ist der tatsächliche Windows-Dateiname im Release.

Für v6.6.8 gibt es ein Flatpak-Release-Artefakt. Der aktuelle Branch enthält
jedoch kein gepflegtes Flatpak-Build-Rezept im Repository. Siehe
[`BUILD_FLATPAK.md`](../misc/BUILD_FLATPAK.md). Debian und AppImage besitzen
aktuelle In-Repo-Anleitungen.

## Aus dem Quellcode

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
iphoto-gui
```

Der installierte GUI-Einstieg ist `iPhoto.entrypoint:main`.

## Aktuelle Entwicklung

- ordnernative Alben ohne Import;
- SQLite-basierte große Bibliotheken und asynchrone Gallery-Fenster;
- Live Photo;
- optionale Offline-Maps;
- optionale People- und Pets-Erkennung;
- GPU-first Detail und gemeinsames Detail/Edit-Rendering;
- nicht-destruktive `.ipo`-Bearbeitung.

## People & Pets

People und Pets besitzen getrennte Runtime-Indizes und dauerhaften State. Die
aktuelle Pets-Pipeline ist `species-bounded-single-link-v3` mit Arttrennung,
Cannot-Link-Regeln und begrenztem Cluster-Durchmesser.

Die Konfliktregel bedeutet nicht „People gewinnt immer“. Starke
Gesichtsüberlappung unterdrückt normalerweise einen Pet-Kandidaten; ein deutlich
größerer plausibler Tierkörper-Rahmen kann durch die Größen-/Bildabdeckungsregel
erhalten bleiben.

Erkennungs-Inferenz startet erst, nachdem die People-Oberfläche geöffnet wurde
und ihr erster Viewport bereit ist; normaler App-Start startet sie nicht.

DINOv2 wird als vorbereitete TorchScript-Datei über das Manifest geprüft. Mit
`torchscript_url: null` muss das Artefakt derzeit paketiert oder explizit gestaged
werden. `src/extension/models` ist nur eine Packaging-/Staging-Konvention.

Siehe [`PETS_RECOGNITION_RUNTIME.md`](../misc/PETS_RECOGNITION_RUNTIME.md).

## Architektur

`DesktopCoordinatorRuntime` ist der Desktop Composition Root;
`main_coordinator.py` ist nur ein Compatibility Import.

- [`AGENT.md`](../../AGENT.md)
- [`architecture.md`](../architecture.md)
- [`development.md`](../development.md)
- [`security.md`](../security.md)
- [`requirements/README.md`](../requirements/README.md)

## Lizenz

MIT — siehe [`LICENSE`](../../LICENSE).
