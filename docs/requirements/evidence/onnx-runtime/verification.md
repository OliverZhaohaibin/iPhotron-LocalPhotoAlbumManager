# Verification ledger

All runs used source code or disposable libraries/settings. No real Nuitka
compilation, full application build, release signing or final app/release size
measurement was performed. macOS packaged acceptance is **Pending Manual
Validation**. The generated v2 model ZIP is a model asset, not a compiled app.

## Actual results

### Published v2 URL follow-up — 2026-11-28

The maintainer supplied the [actual v2 ZIP download](https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/releases/download/People%26Pets-models-v2/iphotron-recognition-models-v2.zip).
GitHub Release metadata and a full HTTPS download both matched 100,318,770 bytes
and SHA-256 `a8ac03fb0b60b44c6187defadf3311c08440a7a306cc4b9bee10b0f1cb2b8d5c`.
The contract now uses that direct URL and `release_status: published`.
The existing ZIP builder performed the remote verification using the trusted
certifi CA bundle (`SSL_CERT_FILE`); TLS verification remained enabled.
Installer, GUI controller, RuntimeContext and current-documentation regressions
passed: **40 passed in 1.25s**. Native packaged acceptance, including all macOS
Nuitka/signing/size checks, remains **Pending Manual Validation**.

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q \
  tests/infrastructure/test_recognition_extension_installer.py \
  tests/gui/ui/controllers/test_recognition_extension_controller.py \
  tests/application/test_runtime_context.py \
  tests/contracts/test_current_documentation_contracts.py
```

### Refactor verification

| Check | Result |
| --- | --- |
| Pinned TorchScript → FP32 opset 17 ONNX | ONNX checker, real ORT inference and raw/normalized/distance/clustering comparisons passed; exact ONNX bytes reproduced on re-export |
| Original InsightFace vs injected shared CPU sessions | Public astronaut: one face; boxes, landmarks and raw embeddings identical |
| New temporary Torch-free source venv, real DINOv2 + official YOLOX and runtime contracts | **40 passed in 0.98s** |
| Final source runtime/Pets regression selection | **212 passed in 6.53s** |
| Final architecture, provider/model, installer/controller, documentation and all packaging contracts | **129 passed, 1 skipped in 9.17s**; skipped actual Windows GUI subsystem |
| Final dependency-namespace/preflight helper rerun | **17 passed in 0.20s**; includes a second custom wheel occupying the ORT namespace |
| Source suite by directory/root-file groups, followed by repaired documentation and new-contract checks | **4,229 passed, 27 skipped** total coverage; later edits received targeted reruns, not another monolithic run |
| GUI directory group | **934 passed, 2 skipped** |
| UI directory group | **690 passed, 4 skipped** |
| Architecture directory after adding ONNX-only source guard | **24 passed** (included in final contract selection) |
| 50k fast index, 50k × 384 USearch, 1k × 384 fallback, bounded connections | **4 passed in 198.52s** |
| Actual generated v2 ZIP offline import and full model validation | Passed in a fresh temporary v2 installation root; whitelist staging also passed |
| Production dependency resolution with final CPU constraints | **88 resolved distributions**, no Torch/TorchVision/TorchAudio/Triton/NVIDIA package |
| CoreML source inference | Explicit CoreML+CPU outside the restricted sandbox passed cat/dog cosine ≥0.9999757; two samples were slower than CPU, so macOS defaults to CPU. Full GPU/performance/packaged acceptance remains pending |
| CoreML initialization failure in this macOS sandbox | Native compilation could not create its temporary directory; all four models recovered to CPU |
| Architecture checker, changed Python Ruff F/E9/I, compileall, i18n gate/resources, shell syntax, workflow/package YAML, diff whitespace | Passed |
| Remote Windows/Linux/macOS CPU CI and GPU/native acceptance | Pending; workflow added, not executed remotely in this session |

The first monolithic `tools/run_pytest_ci.py tests/ --tb=short -q` run stopped
around 85% with a native Qt segmentation fault during teardown/event processing
at `asset_delegate.py:151`. There was no complete pytest result, so that run is
**not passed**. Directory/root-file isolation completed the source suite. An
initial documentation contract still referenced `torchscript_url`; it was
updated to the ONNX schema and passed on rerun. The monolithic Qt crash remains
a validation limitation; isolated source tests do not prove native app stability.

The totals include platform/integration skips; real DINOv2/YOLOX and Pets scale
contracts were subsequently explicitly enabled in the separate runs above.
The root/directories grouping collected every test file; initial groups and the
follow-up contracts provide incremental coverage of the final changes. Raw
logs, the interrupted-run stack trace and group commands are retained under
`dist/onnx-migration/`, which is deliberately ignored by Git.

## Commands actually executed

Paths below use repository-local or disposable inputs. The additional source
CoreML probe ran outside the restricted execution sandbox, on the same public
cat/dog inputs; it performed no Nuitka compilation or packaging. The real v1 source was
read from an existing installation; it was never modified or executed by the
production application.

```bash
.venv/bin/python tools/export_dinov2_onnx.py \
  --torchscript "$V1_TORCHSCRIPT_SOURCE" \
  --output /private/tmp/iphotron-onnx/pets/embedding/dinov2_vits14/dinov2_vits14.onnx \
  --image "$PUBLIC_DOG_IMAGE" --image "$PUBLIC_CAT_IMAGE" \
  --report /private/tmp/iphotron-onnx/dinov2-equivalence.json \
  --reference-output /private/tmp/iphotron-onnx/pets/embedding/dinov2_vits14/torchscript-reference.npz
.venv/bin/python tools/build_recognition_models.py \
  --model-root /private/tmp/iphotron-onnx \
  --output /private/tmp/iphotron-onnx/iphotron-recognition-models-v2.zip \
  --contract src/iPhoto/recognition/recognition_package.json
.venv/bin/python tools/verify_people_onnx.py \
  --model-root dist/onnx-migration/models \
  --image .venv/lib/python3.12/site-packages/skimage/data/astronaut.png \
  --output docs/requirements/evidence/onnx-runtime/people-equivalence.json
.venv/bin/python -m pip install --dry-run --ignore-installed --no-build-isolation \
  -c packaging/recognition-cpu-constraints.txt \
  --report /private/tmp/iphotron-onnx/production-dependency-closure.json '.[recognition]'
/private/tmp/iphotron-onnx/runtime-venv/bin/python tools/recognition_packaging.py \
  environment --output /private/tmp/iphotron-onnx/clean-runtime-environment.json
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  IPHOTO_RUN_DINOV2_MODEL_CONTRACT=1 \
  IPHOTO_DINOV2_CONTRACT_DIR=dist/onnx-migration/models/pets/embedding/dinov2_vits14 \
  IPHOTO_RUN_PETS_MODEL_CONTRACT=1 \
  IPHOTO_PETS_CONTRACT_CACHE=/private/tmp/iphotron-onnx \
  /private/tmp/iphotron-onnx/runtime-venv/bin/python -m pytest -q \
    tests/contracts/test_dinov2_onnx_contract.py tests/contracts/test_pets_model_contract.py \
    tests/test_recognition_onnx_runtime.py tests/test_dinov2_onnx.py
QT_QPA_PLATFORM=offscreen IPHOTO_RUN_PETS_SCALE_CONTRACT=1 \
  IPHOTO_RUN_PETS_PRODUCTION_SHAPE_CONTRACT=1 NUMBA_DISABLE_JIT=1 \
  .venv/bin/python -m pytest -q -s tests/contracts/test_pets_scale_contract.py
python3 tools/check_architecture.py
.venv/bin/python -m pytest tests/architecture -q
.venv/bin/python tools/check_i18n_strings.py src/iPhoto/gui src/maps
PATH="$PWD/.venv/bin:$PATH" bash scripts/i18n_extract.sh
PATH="$PWD/.venv/bin:$PATH" bash scripts/i18n_compile.sh
.venv/bin/python -m compileall -q src tools
bash -n scripts/build_nuitka_macos.sh scripts/build_nuitka_fast.sh \
  scripts/build_appimage.sh scripts/build_flatpak.sh
git diff --check
```

Changed Python files also passed `ruff check --select F,E9,I`; new tools and
runtime files were formatted with Ruff. Black was not installed, so it was not
used. PyYAML parsed both workflow and custom Nuitka package configuration.
Intel ORT 1.23.2's official wheel source was inspected and confirmed to support
`enable_fallback=False`; this is source evidence, not Intel runtime execution.

[Complete change-file inventory](change-files.md) ·
[Main release/manual protocol](../../UNIFIED_ONNX_RUNTIME_VALIDATION.md)
