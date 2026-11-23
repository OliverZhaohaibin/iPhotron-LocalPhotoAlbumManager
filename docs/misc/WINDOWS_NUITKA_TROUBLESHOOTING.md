# Windows Nuitka packaging troubleshooting

Use [BUILD_EXE.md](BUILD_EXE.md) for the maintained release procedure. This
document records the Windows packaging failures resolved on 2026-11-23 and how
to distinguish source, frozen-module, and data-file problems.

## Select the intended build profile

From the repository root, the maintained build is:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_nuitka_windows.ps1 `
  -PythonExe ..\.venv\Scripts\python.exe -OutputDir build -ConsoleMode force
```

The interpreter path above describes a parent-directory venv; choose the venv
actually used by your checkout. Output is `build/entrypoint.dist/entrypoint.exe`.
The entry is `src/entrypoint.py`, which also preserves helper dispatch before Qt
imports. Keep the AOT and optional-runtime prerequisites in the main guide.

In the incident workspace, a separate parent-level `build.py` compiled
`iPhotos/src/iPhoto/gui/main.py` to
`iPhotos/build_output_debug/main.dist/main.exe`. That custom debug profile was
fixed and confirmed working by the user. It is outside this Git repository;
the maintained release script has its own output name, entry point, optional
asset policy and build manifest. Do not confuse either executable with the
other when checking timestamps or launching a newly built version.

## Resolved failures and durable fixes

| Symptom | Confirmed cause and fix |
| --- | --- |
| `packaging.tags` fails with `extra micro pass` / `loop_analysis` | Nuitka `--debug` enabled compiler self-checks. Remove it and the two dependent `--no-debug-*` options. `--unstripped` may retain native symbols without those self-checks. This avoids the observed assertion; it does not claim to fix Nuitka's optimizer. |
| No build output for a long time | The custom Python driver used `capture_output=True`, buffering output until completion. Inherit stdout/stderr, flush the parent streams, and use unbuffered Python plus `--show-progress`. |
| Cannot import `configure` from `startup_profile` | The venv's editable install resolved `iPhoto` to a different, older checkout. Put the intended absolute `src` first on the compiler's `PYTHONPATH` and verify the imported file and export before compiling. |
| `No module named 'iPhoto.resources'`, then unsupported `zh-CN` | `importlib.resources.files('iPhoto.resources.i18n')` dynamically imports a Python package. Include that package (or all of `iPhoto`) and separately include `languages.json` and the `.qm` catalogs. The directory is named `resources`, plural. |
| Photos and videos remain blank; Qt `initialize()` reports `__reduce_cython__` | `OpenGL_accelerate.formathandler` was absent from the frozen module table. Include the complete `OpenGL_accelerate` package and rebuild. The first failed accelerator import can produce a misleading secondary Cython error on retry. |
| Missing shader or pipeline initialization fails | Include both the GLSL sources and the serialized QRhi QSB files at their module-relative paths. Shader families are not interchangeable. |
| People & Pets dashboard reports invalid model manifest | Include `iPhoto/pets/model_manifest.json` independently of optional downloaded or bundled model weights. |

## Required media and language payload

The maintained script includes the following, even for the base package:

- Python packages `OpenGL`, `OpenGL_accelerate`, and `iPhoto.resources.i18n`
  (the latter through `--include-package=iPhoto`).
- `iPhoto/resources/i18n/languages.json`, `iPhoto_zh_CN.qm`, and `iPhoto_de.qm`.
- `iPhoto/pets/model_manifest.json`.
- In `iPhoto/gui/ui/widgets/`, the two `gl_image_viewer.vert` / `.frag` files;
  the `.vert` / `.frag` sources for `image_viewer_rhi`,
  `image_viewer_overlay`, and `video_renderer`; and their six `.qsb` files.

Windows production uses the existing OpenGL backend by default. The raw
OpenGL viewer reads `gl_image_viewer.*`; the QRhi image/overlay/video paths read
QSB files. Copying only another shader family's `.vert` / `.frag` files does
not satisfy that requirement. Do not switch to experimental D3D11 as a
packaging workaround; see [Windows graphics support](WINDOWS_D3D11_QRHI_STATUS.md).

## When a rebuild is required

Rebuild after changing the source checkout, Python modules, native extension
inclusion or compiler flags. In the affected standalone, copying
`formathandler.pyd` into the package directory did not register it with
Nuitka's frozen importer. The old generated `main.build/__loader.c` contained
`OpenGL_accelerate.arraydatatype` but omitted `OpenGL_accelerate.formathandler`.
The corrected build must contain both; the compilation report also helps audit
which files and modules were selected.

Missing plain data files can sometimes be replaced at the exact expected
relative path and read after restart. That is a diagnostic repair, not a
release procedure: update the script and rebuild a reproducible distribution.
A working source import or GLSL compilation test does not prove the frozen
application can load its native extensions.

## Capture startup and playback errors

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_packaged_diagnostic.ps1 `
  -Executable .\build\entrypoint.dist\entrypoint.exe
```

For a custom output, pass its executable explicitly. The collector redirects
stdout and stderr to separate files, sets `IPHOTO_LOG_DIR` and
`PYTHONFAULTHANDLER` for the launched process, waits for exit, records the exit
code, and restores the caller's environment. Default output is under
`build/packaged-diagnostics/<timestamp>/`; use `-OutputDir` to change it.
It uses the application's normal settings/library selection, so prepare a
dedicated test library. Logs may contain local paths; review before sharing.

Inspect the first exception around opening media, not just the final exit
code. The failing build returned zero even though a Qt initialization callback
raised and playback stayed blank. For rendering failures after imports succeed,
use the [scan/playback collector](../WINDOWS_SCAN_PLAYBACK_DIAGNOSTICS.md).

## Validation recorded for this incident

Environment: Windows x86-64, Python 3.12.6, Nuitka 4.1.3, packaging 25.0,
PyOpenGL-accelerate 3.1.10. These are diagnostic facts, not newly pinned
dependency requirements.

- The optimizer assertion was reproduced with `--debug`; `packaging.tags`
  optimization and C generation passed without it.
- The corrected compiler environment resolved `startup_profile` to the intended
  checkout and exposed `configure`.
- Language metadata and both QM catalogs loaded with Qt; six QSB files
  deserialized successfully. The GLSL viewer shaders compiled and linked using
  a native OpenGL context.
- A real Nuitka standalone probe importing `OpenGL.GL`, NumPy and ctypes passed
  imports of `OpenGL_accelerate.arraydatatype`, `formathandler`, `wrapper`, and
  `numpy_formathandler` after including the full accelerator package.
- After rebuilding the application, the user confirmed normal display and
  operation. This is playback acceptance for that build; it does not establish
  unrelated People/Pets scanning, offline Maps or cross-platform acceptance.
