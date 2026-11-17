# Map extension packaged release validation

Status: **manual packaged validation required**. Source/offscreen tests do not
prove the Windows installer payload, native Qt/DLL compatibility or graphics.
Do not close #920 solely because unit tests pass or a source tag uses LocalAppData.

Record app version, executable SHA-256, source revision from the build manifest,
OS/architecture, actual install root, extension hash and pass/fail for each row.
Attach the build manifest and completed matrix to release validation evidence.

| Target / scenario | Required result |
| --- | --- |
| Windows 11, ordinary user, app under Program Files | No elevation; all new map files under LocalAppData; package folder unchanged |
| Windows, Chinese user path and TEMP on another volume | Extraction/staging uses target volume; no rename across volumes or path-length failure |
| Windows, offline official ZIP | No network; hash verification, installation, restart, map rendering and place search succeed |
| Windows VPN/TUN, GitHub or release asset host fails to resolve | Details identify the failed host and DNS code. Download in Browser opens the official asset and offers Install from File. Import the archive and verify map rendering and search. |
| Map and People & Pets download popups, light and dark themes, English, German and Simplified Chinese | Both extensions use matching popup and progress styles. Map Details expand and can be copied. Keyboard dismissal leaves the Settings import entry available. |
| Windows, local proxy port closed | Classified connection refusal after one download attempt. Dialog suggests checking VPN or proxy settings and offers browser download and local installation. |
| Windows, another installer briefly holds the map installation lock | First-use preparation offers Retry before probing the runtime. Once the lock is released, Retry activates the staged extension. Cancel loads the available fallback. |
| Windows, DLL in use / interrupted activation | Old installation retained or restored; validated pending survives; next launch activates before DLL load |
| Windows, old Program Files extension.pending | Valid files copied into user location; old protected directory unchanged |
| Windows, corrupt pending / ZIP / wrong platform | Clear error; valid current installation retained |
| Windows, fresh install and existing bundled maps | Correct selected root for data, search, helper and native dependencies |
| Linux packaged app / AppImage | Online tar.xz and offline import; map rendering and search after restart |
| macOS signed bundle | Tar and checksum sealed before signing; offline first-use preparation, rendering and search |
| All platforms, extension unavailable | Gallery, editing and library state remain functional; optional maps degrade gracefully |

Verify archive CRC, byte size, SHA-256 and layout before publishing a replacement
package. Validate native runtime against the release Python/Qt architecture.
If the historical v5.0.0 payload fails the native acceptance check, publish a
new compatible immutable asset and pin its identity before releasing the app;
do not replace the bytes at the existing public URL.

Automated regression suite:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q \
  tests/infrastructure/test_map_extension_installer.py \
  tests/ui/tasks/test_map_extension_download_worker.py \
  tests/gui/ui/controllers/test_map_extension_download_controller.py \
  tests/gui/coordinators/test_location_info_coordinator.py \
  tests/test_map_sources.py tests/test_map_runtime_service.py \
  tests/test_photo_map_view.py tests/test_build_manifest.py \
  tests/application/test_runtime_context.py tests/architecture
.venv/bin/python tools/check_architecture.py
```
