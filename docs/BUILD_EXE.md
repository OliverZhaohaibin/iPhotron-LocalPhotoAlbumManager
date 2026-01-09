# Building the Executable with Nuitka and AOT

This document outlines the process for building the iPhoto executable, including the mandatory Ahead-Of-Time (AOT) compilation step for Numba filters.

## Prerequisites

Ensure you have the development dependencies installed:

```bash
pip install .[dev]
```

## Step 1: AOT Compilation

Before packaging with Nuitka, you must compile the Numba JIT filters into a C-extension. This improves startup performance and allows Nuitka to strip the heavy `numba` and `llvmlite` compiler dependencies from the final distribution.

Run the build script:

```bash
python src/iPhoto/core/filters/build_jit.py
```

This will generate a shared object file (e.g., `_jit_compiled.cpython-312-x86_64-linux-gnu.so` or `.pyd` on Windows) in `src/iPhoto/core/filters/`.

## Step 2: Build with Nuitka

When building with Nuitka, you should exclude the `numba` package to reduce binary size. The application is designed to detect the presence of the AOT module and skip loading Numba.

Example Nuitka command (adjust as needed for your specific platform/requirements):

> **Note:** The entry point `src/iPhoto/gui/main.py` is used as an example. Please verify and adjust this path to match your project's actual entry point if it differs.
```bash
nuitka --standalone --nofollow-import-to=numba --include-package=iPhoto --output-dir=dist src/iPhoto/gui/main.py
```

**Note:** Ensure that the generated `_jit_compiled` extension is included in the package. Nuitka usually detects binary extensions in included packages automatically.

## Troubleshooting

If the application fails to start with errors related to image adjustments:
1. Verify that the `_jit_compiled` file exists in the `iPhoto/core/filters` directory of the distribution.
2. Check logs for "AOT compiled module not found".
