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
cases. The official ZIP is 100,497,212 bytes with SHA-256
`73819048dcf2d9352758a544a751be67512c18e0be458c295551b7142e91fa28`.
[Official People & Pets ZIP](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/pet-models-v1/iphotron-recognition-models-v1.zip)

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
