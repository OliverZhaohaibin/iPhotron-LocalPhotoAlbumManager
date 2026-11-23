# Windows Nuitka packaging troubleshooting

## Build history

The 2026-11-23 incident began in a separate parent-level `build.py` outside
this repository. It compiled `iPhotos/src/iPhoto/gui/main.py` to
`iPhotos/build_output_debug/main.dist/main.exe`. The maintained PowerShell
script builds `src/entrypoint.py`, which dispatches helper commands before Qt
imports. Check the executable path and timestamp when comparing artifacts.
[Maintained build procedure](BUILD_EXE.md#recommended-windows-build-script)

Before PR #951, the maintained Windows script already froze `OpenGL` and
`OpenGL_accelerate` and bundled translations, GLSL and QSB shaders, and the
Pets model manifest. Those inputs belong to the base package.

PR #951 brought checkout-first source selection and live compiler progress to
the maintained script. It added preflight checks for required data, language
catalogs, and the startup profiler import, plus the packaged diagnostic
launcher and this troubleshooting guide.

## Fixes made in the custom `build.py` workflow

| Symptom | Cause and fix |
| --- | --- |
| `packaging.tags` fails with `extra micro pass` or `loop_analysis` | Nuitka `--debug` enabled compiler self-checks that raised the assertion. Remove `--debug` and its two dependent `--no-debug-*` options. `--unstripped` retains native symbols when a custom profile needs them. |
| No build output for a long time | The custom driver used `capture_output=True` and held output until completion. Inherit stdout and stderr, flush the parent streams, and use unbuffered Python with `--show-progress`. Linking can still be quiet. |
| Cannot import `configure` from `startup_profile` | The venv's editable install pointed at an older checkout. Put the intended absolute `src` first on `PYTHONPATH`, then verify the module's file and callable export. |
| `No module named 'iPhoto.resources'`, followed by an unsupported `zh-CN` language | `importlib.resources.files('iPhoto.resources.i18n')` needs the frozen Python package and its files. Include the package through `iPhoto` or `iPhoto.resources`, then bundle `languages.json`, `iPhoto_zh_CN.qm`, and `iPhoto_de.qm` under `iPhoto/resources/i18n/`. The directory name is plural. |
| Photos or videos stay blank and Qt `initialize()` reports `__reduce_cython__` | The frozen module table omitted `OpenGL_accelerate.formathandler`. The first failed accelerator import can produce a secondary Cython error on retry. Include the complete `OpenGL_accelerate` package. |

## Check frozen modules and data files

In the affected standalone, `main.build/__loader.c` listed
`OpenGL_accelerate.arraydatatype` and omitted `OpenGL_accelerate.formathandler`.
Copying `formathandler.pyd` into the standalone package directory left the
frozen module table unchanged. Use `nuitka-compilation-report.xml` or the
generated loader to check module inclusion. Changes to Python or native module
inclusion, compiler flags, or the checkout being compiled require a new artifact.

For missing data, check the expected module-relative paths. The raw Windows
OpenGL viewer loads `gl_image_viewer.vert` and `gl_image_viewer.frag` from
`iPhoto/gui/ui/widgets/`. The QRhi image, overlay, and video paths load six
serialized `.qsb` shaders from the same directory. Keep them paired with the
`image_viewer_rhi`, `image_viewer_overlay`, and `video_renderer` GLSL sources in
the bundle. The Pets dashboard reads `iPhoto/pets/model_manifest.json` even when
model weights come from an extension. Use the default Windows OpenGL backend to
reproduce packaging issues. [Windows graphics support](WINDOWS_D3D11_QRHI_STATUS.md)

Placing a missing data file at its expected path can isolate the problem after
an app restart. For release, restore the data inclusion in the build profile
so it produces a reproducible distribution.

## Capture startup and playback errors

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_packaged_diagnostic.ps1 `
  -Executable .\build\entrypoint.dist\entrypoint.exe
```

Pass the executable explicitly for custom output paths. The launcher opens the
packaged app with normal window behavior, including builds with the Windows
console disabled. Use a dedicated test library because the app uses its
normal settings and library selection.

The launcher saves `stdout.log`, `stderr.log`, and `exit-code.txt` under
`build/packaged-diagnostics/<timestamp>/` by default. Set `-OutputDir` to
change the parent directory. It sets `IPHOTO_LOG_DIR` for the app's
`iPhoto.log`, enables `PYTHONFAULTHANDLER`, waits for the process to exit, and
restores the caller's environment. Outside the launcher, the default app log
is `%LOCALAPPDATA%\iPhoto\iPhoto.log`. Review logs for local paths before
sharing them.

An empty `stderr.log` means the launcher captured no stderr output. GUI
builds can report errors only through `iPhoto.log` or fail before application
logging starts. Check the app log when present, `stdout.log`, and the exit
code. In the incident, the app returned zero even though a Qt initialization
callback failed and playback stayed blank. Look for the first exception
around opening the media. [Nuitka console diagnostics](https://nuitka.net/user-documentation/common-issue-solutions.html#windows-programs-with-no-console-give-no-errors)

For rendering failures after imports succeed, use the
[scan and playback collector](../WINDOWS_SCAN_PLAYBACK_DIAGNOSTICS.md).

## Completed Windows packaging validation

The modified Windows packaging workflow completed a real Nuitka standalone
application build. The user launched the packaged executable and confirmed
normal operation, including photo and video rendering. The packaging and
rendering failures from this incident were resolved in that tested build.

The incident was tested on Windows x86-64 with Python 3.12.6, Nuitka 4.1.3,
packaging 25.0, and PyOpenGL-accelerate 3.1.10. The investigation also recorded
the following checks.

- `packaging.tags` optimization and C generation succeeded after removing
  the custom profile's `--debug` checks.
- The compiler environment resolved `startup_profile` to the intended
  checkout and exposed `configure`.
- Qt loaded the language metadata and both QM catalogs and deserialized all
  six QSB shaders. The GLSL viewer shaders compiled and linked in a native
  OpenGL context.
- A Nuitka standalone probe importing `OpenGL.GL`, NumPy, and `ctypes` loaded
  `OpenGL_accelerate.arraydatatype`, `formathandler`, `wrapper`, and
  `numpy_formathandler` after including the whole accelerator package.

Automated CI exercises the source probe and uses a small Windows GUI fixture
to check diagnostic log collection. The completed application run supplies the
evidence for the packaged build and its media rendering. People/Pets scanning,
offline Maps, and other platforms or deployment profiles have separate
acceptance checks.

## Earlier Windows packaging issues

| Symptom | Cause and fix |
| --- | --- |
| The build exits with code `9009` or opens the Microsoft Store Python prompt | Python resolved to the Windows App Execution Alias. Install the build dependencies into a supported venv or pass its interpreter with `-PythonExe`. The maintained script rejects the Store alias. |
| Nuitka reports `duplicate locals name` for `insightface.thirdparty.face3d` in both `.venv\insightface` and `.venv\Lib\site-packages\insightface` | The launch directory exposed a shadow InsightFace source tree. The maintained script runs from the checkout root and excludes the unused `insightface.thirdparty.face3d` tree. |

An older installer procedure combined an Inno Setup script with a separate
map archive. The current repository provides the standalone Nuitka build and
process-scoped Maps installation. [Maps extension workflow](MAP_EXTENSION_INSTALLATION.md)
