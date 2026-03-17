# Build Scan C Extension

The scan hot-path native library uses an AOT workflow.

That means:

- The app never compiles C code at runtime.
- Python first tries to load a prebuilt native binary.
- If the binary is missing, fails to load, or does not export the expected symbols, the app falls back to the Python implementation automatically.

## Output Locations

Native source directory:

- [src/iPhoto/_native](../../src/iPhoto/_native)

Runtime-loaded binaries:

- Windows: `src/iPhoto/_native/_scan_utils.dll`
- Linux: `src/iPhoto/_native/_scan_utils.so`
- macOS: `src/iPhoto/_native/_scan_utils.dylib`

Build helper artifacts may appear under:

- `src/iPhoto/_native/build/windows`
- `src/iPhoto/_native/build/linux`
- `src/iPhoto/_native/build/darwin`

## Windows Build

Requirements:

- Visual Studio 2022 C++ tools
- `vcvars64.bat`

This repository currently uses:

- `C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat`

Build:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_scan_utils.ps1
```

Clean and rebuild:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_scan_utils.ps1 -Clean
```

Successful output:

- `src/iPhoto/_native/_scan_utils.dll`
- `src/iPhoto/_native/build/windows/scan_utils.lib`
- `src/iPhoto/_native/build/windows/scan_utils.pdb`

## Linux And macOS Build

Requirements:

- `cc`, `gcc`, or `clang`

Build:

```bash
bash scripts/build_scan_utils.sh
```

Platform output:

- Linux: `src/iPhoto/_native/_scan_utils.so`
- macOS: `src/iPhoto/_native/_scan_utils.dylib`

To pick a specific compiler:

```bash
CC=clang bash scripts/build_scan_utils.sh
```

## Runtime Loading

At runtime Python tries to load the platform binary first:

- Success: the scan runs in `C extension` mode.
- Failure: the scan runs in `Python fallback` mode.

No runtime JIT compilation is performed.

## How To Confirm The Active Runtime

Every scan action prints the actual active runtime mode at the start.

Examples:

```text
Scan backend: C extension
```

```text
Scan backend: Python fallback (native library missing: _scan_utils.dll)
```

When the scan finishes, the console prints the same mode plus total elapsed time:

```text
Scan finished (C extension) in 3.42s
```

```text
Scan finished (Python fallback) in 8.91s
```

This applies to:

- full scans
- `rescan`
- incremental scans such as `scan_specific_files`

## Troubleshooting

`Scan backend: Python fallback (native library missing: ...)`

- The prebuilt binary does not exist yet, or the filename is wrong.
- Run the platform build script, then start the app again.

`Scan backend: Python fallback (native library load failed: ...)`

- The binary exists, but it does not match the current platform or required runtime dependencies.
- Common causes include architecture mismatch, a broken binary, or missing system runtime files.

`Scan backend: Python fallback (native symbol binding failed: ...)`

- The binary exports do not match the current Python-side ABI.
- Rebuild the native library from the current repository sources.

Windows build script cannot find `vcvars64.bat`

- Check the installed Visual Studio version and installation path.
- If your path differs, update `$vcvarsPath` in `scripts/build_scan_utils.ps1`.

Linux or macOS build script cannot find a compiler

- Install `cc`, `gcc`, or `clang`.
- Or set one explicitly with `CC=/path/to/compiler`.
