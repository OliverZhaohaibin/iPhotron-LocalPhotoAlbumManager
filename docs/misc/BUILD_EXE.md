# Building the Executable with Nuitka and AOT

This document outlines the process for building the iPhoto executable, including the mandatory Ahead-Of-Time (AOT) compilation step for Numba filters.

## Overview

The application uses **Numba** for JIT-compiled image processing kernels at
development time. For release builds the kernels are compiled ahead-of-time
(AOT) into a native C-extension so that the heavy `numba` and `llvmlite`
packages can be completely excluded from the final distribution.

All Numba imports across the codebase use **conditional (try/except)** import
guards. When the AOT-compiled extension (`_jit_compiled`) is present the
application loads it directly; when it is absent and Numba is installed it
falls back to runtime JIT; otherwise it uses a pure-NumPy implementation.
This means the executable works correctly without `numba` or `llvmlite`
installed as long as the AOT module has been built.

## Prerequisites

1. Install the development dependencies (Numba is required for the AOT
   compilation step):

   ```bash
   pip install .[dev]
   ```

2. Ensure **Nuitka** is installed:

   ```bash
   pip install nuitka
   ```

## Step 1: AOT Compilation

Before packaging with Nuitka, you **must** compile the Numba JIT filters into
a C-extension. This step uses Numba's `pycc` AOT compiler to produce a
platform-specific shared library.

Run the build script:

```bash
python src/iPhoto/core/filters/build_jit.py
```

This will generate a shared object file in `src/iPhoto/core/filters/`:

- Linux: `_jit_compiled.cpython-<version>-<arch>-linux-gnu.so`
- Windows: `_jit_compiled.pyd`
- macOS: `_jit_compiled.cpython-<version>-darwin.so`

### Verify the AOT module

```bash
python -c "from iPhoto.core.filters import _jit_compiled; print('AOT module loaded successfully')"
```

## Step 2: Build with Nuitka

When building with Nuitka, exclude **both** `numba` and `llvmlite` to
completely remove them from the final binary. The application detects the
AOT module at import time and will never attempt to load Numba.

Example Nuitka command (adjust paths for your platform):

> **Note:** The entry point `src/iPhoto/gui/main.py` is used as an example.
> Verify and adjust this path to match your project's actual entry point if
> it differs.

```bash
nuitka --standalone \
    --nofollow-import-to=numba \
    --nofollow-import-to=llvmlite \
    --nofollow-import-to=pytest \
    --nofollow-import-to=iPhoto.tests \
    --include-package=iPhoto \
    --output-dir=dist \
    src/iPhoto/gui/main.py
```

### Startup-speed optimized build profile (recommended)

If launch latency is the top priority, prefer a **directory-based standalone build**
instead of onefile packaging. Onefile executables must unpack at process start,
which can dominate cold-start time on slower disks.

```bash
nuitka --standalone \
    --python-flag=no_site \
    --lto=yes \
    --clang \
    --follow-imports \
    --nofollow-import-to=numba \
    --nofollow-import-to=llvmlite \
    --nofollow-import-to=pytest \
    --nofollow-import-to=iPhoto.tests \
    --include-package=iPhoto \
    --assume-yes-for-downloads \
    --output-dir=dist \
    src/iPhoto/gui/main.py
```

Notes:

- `--python-flag=no_site` skips importing `site` at startup, reducing process init overhead.
- `--lto=yes` + `--clang` can improve generated binary performance (build time increases).
- For fastest startup, **do not add `--onefile`**.

### Key flags explained

| Flag | Purpose |
|---|---|
| `--nofollow-import-to=numba` | Prevents Nuitka from bundling the `numba` package |
| `--nofollow-import-to=llvmlite` | Prevents Nuitka from bundling the `llvmlite` package (dependency of `numba`) |
| `--nofollow-import-to=pytest` | Prevents Nuitka from bundling `pytest` (only needed for development) |
| `--nofollow-import-to=iPhoto.tests` | Excludes the in-tree test sub-package from the build |
| `--include-package=iPhoto` | Ensures all iPhoto sub-packages (including the AOT `.so`/`.pyd`) are included |

## Step 3: Verify the Distribution

After building, confirm that:

1. The `_jit_compiled` extension exists inside the packaged
   `iPhoto/core/filters/` directory:

   ```bash
   # Linux / macOS
   find dist/ -name "_jit_compiled*"
   # Windows (PowerShell)
   Get-ChildItem -Recurse dist/ -Filter "_jit_compiled*"
   ```

2. Neither `numba` nor `llvmlite` are present in the distribution:

   ```bash
   # Should produce no output
   find dist/ -type d -name "numba"
   find dist/ -type d -name "llvmlite"
   ```

3. The application starts and image adjustments work correctly.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `AOT compiled module not found` in logs | `_jit_compiled` extension missing from distribution | Re-run Step 1 and rebuild; verify the `.so`/`.pyd` file is in `iPhoto/core/filters/` |
| `ImportError` referencing `numba` at runtime | A code path still has an unconditional numba import | All numba imports must use `try/except ImportError` guards |
| Image adjustments produce no visible effect | Kernel not loaded — check logs for error messages | Ensure the AOT module matches the current Python version and platform |
| `Undesirable import of 'pytest'` warning from Nuitka | `iPhoto.tests` sub-package is being compiled into the build | Add `--nofollow-import-to=pytest` and `--nofollow-import-to=iPhoto.tests` to the Nuitka command |
