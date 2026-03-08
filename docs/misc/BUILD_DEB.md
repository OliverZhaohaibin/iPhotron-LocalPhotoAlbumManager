# Building a Debian Package (.deb) for Linux

This document describes how to build a Debian package (`.deb`) for iPhotron on Linux.

## Overview

A `.deb` package allows easy installation and removal on Debian-based distributions (Ubuntu, Mint, etc.) using standard package management tools such as `apt` and `dpkg`.

## Prerequisites

- A Debian-based Linux distribution (Ubuntu, Debian, Mint, …)
- `dpkg-deb` (usually pre-installed on Debian/Ubuntu systems)
- A working build of iPhotron (see [`BUILD_EXE.md`](BUILD_EXE.md) for producing the standalone binary)

## Directory Structure

Create the following staging directory layout before building:

```
iPhotron_4.30_amd64/
├── DEBIAN/
│   └── control
└── usr/
    └── local/
        └── bin/
            └── iPhotron          ← your compiled executable
```

## The `control` File

The `DEBIAN/control` file contains the package metadata. Create it with the following content:

```
Package: iPhotron
Version: 4.30
Section: graphics
Priority: optional
Architecture: amd64
Maintainer: OliverZhao
Description: Folder-native local photo album manager
 iPhotron is a folder-native photo manager inspired by macOS Photos.
 It organizes media using lightweight JSON manifests and provides rich
 album functionality while keeping all original files intact.
```

> **Fields explained**
>
> | Field | Value | Notes |
> |-------|-------|-------|
> | `Package` | `iPhotron` | Binary package name |
> | `Version` | `4.30` | Upstream version |
> | `Architecture` | `amd64` | Target CPU architecture (x86-64) |
> | `Maintainer` | `OliverZhao` | Name (and optionally email) of the package maintainer |
> | `Description` | short + long | First line is the synopsis; indented lines form the long description |

## Build Steps

1. **Prepare the staging tree** — copy your compiled iPhotron binary into the correct location inside the staging directory:

   ```bash
   mkdir -p iPhotron_4.30_amd64/DEBIAN
   mkdir -p iPhotron_4.30_amd64/usr/local/bin
   cp dist/iPhotron iPhotron_4.30_amd64/usr/local/bin/iPhotron
   chmod 755 iPhotron_4.30_amd64/usr/local/bin/iPhotron
   ```

2. **Create the `control` file** — save the content from the section above to `iPhotron_4.30_amd64/DEBIAN/control` and ensure it is not world-writable:

   ```bash
   chmod 644 iPhotron_4.30_amd64/DEBIAN/control
   ```

3. **Build the package**:

   ```bash
   dpkg-deb --build iPhotron_4.30_amd64
   ```

   This produces `iPhotron_4.30_amd64.deb` in the current directory.

4. **Verify the package**:

   ```bash
   dpkg-deb --info iPhotron_4.30_amd64.deb
   dpkg-deb --contents iPhotron_4.30_amd64.deb
   ```

## Installation

Install the generated package with:

```bash
sudo apt install ./iPhotron_4.30_amd64.deb
```

Or using `dpkg` directly (and then resolving any missing dependencies):

```bash
sudo dpkg -i iPhotron_4.30_amd64.deb
sudo apt-get install -f   # fix missing dependencies if any
```

## Removal

```bash
sudo apt remove iPhotron
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `dpkg-deb: error: control directory has bad permissions` | `DEBIAN/` directory not mode 755 | `chmod 755 iPhotron_4.30_amd64/DEBIAN` |
| `dpkg: dependency problems` after install | Missing runtime libraries | Add `Depends:` line to `control` listing required packages |
| Binary not found after install | Wrong install path in staging tree | Ensure binary is under `usr/local/bin/` or another directory in `$PATH` |
