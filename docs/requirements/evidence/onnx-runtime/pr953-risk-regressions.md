# PR #953 targeted risk regression ledger

Source baseline: `3b60fe138b8849879d170999859983d3bf3cbf73`,
`codex/unified-onnx-runtime`. Tests ran on 2026-11-28, macOS arm64,
Python 3.12, installed ONNX Runtime 1.25.1. All mutated photos, SQLite files,
settings and installation fixtures were temporary. Real inference used the
existing verified local release models and frozen TorchScript reference
tensors; production inference and tests did not require a Torch import.

## Failure propagation and fixes

| Category | Shared runtime behavior | Pipeline/worker behavior |
| --- | --- | --- |
| Recognized accelerator/provider failure | One CPU session recovery and one retry of the same inference. Exhaustion is `RecognitionProviderError`, a runtime-unavailable subtype; the terminal cause is retained and the failed session released. | Face/Pet runtime-unavailable error exits only the owning worker. No asset retry or failure is submitted. |
| Runtime/native failure | Import, session creation, native `Fail`/`RuntimeException`, runtime and OS failures use `RecognitionRuntimeUnavailableError`. A failed running session becomes terminal. | Preserve pending/retry work and release worker resources. A later worker can retry after the runtime is usable. |
| Model/contract failure | Native invalid graph/protobuf/argument/operator failures use `RecognitionModelError`, without provider recovery. | Face/Pet model-unavailable errors use existing model recovery; pipeline invariant failures stop the worker. They never become individual asset failures. |
| Individual asset failure | Ordinary invalid image/path/decode or asset-specific errors retain the existing pipeline behavior. | Only these produce result `.error`, pending-to-retry and retry-to-failed transitions. |

The original failure chain was `ManagedOnnxSession.run()` -> Face analysis or
Pets detector/embedder generic catches -> result `.error` -> Worker ->
IndexCoordinator. People's coordinator stages failures and uses the attempted
status guard to journal retry/failed changes. Pets' worker splits errors into
first retries and terminal failures before the coordinator journals them.
Neither coordinator can infer a runtime cause from an error string.

The fix preserves the typed error before those generic catches. Both workers
already stop on typed availability errors and close pipelines in `finally`.
Their coordinator submission point remains after complete detection. The Pets
worker additionally removes its unique staging directory when detection
raises. Neither IndexCoordinator, its mutation lease/journal, nor the retry
state machine was redesigned. Existing finish handling does not immediately
restart a failed runtime worker; model installation resumption retains its
generation/revision guards.

Persistence tests inject failures through native session doubles, production
pipelines, scan workers, session services, actual journals and SQLite. They
cover both failure on the first asset and failure after two successful
inferences in an unfinished batch, plus queued assets outside that batch.
They verify unchanged pending/retry states, no failure journal admission,
unaffected recognition-kind state, staging cleanup, worker session release,
then library shutdown/reopen and successful queue drain with a healthy runtime.
Successful GPU-to-CPU recovery continues and commits normally. These are
failure/state tests, not GPU hardware tests.

People default lookup now verifies official pack identities before selecting
v2, backup, v1, legacy backup, cache or bundle. Custom explicit overrides retain
their existing loading path. Pets rejects non-object JSON, wrong size types,
invalid UTF-8, hash mismatch and incomplete directories through typed model
errors, and continues to a valid backup/bundled artifact when appropriate.
Installer malformed-JSON tests also verify an intact installation survives.

## Commands and observed results

Final combined regressions:

```bash
.venv/bin/python -m pytest -q \
  tests/test_recognition_onnx_runtime.py tests/test_recognition_runtime_recovery.py \
  tests/test_dinov2_onnx.py tests/test_people_pipeline.py tests/test_face_cluster_pipeline.py \
  tests/test_recognition_packaging.py tests/test_pet_packaging_contract.py tests/test_windows_packaging.py \
  tests/test_people_service.py tests/test_pet_service.py tests/test_pet_remediation_contracts.py \
  tests/test_recognition_operation_journal.py tests/test_recognition_merge_service.py \
  tests/application/test_recognition_query_service.py \
  tests/application/test_recognition_batch_queries.py tests/application/test_recognition_edit_service.py \
  tests/infrastructure/test_recognition_extension_installer.py \
  tests/gui/ui/controllers/test_recognition_extension_controller.py \
  tests/test_library_manager.py tests/gui/test_startup_import_boundary.py \
  tests/application/test_runtime_context.py tests/application/test_library_session.py --tb=short
```

Result: **720 passed, 1 skipped, 2 warnings in 41.53 s**. The skip requires the
Windows GUI subsystem. Both warnings are the pre-existing Python 3.12 warning
about `fork()` from a multithreaded test process in operation-journal tests.

```bash
python3 tools/check_architecture.py
.venv/bin/python -m pytest tests/architecture -q --tb=short
git diff --check
```

Architecture checker: passed. Architecture pytest: **24 passed**. Diff check:
passed. Ruff `--select F,E9` over the changed Python files also passed; it emitted
the existing deprecated top-level Ruff configuration warning.

After clearing the retained terminal exception on session teardown, reran:

```bash
.venv/bin/python -m pytest -q tests/test_recognition_onnx_runtime.py \
  tests/test_recognition_runtime_recovery.py --tb=short
```

Result: **55 passed in 9.80 s**.

Real ONNX CPU numeric and persisted mixed-history validation:

```bash
IPHOTO_RUN_DINOV2_MODEL_CONTRACT=1 \
IPHOTO_DINOV2_CONTRACT_DIR=dist/onnx-migration/models/pets/embedding/dinov2_vits14 \
IPHOTO_RECOGNITION_CONTRACT_MODEL_ROOT=dist/onnx-migration/models \
.venv/bin/python -m pytest -q \
  tests/contracts/test_dinov2_onnx_contract.py \
  tests/contracts/test_recognition_provider_contract.py --tb=short
```

Result: **6 passed, 20 skipped**. All accelerator cases explicitly skipped as
Pending Manual Validation. CPU covered RetinaFace, ArcFace, YOLOX, DINOv2,
legacy/new mixed clustering, persisted identity choices and unchanged embedding
generation. This is fixture evidence, not acceptance of an entire historical
user library.

The native inspector was also run directly on the installed ORT CPU binding
and dylib with `otool`. It reported Apple system frameworks/libraries and ORT
install names, with no CUDA or Torch native dependency. This inspected the
installed source runtime only; no application artifact was built or measured.

## Remaining acceptance

- CoreML, CUDA, DirectML and OpenVINO: **Pending Manual Validation** on real
  target devices, including numerical gates, actual node/device execution,
  runtime recovery and GUI responsiveness. Prior two-image CoreML source
  evidence remains limited to that probe.
- Representative historical-library copies: verify mixed embeddings, groups,
  covers, hidden state, redirects, rejected detections and unfinished journal
  recovery with real images and the target provider. Do not reset generations
  or clear existing recognition data to prepare acceptance.
- High-throughput desktop configuration: deferred pending single/dual-worker
  throughput, CPU/RSS, Gallery/Detail frame latency and startup gate measurements.
  The default two-thread maximum and existing scheduling stay unchanged.
- Windows/Linux native release and dynamically loaded dependency closure:
  pending target-host validation. Import-table fixtures do not prove OS/device
  support. The Windows GUI-subsystem check requires Windows.
- macOS full Nuitka compilation, App Bundle packaging, signing/notarization and
  release-size measurement: **Pending Manual Validation**, owned by the
  maintainer. None was performed. The test that builds/signs a macOS fixture
  App Bundle was deliberately excluded from this local regression run.

`PET_EMBEDDING_PIPELINE_VERSION`, production clustering thresholds, user merge
semantics and production inference dependencies remain unchanged. LTO and both
manifest collection rules are protected by Packaging Contract Tests.
