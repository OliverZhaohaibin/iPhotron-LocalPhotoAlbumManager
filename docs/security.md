# 🔒 Security

> Permissions, encryption, data storage locations, and threat model for **iPhotron**.

---

## Overview

iPhotron is a **local-first photo manager**. It does not upload data to any cloud service, does not require an internet connection for core functionality, and does not collect user telemetry. All library data remains on the user's local filesystem. The only network-facing flow is an optional user-triggered maps-extension download on platforms where a published extension archive exists.

---

## Permissions

### Filesystem Access

| Access | Scope | Purpose |
|--------|-------|---------|
| **Read** | User-selected library folders | Scan photos/videos, read metadata (EXIF, GPS) |
| **Write** | Library folders | Create `.iphoto.album.json` manifests, `.ipo` sidecar files |
| **Write** | `.iPhoto/` directory at library root | Global SQLite database (`global_index.db`), thumbnail cache |
| **Write** | Selected original media file | Best-effort GPS metadata write-back after an explicit Assign Location action |
| **Read/Write** | Application settings directory | User preferences (theme, export destination) |

### External Tool Access

| Tool | Access | Purpose |
|------|--------|---------|
| **ExifTool** | Read/write on media files when requested | Extract EXIF, GPS, QuickTime metadata; write GPS coordinates for explicit Assign Location actions |
| **FFmpeg / FFprobe** | Read-only on media files | Generate video thumbnails, parse video info |

### Network Access

iPhotron requires **no network access for normal library operation**. Map rendering,
reverse geocoding, and Assign Location search work offline when the maps
extension is installed.

| Feature | Access | Purpose |
|---------|--------|---------|
| **Map rendering** | Offline (bundled OBF/vector map assets) | Render map tiles for the location view |
| **Reverse geocoding** | Local database lookup | Convert GPS coordinates to place names (offline, via `reverse-geocoder` library) |
| **Map extension download** | Optional HTTPS download | Fetch a published extension archive only when the user chooses the download path |

> **Note:** No telemetry or cloud sync is performed. A network connection is only
> needed if the user chooses to download a missing map extension from a release
> archive.

---

## Encryption

### At Rest

iPhotron does **not** encrypt data at rest. The following files are stored in plaintext:

| File | Format | Contents |
|------|--------|----------|
| `.iphoto.album.json` | JSON | Album metadata: cover image, featured photos, sort order |
| `*.ipo` | JSON | Edit parameters: light, color, B&W, crop, perspective adjustments |
| `global_index.db` | SQLite | Asset metadata: file paths, timestamps, GPS coordinates, media types, location assignment metadata |
| `.iPhoto/faces/face_index.db` | SQLite | Rebuildable People runtime snapshot |
| `.iPhoto/faces/face_state.db` | SQLite | Stable People decisions: names, covers, hidden flags, groups, ordering |
| Thumbnail cache | Image files | Downscaled preview images |

**Rationale:** The data managed by iPhotron (album organization, edit parameters, file metadata) is non-sensitive in most contexts. Users who require encryption should use full-disk encryption (e.g., BitLocker, FileVault, LUKS).

### In Transit

- No media, metadata, telemetry, or library state is transmitted by normal app
  operation. The optional maps-extension download retrieves a release archive
  only when the user chooses that path.

---

## Data Storage Locations

```
LibraryRoot/                          # User-selected photo library folder
├── .iPhoto/
│   ├── global_index.db               # SQLite database (all asset metadata)
│   ├── thumbs/                       # Thumbnail cache
│   └── faces/
│       ├── face_index.db             # Rebuildable People runtime snapshot
│       ├── face_state.db             # Stable People user decisions
│       └── thumbnails/               # Cropped face thumbnails
├── Album1/
│   ├── .iphoto.album.json            # Album manifest
│   ├── photo.jpg                     # Original photo (edits are sidecar-only)
│   └── photo.jpg.ipo                 # Edit sidecar (if edited)
└── Album2/
    └── ...
```

### Settings Storage

User settings (theme preference, export destination) are stored via Qt's `QSettings` mechanism:

| Platform | Location |
|----------|----------|
| **Windows** | Registry: `HKEY_CURRENT_USER\Software\iPhoto` |
| **macOS** | `~/Library/Preferences/com.iphoto.plist` |
| **Linux** | `~/.config/iPhoto/iPhoto.conf` |

---

## Threat Model

### Assets Protected

| Asset | Sensitivity | Protection |
|-------|-------------|------------|
| Original photos/videos | Personal (potentially high) | Edits are non-destructive; explicit Assign Location may best-effort write GPS metadata |
| GPS coordinates in metadata | Location data (medium) | Stored in SQLite index and, when ExifTool write-back succeeds, in the original file metadata |
| Album organization | Low | Stored in JSON manifests alongside photos |
| Edit parameters | Low | Stored in `.ipo` sidecar files |

### Threat Scenarios

#### T1: Unauthorized Access to Photo Library

| | |
|---|---|
| **Threat** | An attacker gains read access to the library folder |
| **Impact** | Access to original photos, GPS metadata, album organization |
| **Mitigation** | OS-level file permissions; full-disk encryption recommended for sensitive libraries |
| **iPhotron's role** | iPhotron does not add or remove filesystem protections |

#### T2: SQLite Database Tampering

| | |
|---|---|
| **Threat** | An attacker modifies `global_index.db` |
| **Impact** | Corrupted index leading to incorrect display; no data loss (rescannable) |
| **Mitigation** | OS-level file permissions; iPhotron's automatic recovery (REINDEX → Salvage → Reset) |
| **Recovery** | Re-scan the library to rebuild the database from filesystem |

#### T3: Malicious Media Files

| | |
|---|---|
| **Threat** | A crafted image/video exploits a vulnerability in a parsing library |
| **Impact** | Potential code execution via Pillow, FFmpeg, or ExifTool |
| **Mitigation** | Keep dependencies updated; use `pillow-heif` and `opencv-python-headless` (no GUI attack surface) |

#### T4: Malicious Map Data

| | |
|---|---|
| **Threat** | Crafted or corrupted bundled map data, resources, or search database |
| **Impact** | Incorrect map display; potential parsing vulnerability |
| **Mitigation** | Map assets are rendered through local native/helper renderers or Qt/OpenGL paths, not a web view; no script execution is performed; published extension archives should be validated before release |

#### T5: Supply Chain Attack via Dependencies

| | |
|---|---|
| **Threat** | A compromised PyPI package is installed |
| **Impact** | Arbitrary code execution |
| **Mitigation** | Pin dependency versions in `pyproject.toml`; review dependency updates; use virtual environments |

---

## Security Best Practices for Users

1. **Use full-disk encryption** (BitLocker / FileVault / LUKS) if your photo library contains sensitive content.
2. **Keep ExifTool and FFmpeg updated** to receive security patches.
3. **Install ExifTool only from a trusted source** if you plan to use GPS write-back through Assign Location.
4. **Run `pip install --upgrade`** periodically to update Python dependencies.
5. **Use OS-level file permissions** to restrict access to your library folder.
6. **Back up your library** regularly — iPhotron's `.iPhoto/` directory and `.ipo` files should be included in backups.

---

## Reporting Security Issues

If you discover a security vulnerability, please report it via [GitHub Security Advisories](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/security/advisories) or email the maintainers directly. Do not open a public issue for security vulnerabilities.
