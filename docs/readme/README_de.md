# 📸 iPhotron
> Bringen Sie die *Fotos*-Erfahrung von macOS auf Windows — ordnerbasierte, nicht-destruktive Fotoverwaltung mit Live Photo, Karten und intelligenten Alben.

![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Language](https://img.shields.io/badge/language-Python%203.10%2B-blue)
![Framework](https://img.shields.io/badge/framework-PySide6%20(Qt6)-orange)
![License](https://img.shields.io/badge/license-MIT-green)
[![GitHub Repo](https://img.shields.io/badge/github-iPhotos-181717?logo=github)](https://github.com/OliverZhaohaibin/iPhotos-LocalPhotoAlbumManager)

**Sprachen / Languages:**  
[![English](https://img.shields.io/badge/English-Click-blue?style=flat)](../../README.md) | [![中文简体](https://img.shields.io/badge/中文简体-点击-red?style=flat)](README_zh-CN.md) | [![Deutsch](https://img.shields.io/badge/Deutsch-Klick-yellow?style=flat)](README_de.md)

---

## ☕ Unterstützung

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Entwicklung%20unterstützen-yellow?style=for-the-badge&logo=buy-me-a-coffee&logoColor=white)](https://buymeacoffee.com/oliverzhao)
[![PayPal](https://img.shields.io/badge/PayPal-Entwicklung%20unterstützen-blue?style=for-the-badge&logo=paypal&logoColor=white)](https://www.paypal.com/donate/?hosted_button_id=AJKMJMQA8YHPN)

## 📥 Download & Installation

[![Für Windows herunterladen](https://img.shields.io/badge/⬇️%20Download-Windows%20(.exe)-blue?style=for-the-badge&logo=windows)](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v4.5.0/v4.50.exe)
[![Für Linux herunterladen](https://img.shields.io/badge/⬇️%20Download-Linux%20(.deb)-orange?style=for-the-badge&logo=linux&logoColor=white)](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v4.5.0/iPhotron_4.50_amd64.deb)
[![Download for Linux (.AppImage)](https://img.shields.io/badge/⬇️%20Download-Linux%20(.AppImage)-brightgreen?style=for-the-badge&logo=linux&logoColor=white)](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/v4.5.0/iPhotron-x86_64.AppImage)


**💡 Schnellinstallation:** Klicken Sie auf die Schaltflächen oben, um das neueste Installationsprogramm direkt herunterzuladen.

- **Windows:** Führen Sie das `.exe`-Installationsprogramm direkt aus.
- **Linux:** Installationsbefehl:

```bash
sudo apt install ./iPhotron_4.30_amd64.deb
```

**Für Entwickler:**

```bash
pip install -e .
```

---

## 🚀 Schnellstart

```bash
iphoto-gui
```

Oder direkt ein bestimmtes Album öffnen:

```bash
iphoto-gui /fotos/LondonReise
```

---

## 🌟 Star-Verlauf

<p align="center">
  <a href="https://www.star-history.com/#OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager&type=date&legend=bottom-right">
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager&type=date&legend=bottom-right" />
  </a>
</p>

## 🚀 Product Hunt
<p align="center">
  <a href="https://www.producthunt.com/products/iphotron/launches/iphotron?embed=true&amp;utm_source=badge-featured&amp;utm_medium=badge&amp;utm_campaign=badge-iphotron" target="_blank" rel="noopener noreferrer">
    <img alt="iPhotron - A macOS Photos–style photo manager for Windows | Product Hunt" width="250" height="54" src="https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1067965&amp;theme=light&amp;t=1772225909629">
  </a>
</p>

<p align="center">
  <span style="color:#FF6154;"><strong>Bitte unterstütze uns mit einem Upvote</strong></span> •
  <span style="color:#FF6154;"><strong>Folgen</strong></span> •
  <span style="color:#FF6154;"><strong>Im Forum diskutieren</strong></span>
</p>

---

## 🌟 Überblick

**iPhotron** ist ein **ordnerbasierter Foto-Manager**, inspiriert von macOS *Fotos*.  
Es organisiert Ihre Medien mit leichtgewichtigen JSON-Manifesten und Cache-Dateien —  
bietet umfangreiche Album-Funktionalität und **hält alle Originaldateien intakt**.

Wichtige Highlights:
- 🗂 Ordnerbasiertes Design — jeder Ordner *ist* ein Album, kein Import erforderlich.
- ⚙️ JSON-basierte Manifeste zeichnen "menschliche Entscheidungen" auf (Cover, Favoriten, Reihenfolge).
- ⚡ **SQLite-gestützte globale Datenbank** für blitzschnelle Abfragen auf massiven Bibliotheken.
- 🧠 Intelligentes inkrementelles Scannen mit persistentem SQLite-Index.
- 🎥 Vollständige **Live Photo**-Paarungs- und Wiedergabeunterstützung.
- 🗺 Kartenansicht, die GPS-Metadaten über alle Fotos und Videos visualisiert.
![Main interface](../picture/mainview.png)
![Preview interface](../picture/preview.png)
---

## 🗺 Maps Extension

Die Offline-OBF-Kartenlaufzeit von iPhotron wird als selbstenthaltene
**maps extension** unter `src/maps/tiles/extension/` bereitgestellt. Genau
dieses Verzeichnislayout wird von der lokalen Entwicklung, von Paket-Builds
und vom Windows-Installer verwendet.

Die Extension enthält derzeit:
- Offline-Kartendaten in `World_basemap_2.obf`
- OsmAnd-Ressourcen unter `misc/`, `poi/`, `rendering_styles/` und `routing/`
- native Binärdateien unter `bin/`, darunter `osmand_render_helper.exe`,
  `osmand_native_widget.dll`, `OsmAndCore_shared.dll` und die benötigten Qt-DLLs

> **Derzeit nur Windows:** Die unten gezeigte vollständige native
> maps-extension-Laufzeit ist aktuell nur unter Windows verfügbar. Unter Linux
> und macOS verwendet iPhotron weiterhin den bestehenden Python-/Legacy-
> Kartenpfad.

| Ohne Maps Extension | Mit Maps Extension |
| --- | --- |
| ![Ohne Maps Extension](../picture/without_extension.png) | ![Mit Maps Extension](../picture/maps_extension.png) |

Die Extension wird im Upstream-Teilprojekt
[PySide6-OsmAnd-SDK](https://github.com/OliverZhaohaibin/PySide6-OsmAnd-SDK)
gebaut. Dieses Repository enthält die vendorten OsmAnd-Quellen, Windows-
Buildskripte, die native Qt-Widget-Bridge und die Preview-App, aus denen die
hier verwendete Laufzeit erzeugt wird.

Den vollständigen Workflow "maps extension aus dem Side-Project in dieses
Repository übernehmen" findest du in [Development](../development.md). Hinweise
zu Nuitka, Runtime-Synchronisierung und Windows-Installer stehen in
[Executable Build](../misc/BUILD_EXE.md).

## ✨ Funktionen

### 🗺 Standortansicht
Zeigt Ihre Foto-Fußabdrücke auf einer interaktiven Karte und gruppiert nahe gelegene Fotos nach GPS-Metadaten.
![Location interface](../picture/map1.png)
![Location interface](../picture/map2.png)

### 🎞 Live Photo-Unterstützung
Paart nahtlos HEIC/JPG- und MOV-Dateien mithilfe von Apples `ContentIdentifier`.  
Ein "LIVE"-Badge erscheint auf Standbildern — klicken Sie, um das Bewegungsvideo inline abzuspielen.
![Live interface](../picture/live.png)

### 🧩 Intelligente Alben
Die Seitenleiste bietet eine automatisch generierte **Grundbibliothek**, die Fotos in Gruppen einteilt:
`Alle Fotos`, `Videos`, `Live Photos`, `Favoriten` und `Kürzlich gelöscht`.

### 🖼 Immersive Detailansicht
Ein eleganter Foto-/Videobetrachter mit Filmstreifen-Navigator und schwebendem Wiedergabebalken.

### 🎨 Nicht-destruktive Fotobearbeitung
Eine umfassende Bearbeitungssuite mit **Anpassen**- und **Zuschneiden**-Modi:

#### Anpassen-Modus
- **Lichtanpassungen:** Brillanz, Belichtung, Lichter, Schatten, Helligkeit, Kontrast, Schwarzpunkt
- **Farbanpassungen:** Sättigung, Lebendigkeit, Farbstich (Weißabgleichkorrektur)
- **Schwarzweiß:** Intensität, Neutraltöne, Ton, Körnung mit künstlerischen Film-Voreinstellungen
- **Farbkurven:** RGB- und kanalbasierter (R/G/B) Kurven-Editor mit ziehbaren Kontrollpunkten für präzise Tonanpassungen
- **Selektive Farbe:** Zielt auf sechs Farbbereiche (Rot/Gelb/Grün/Cyan/Blau/Magenta) mit unabhängigen Farbton-/Sättigungs-/Helligkeitskontrollen
- **Tonwerte:** 5-Punkt-Eingangs-Ausgangs-Tonzuordnung mit Histogramm-Hintergrund und kanalbasierter Steuerung
- **Master-Schieberegler:** Jeder Abschnitt verfügt über einen intelligenten Master-Schieberegler, der Werte auf mehrere Feinabstimmungssteuerungen verteilt
- **Live-Miniaturansichten:** Echtzeit-Vorschaustreifen, die den Effektbereich für jede Anpassung zeigen

![edit interface](../picture/editview.png)
![edit interface](../picture/professionaltools.png)

#### Zuschneiden-Modus
- **Perspektivkorrektur:** Vertikale und horizontale Trapezverzerrungsanpassungen
- **Ausrichten-Werkzeug:** ±45° Drehung mit Sub-Grad-Präzision
- **Spiegeln (Horizontal):** Horizontale Spiegelungsunterstützung
- **Interaktives Zuschneiderechteck:** Ziehbare Griffe, Kantenfang und Seitenverhältnisbeschränkungen
- **Schwarzrand-Prävention:** Automatische Validierung stellt sicher, dass nach Perspektivtransformationen keine schwarzen Kanten erscheinen
  
![crop interface](../picture/cropview.png)
Alle Bearbeitungen werden in `.ipo`-Sidecar-Dateien gespeichert und bewahren die Originalfotos unberührt.

### ℹ️ Schwebendes Info-Panel
Schalten Sie ein schwebendes Metadaten-Panel um, das EXIF, Kamera-/Objektivinformationen, Belichtung, Blende, Brennweite, Dateigröße und mehr anzeigt.
![Info interface](../picture/info1.png)

### 💬 Umfangreiche Interaktionen
- Ziehen und Ablegen von Dateien direkt aus dem Explorer/Finder in Alben.
- Mehrfachauswahl und Kontextmenüs für Kopieren, In Ordner anzeigen, Verschieben, Löschen, Wiederherstellen.
- Sanfte Miniaturansichts-Übergänge und macOS-ähnliche Album-Navigation.

---

## 📚 Dokumentation

Detaillierte technische Dokumentation (auf Englisch):

[![Architecture](https://img.shields.io/badge/📐_Architecture-blue?style=for-the-badge)](../architecture.md)
[![Development](https://img.shields.io/badge/🧰_Development-green?style=for-the-badge)](../development.md)
[![Executable Build](https://img.shields.io/badge/🧱_Executable_Build-purple?style=for-the-badge)](../misc/BUILD_EXE.md)
[![Security](https://img.shields.io/badge/🔒_Security-red?style=for-the-badge)](../security.md)
[![Changelog](https://img.shields.io/badge/📋_Changelog-orange?style=for-the-badge)](../CHANGELOG.md)

| Dokument | Beschreibung |
|----------|-------------|
| [Architecture](../architecture.md) | Gesamtarchitektur, Modulgrenzen, Datenfluss, wichtige Designentscheidungen |
| [Development](../development.md) | Entwicklungsumgebung, Abhängigkeiten, Debugging und der vollständige maps-extension-Workflow über das Side-Project |
| [Executable Build](../misc/BUILD_EXE.md) | Nuitka-Paketierung, AOT, maps-extension-Synchronisierung und Hinweise zum Windows-Installer |
| [Security](../security.md) | Berechtigungen, Verschlüsselung, Datenspeicherorte, Bedrohungsmodell |
| [Changelog](../CHANGELOG.md) | Alle Versionshinweise und Änderungen |

---

## 📄 Lizenz

**MIT-Lizenz © 2025**  
Erstellt von **Haibin Zhao (OliverZhaohaibin)**  

> *iPhotron — Ein ordnerbasiertes, menschenlesbares und vollständig wiederaufbaubares Fotosystem.*  
> *Keine Importe. Keine Datenbank. Nur Ihre Fotos, elegant organisiert.*
