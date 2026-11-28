# People & Pets extension packaged validation

Record the app version, source revision, OS and architecture, installation
root, model archive SHA-256, and pass or fail for each scenario.

| Scenario | Expected result |
| --- | --- |
| Windows standard user, app in `Program Files` | The Settings action is available without a bound library. The ZIP installs to the LocalAppData user directory without elevation. |
| Signed macOS app and Linux AppImage | A clean install can import the official ZIP offline. The user model directory receives both components. |
| First use on all three platforms, with the optional AI runtime and empty caches | Opening People & Pets with pending work silently downloads the official ZIP and installs both model sets. A failed install produces one app-themed prompt with browser download and file installation. Starting the app or rescanning metadata before People opens leaves recognition inactive. |
| Browser fallback | The default browser opens the official attachment. If it cannot open, the same URL can be copied. Returning to the app allows ZIP selection without network access. |
| Valid ZIP while one or both workers are paused | The same library resumes affected scans after installation. Check recognition on at least one photo containing a face and one photo containing a cat or dog. Existing recognition state stays available. |
| Nonempty but damaged Face ONNX file | The worker keeps pending photos and enters model recovery. A successful verified install resumes the Face worker. |
| Invalid `IPHOTO_FACE_MODEL_DIR` | The app prompts the user to fix or unset the override without downloading the ZIP. Repairing the directory and rescanning resumes Face recognition. |
| Quit during a stalled silent download | The reply is cancelled and the app exits promptly. A subsequent install can retry without a leftover partial archive or held lock. |
| Offline import with a wrong or damaged ZIP | A themed error appears and the current model installation remains usable. |
| English, German, and Simplified Chinese with light and dark app themes | Menu labels and prompts are translated, buttons fit, and popups center over the main window. |

Use a fresh user profile or isolated model directories for the empty-cache
cases. The generated v2 ONNX ZIP is **100,318,770 bytes**, SHA-256
`a8ac03fb0b60b44c6187defadf3311c08440a7a306cc4b9bee10b0f1cb2b8d5c`.
Its release contract has `release_status: published` and the verified
[official v2 ZIP download](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/People%26Pets-models-v2/iphotron-recognition-models-v2.zip).
The actual HTTPS attachment matched the recorded byte size and SHA-256.
Packaged online/browser scenarios above still require target-OS validation.
Old v1 clients retain their original ZIP.

Add upgrade cases: install v2 beside v1, corrupt/cancel the v2 upgrade and verify
People still reads v1, reject a v1 ZIP as v2, and verify `.pt` alone cannot make
Pets ready. Preserve all library DBs, covers, redirects and pending journal work.
macOS real Nuitka, signed-app and release-size checks are
**Pending Manual Validation**, by maintainer instruction. Source and fixture
Packaging Contract Tests do not establish packaged acceptance. See the
[complete ONNX matrix and commands](UNIFIED_ONNX_RUNTIME_VALIDATION.md).

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q \
  tests/infrastructure/test_recognition_extension_installer.py \
  tests/gui/ui/controllers/test_recognition_extension_controller.py \
  tests/test_library_manager.py tests/test_people_pipeline.py tests/test_information_popup.py \
  tests/test_people_service.py tests/test_pet_service.py \
  tests/application/test_runtime_context.py tests/architecture
.venv/bin/python tools/check_architecture.py
```

Run the [map extension regressions](MAP_EXTENSION_INSTALL_VALIDATION.md) whenever the shared archive code changes.
