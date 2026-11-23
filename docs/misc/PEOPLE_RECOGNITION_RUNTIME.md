# People Recognition Runtime

Current face runtime, local-model loading, scan status, and durable People state.
The [architecture](../architecture.md#recognition-coordination) owns shared
recognition coordination; [Pets runtime](PETS_RECOGNITION_RUNTIME.md) owns pet
detection and clustering.

## Runtime contract

Install the optional Python runtime with `python -m pip install -e ".[ai-demo]"`.
The People feature detects faces with InsightFace, stores embeddings, and
clusters them into people. Service binding and cached dashboard warm-up must
not initialize the pipeline. Desktop scanning follows the shared
[recognition activation gate](../architecture.md#scan-and-index).

`IPHOTO_FACE_MODEL_DIR` explicitly selects the directory containing `buffalo_s/`.
An invalid override reports an actionable warning rather than silently
downloading into another directory. Without an override, the worker searches
the installed combined extension (including its interrupted-promotion backup),
then the legacy platform cache, then the bundled `extension/models` fallback.
The [shared extension contract](../architecture.md#recognition-extension-installation)
owns installation roots, automatic recovery, and the process-wide attempt limit.

Legacy caches remain readable:

| Platform | Legacy model root |
| --- | --- |
| Windows | `%LOCALAPPDATA%\iPhoto\extensions\faces\v1\models` |
| macOS | `~/Library/Caches/iPhoto/models` |
| Linux | `${XDG_CACHE_HOME:-~/.cache}/iPhoto/models` |

In a source checkout, these are optional ignored local/release staging inputs:

| Path | Purpose |
|------|---------|
| `src/extension/models/buffalo_s/` | Optional local/release InsightFace staging cache |
| `src/extension/models/buffalo_s/det_500m.onnx` | Face detector |
| `src/extension/models/buffalo_s/w600k_mbf.onnx` | Face recognition embedding model |

These files are not tracked and are not guaranteed in a fresh clone. For local
debugging, override the model root with:

```powershell
$env:IPHOTO_FACE_MODEL_DIR = "D:\tmp\iphoto-models"
```

The production pipeline loads local ONNX files with `get_model(...,
download=False)`. It attaches those sessions to `FaceAnalysis` without calling
the upstream constructor that can download a pack. Missing or damaged models
are typed availability failures handled by the shared extension workflow;
do not reintroduce InsightFace downloads or writes into read-only package data.
An offline-ready release must supply both the runtime and usable model files.

## InsightFace import rules

Always import the concrete FaceAnalysis module through the People pipeline's
compatibility path:

```python
from insightface.app.face_analysis import FaceAnalysis
```

Do not switch back to:

```python
from insightface.app import FaceAnalysis
```

The package-level import pulls in InsightFace's mask-rendering path, which can
drag in `albumentations` and `pydantic`. In Nuitka builds this has produced
runtime annotation failures such as `name 'Literal' is not defined` and
`name 'NDArray' is not defined`.

The pipeline intentionally installs two compatibility shims before importing
InsightFace:

- runtime typing names in `builtins`, for third-party annotations evaluated at
  runtime inside packaged apps
- a lightweight `albumentations` stub, because iPhotron does not use
  InsightFace mask rendering

Keep these shims in place unless the packaging strategy changes and the
replacement has been verified in a Nuitka build.

## Required InsightFace modules

People clustering only needs bounding boxes and embeddings. The pipeline keeps
only the `detection` and `recognition` sessions and prepares that local model
set. Preserve this restriction without restoring the downloading constructor.

Do not load `landmark_2d_106`, `landmark_3d_68`, or `genderage` for the People
scan. Those models are not used for clustering and have caused packaged
asset-level failures such as:

```text
'NoneType' object has no attribute 'shape'
```

If a future feature needs landmarks or gender/age attributes, add that feature
behind a separate tested path and validate it in a Nuitka package before
enabling it for background scanning.

## Scan status and retry rules

Face scan state is stored per asset. The intended behavior is:

- `pending`: asset has not been scanned yet
- `done`: scan completed, with or without detected faces
- `skipped`: asset is not eligible for face scanning
- `retry`: asset failed once and should be attempted again
- `failed`: asset failed after a retry and should not block the whole queue

Important rules:

- A batch-level exception should pause scanning and surface the real exception
  text in the People page.
- An asset-level exception should be logged, then marked `retry` on the first
  failure.
- If the same asset fails again while already in `retry`, mark it `failed` so
  the queue can continue.
- A full library rescan must reset `retry` and `failed` face statuses back to
  the initial status. It should preserve only stable completed states such as
  `done` and `skipped` when the asset identity is unchanged.

This prevents a broken image or transient packaged-runtime issue from
deadlocking People scanning until the user deletes the database manually.

## Stable People state

Keep the People runtime snapshot and user decisions separate:

- `.iPhoto/faces/face_index.db` is the rebuildable runtime snapshot containing
  detected faces and clustered person records.
- `.iPhoto/faces/face_state.db` stores names, canonical identities, selected
  covers, hidden flags, manual faces, the shared People & Pets card order,
  groups, group order, group covers, and group asset caches. See the
  [persistence model](../architecture.md#persistence-model) for sidebar pin storage.
- A scan commit may recluster all faces and rewrite the runtime snapshot, but it
  must preserve the stable state and repair it through repository/coordinator
  APIs instead of dropping it.
- Group asset caches must be refreshed when scan commits, merges, manual face
  edits, person deletion, or group membership changes can affect common-photo
  results.
- People in different hidden states must not be merged. Keep this enforced in
  both UI and repository/service layers.

## Debugging packaged face scan failures

The app writes rotating logs to:

```powershell
%LOCALAPPDATA%\iPhoto\iPhoto.log
```

For custom locations:

```powershell
$env:IPHOTO_LOG_DIR = "D:\tmp\iphoto-logs"
```

Useful log messages:

- `Face scanning paused: ...`
  Batch-level failure; the message should include the actual exception.
- `Face detection failed for ...`
  Full traceback for an asset-level detection failure.
- `Face scan failed for asset ...`
  The worker marked a specific asset for retry or failure.

When a packaged build says `Some assets could not be face scanned and will be
retried after a rescan`, inspect the log before changing model paths. That
message means model initialization succeeded far enough to process assets, but
at least one asset failed during detection/embedding.

## Verification checklist

After changing People scanning, run the focused tests:

```powershell
python -m pytest tests\test_people_pipeline.py tests\test_people_service.py tests\cache\test_global_repository.py tests\test_face_cluster_pipeline.py
```

When changing People UI, groups, covers, hidden-state filtering, merges, or
popup/menu behavior, also run:

```powershell
python -m pytest tests\gui\widgets\test_people_dashboard_widget.py tests\test_people_repository.py tests\test_people_service.py tests\test_information_popup.py tests\ui\controllers\test_context_menu_cover.py
```

For a local smoke test, copy an image as `sample.jpg` into the disposable
`C:\tmp\iphoto-people-smoke` folder first and provision the model staging root.
The command writes crops into that test folder; never point it at a production
library:

```powershell
python -c "from pathlib import Path; from iPhoto.people.pipeline import FaceClusterPipeline; root=Path(r'C:\tmp\iphoto-people-smoke'); p=FaceClusterPipeline(model_root=Path('src/extension/models')); out=p.detect_faces_for_rows([{'id':'test','rel':'sample.jpg'}], library_root=root, thumbnail_dir=root/'.iPhoto'/'faces'/'thumbnails'); print([(x.asset_rel, x.error, len(x.faces)) for x in out]); print(sorted(p._ensure_face_analysis().models.keys()))"
```

The loaded InsightFace models should be only:

```text
['detection', 'recognition']
```

For release verification, rebuild with Nuitka and test the People page from the
packaged executable, not from the editable source checkout.
Extension lifecycle changes also require the
[People & Pets installation matrix](../requirements/PEOPLE_PETS_EXTENSION_INSTALL_VALIDATION.md).
