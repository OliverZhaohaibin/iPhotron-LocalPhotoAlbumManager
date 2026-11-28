"""Fault injection through real pipelines/workers, journals and SQLite state.

Only ORT's native session is substituted. This is failure/state evidence, not
GPU hardware qualification. All photos and library state are disposable.
"""

from __future__ import annotations

import errno
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from iPhoto.bootstrap.library_session import create_headless_library_session
from iPhoto.cache.index_store import get_global_repository
from iPhoto.library.workers import face_scan_worker, pet_scan_worker
from iPhoto.people.pipeline import FaceClusterPipeline
from iPhoto.people.errors import FaceModelUnavailableError, FaceRuntimeUnavailableError
from iPhoto.pets import pipeline as pets
from iPhoto.recognition import onnx_runtime as ort


class Options:
    def add_session_config_entry(self, *_):
        pass


def resource_failure(fault):
    if fault == "memory":
        return MemoryError()
    if fault == "array_memory":
        return type("ArrayMemoryError", (MemoryError,), {})("array allocation exhausted")
    return OSError(errno.ENOMEM, "native allocation exhausted")


def install_fault(monkeypatch, fault, failure_after=0):
    calls = []
    attempts = []

    class NativeSession:
        def __init__(self, _path, *, providers, **_kwargs):
            attempts.append(providers)
            self.providers = providers
            if providers == [ort.CPU] and fault == "cpu_create":
                raise RuntimeError("CPU native allocation failed")

        def disable_fallback(self):
            pass

        def get_providers(self):
            return self.providers

        def run(self, *_args):
            calls.append(self.providers)
            if fault in {"memory", "array_memory", "enomem"} and len(calls) > failure_after:
                raise resource_failure(fault)
            if fault != "healthy" and len(calls) > failure_after and self.providers != [ort.CPU]:
                raise RuntimeError("CUDA failure: device lost")
            if fault == "cpu_run" and self.providers == [ort.CPU]:
                raise RuntimeError("CPU kernel failed")
            if fault == "model" and self.providers == [ort.CPU]:
                raise type("InvalidArgument", (Exception,), {})("model contract broken")
            return [np.arange(1, 385, dtype=np.float32)[None, :]]

    native = SimpleNamespace(
        SessionOptions=Options,
        InferenceSession=NativeSession,
        get_available_providers=lambda: ["CUDAExecutionProvider", ort.CPU],
        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL=0),
        GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL=1),
    )
    monkeypatch.setattr(ort.importlib, "import_module", lambda _: native)
    monkeypatch.setenv("IPHOTO_RECOGNITION_PROVIDERS", "CUDAExecutionProvider")
    return calls, attempts


def attach_inference(monkeypatch, kind, stage):
    sessions = []

    def session(pipeline):
        if not sessions or sessions[-1][0] is not pipeline:
            sessions.append(
                (pipeline, pipeline._runtime.create_session(pipeline._model_root / "fixture.onnx"))
            )
        return sessions[-1][1]

    if kind == "face":

        def face_analysis(pipeline):
            native = session(pipeline)

            def get(_image):
                native.run(None, {})
                return []

            return SimpleNamespace(get=get)

        monkeypatch.setattr(FaceClusterPipeline, "_ensure_face_analysis", face_analysis)
    else:
        # Use the production detector/embedder run wrappers and pipeline catches.
        def detector(pipeline):
            if stage == "embedding":
                return SimpleNamespace(
                    detect=lambda _: [
                        pets._DetectedPetBox(
                            bbox=(5, 5, 80, 80), confidence=0.99, species_label="cat"
                        )
                    ]
                )
            value = pets._YoloxOnnxPetDetector.__new__(pets._YoloxOnnxPetDetector)
            value._session = session(pipeline)
            value._input_name = "image"
            value._input_size = (416, 416)
            value._score_threshold = 0.30
            value._enable_tiled_detection = False
            return value

        def embedder(pipeline):
            if stage == "detector":
                return SimpleNamespace(embed=lambda _: np.ones(384, dtype=np.float32))
            value = pets._DinoV2Embedder.__new__(pets._DinoV2Embedder)
            value._session = session(pipeline)
            return value

        monkeypatch.setattr(pets.PetClusterPipeline, "_ensure_detector", detector)
        monkeypatch.setattr(pets.PetClusterPipeline, "_ensure_embedder", embedder)
        # The healthy detector's native fixture represents a valid empty image.
        monkeypatch.setattr(
            pets, "_flatten_predictions", lambda _: np.empty((0, 85), dtype=np.float32)
        )
    return sessions


@pytest.mark.parametrize(
    "kind,stage", [("face", "face"), ("pet", "detector"), ("pet", "embedding")]
)
@pytest.mark.parametrize("fault", ["gpu_recovered", "cpu_create", "cpu_run", "model", "memory", "array_memory", "enomem"])
@pytest.mark.parametrize("failure_after", [0, 2])
def test_runtime_failure_preserves_persisted_queue_and_restart_recovers(
    tmp_path, monkeypatch, kind, stage, fault, failure_after
):
    monkeypatch.delenv("IPHOTO_PET_SCAN_DISABLED", raising=False)
    monkeypatch.delenv("IPHOTO_FACE_MODEL_DIR", raising=False)
    monkeypatch.setattr(face_scan_worker, "resolve_face_model_dir", lambda _: tmp_path / "models")
    monkeypatch.setattr(pet_scan_worker.PetScanWorker, "CPU_BACKOFF_SECONDS", 0)
    status = f"{kind}_status"
    other_status = "pet_status" if kind == "face" else "face_status"
    rows = []
    for number in range(7):
        rel = f"photo-{number}.jpg"
        Image.new("RGB", (128, 128), (number, 10, 20)).save(tmp_path / rel)
        rows.append(
            {
                "id": f"asset-{number}",
                "rel": rel,
                "media_type": 0,
                status: "retry" if number % 2 else "pending",
                other_status: "done",
            }
        )
    repository = get_global_repository(tmp_path)
    repository.write_rows(rows)
    calls, attempts = install_fault(monkeypatch, fault, failure_after)
    managed = attach_inference(monkeypatch, kind, stage)
    worker_type = (
        face_scan_worker.FaceScanWorker if kind == "face" else pet_scan_worker.PetScanWorker
    )

    def run_worker():
        session = create_headless_library_session(tmp_path)
        try:
            service = session.people if kind == "face" else session.pets
            if kind == "pet":
                service.repository().set_scan_metadata_many(
                    {
                        "detector_pipeline_version": pets.PET_DETECTOR_PIPELINE_VERSION,
                        "clustering_pipeline_version": pets.PET_CLUSTERING_PIPELINE_VERSION,
                    }
                )
            worker = worker_type(
                tmp_path, **{f"{kind if kind == 'pet' else 'people'}_service": service}
            )
            messages, failures, events = [], [], []
            worker.statusChanged.connect(messages.append)
            worker.modelUnavailable.connect(failures.append)
            signal = worker.peopleIndexUpdated if kind == "face" else worker.petIndexUpdated
            signal.connect(lambda: events.append(True))
            worker.finish_input()
            worker.run()
            assert all(native._session is None for _, native in managed)
            return messages, failures, events
        finally:
            session.shutdown()

    messages, failures, events = run_worker()

    # Read SQLite after shutting down the library session, rather than trusting
    # an in-memory repository/status mock. Reopen the normal session below.
    def stored_statuses():
        with sqlite3.connect(tmp_path / ".iPhoto/global_index.db") as db:
            return db.execute(
                f"SELECT id, {status}, {other_status} FROM assets ORDER BY id"
            ).fetchall()

    if fault == "gpu_recovered":
        assert all(value == "done" and other == "done" for _, value, other in stored_statuses())
        assert len(calls) == 8 and len(attempts) == 2
        assert not failures and events
    else:
        assert stored_statuses() == [(row["id"], row[status], "done") for row in rows]
        assert messages and not events
        assert failures == (["incomplete"] if fault == "model" else [])
        assert len(calls) == failure_after + (2 if fault in {"cpu_run", "model"} else 1)
        if fault in {"memory", "array_memory", "enomem"}:
            assert len(attempts) == 1
        with sqlite3.connect(tmp_path / ".iPhoto/recognition/operations.db") as db:
            assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
        assert not list((tmp_path / ".iPhoto/pets/thumbnails/.staging").glob("*"))

        install_fault(monkeypatch, "healthy")
        _, failures, events = run_worker()
        assert not failures and events
        assert all(value == "done" and other == "done" for _, value, other in stored_statuses())


@pytest.mark.parametrize("kind,stage", [("face", "face"), ("pet", "detector"), ("pet", "embedding")])
@pytest.mark.parametrize("fault", ["memory", "array_memory", "enomem"])
def test_raw_inference_resource_failure_keeps_its_runtime_category(tmp_path, monkeypatch, kind, stage, fault):
    image = Image.new("RGB", (128, 128))
    failure = resource_failure(fault)

    def fail(*_a, **_kw):
        raise failure

    if kind == "face":
        image.save(tmp_path / "photo.jpg")
        pipeline = FaceClusterPipeline(model_root=tmp_path / "models")
        monkeypatch.setattr(pipeline, "_ensure_face_analysis", lambda: SimpleNamespace(get=fail))
        try:
            with pytest.raises(FaceRuntimeUnavailableError) as error:
                pipeline.detect_faces_for_rows(
                    [{"id": "asset", "rel": "photo.jpg"}], library_root=tmp_path, thumbnail_dir=tmp_path / "thumbs",
                )
        finally:
            pipeline.close()
    else:
        native = SimpleNamespace(run=fail)
        if stage == "detector":
            wrapper = pets._YoloxOnnxPetDetector.__new__(pets._YoloxOnnxPetDetector)
            wrapper._session, wrapper._input_name, wrapper._input_size = native, "image", (416, 416)
            operation = wrapper._detect_single_image
        else:
            wrapper = pets._DinoV2Embedder.__new__(pets._DinoV2Embedder)
            wrapper._session = native
            operation = wrapper.embed
        with pytest.raises(pets.PetRuntimeUnavailableError) as error:
            operation(image)
    assert error.value.__cause__ is failure


@pytest.mark.parametrize("stage,hook", [
    ("detector", "_preprocess_yolox"), ("detector", "_flatten_predictions"),
    ("embedding", "image_to_chw_float"), ("embedding", "normalize_vector"),
])
@pytest.mark.parametrize("fault", ["memory", "enomem"])
def test_pet_pre_and_postprocessing_resource_failure_never_becomes_an_asset_error(
    tmp_path, monkeypatch, stage, hook, fault
):
    Image.new("RGB", (128, 128)).save(tmp_path / "photo.jpg")
    install_fault(monkeypatch, "healthy")
    managed = attach_inference(monkeypatch, "pet", stage)
    failure = resource_failure(fault)

    def fail(*_a, **_kw):
        raise failure

    monkeypatch.setattr(pets, hook, fail)
    pipeline = pets.PetClusterPipeline(model_root=tmp_path / "models")
    try:
        with pytest.raises(pets.PetRuntimeUnavailableError) as error:
            pipeline.detect_pets_for_rows(
                [{"id": "asset", "rel": "photo.jpg"}], library_root=tmp_path, thumbnail_dir=tmp_path / "thumbs",
            )
        assert error.value.__cause__ is failure
    finally:
        pipeline.close()
    assert all(native._session is None for _, native in managed)


@pytest.mark.parametrize("has_work", [True, False])
def test_no_verified_official_face_model_keeps_queue_without_creating_pipeline(tmp_path, monkeypatch, has_work):
    from iPhoto.people import model_paths, service

    monkeypatch.delenv("IPHOTO_FACE_MODEL_DIR", raising=False)
    cache = tmp_path / "models"
    for name in model_paths.BUFFALO_S_FILES:
        path = cache / "buffalo_s" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"nonempty but unverified official cache")
    monkeypatch.setattr(service, "shared_face_model_dir", lambda: cache)
    monkeypatch.setattr(model_paths, "installed_recognition_model_roots", lambda: ())
    monkeypatch.setattr(model_paths, "legacy_recognition_model_roots", lambda: ())
    monkeypatch.setattr(model_paths, "bundled_face_model_dir", lambda: tmp_path / "missing-bundle")
    monkeypatch.setattr(face_scan_worker, "FaceClusterPipeline", lambda **_kw: pytest.fail("unverified models entered inference"))
    rows = [
        {"id": f"asset-{i}", "rel": f"photo-{i}.jpg", "media_type": 0, "face_status": state, "pet_status": "done"}
        for i, state in enumerate(("pending", "retry"))
    ] if has_work else []
    repository = get_global_repository(tmp_path)
    repository.write_rows(rows)
    session = create_headless_library_session(tmp_path)
    failures, events = [], []
    try:
        worker = face_scan_worker.FaceScanWorker(tmp_path, people_service=session.people)
        worker.modelUnavailable.connect(failures.append)
        worker.peopleIndexUpdated.connect(lambda: events.append(True))
        worker.finish_input()
        worker.run()
    finally:
        session.shutdown()
    assert failures == (["missing"] if has_work else []) and not events
    with sqlite3.connect(tmp_path / ".iPhoto/global_index.db") as db:
        assert db.execute("SELECT face_status, pet_status FROM assets ORDER BY id").fetchall() == ([
            ("pending", "done"), ("retry", "done")
        ] if has_work else [])


@pytest.mark.parametrize("usable", [True, False])
def test_explicit_face_override_bypasses_official_lookup_and_retains_invalid_override_recovery(
    tmp_path, monkeypatch, usable
):
    custom = tmp_path / "custom-models"
    monkeypatch.setenv("IPHOTO_FACE_MODEL_DIR", str(custom))
    monkeypatch.setattr(face_scan_worker, "resolve_face_model_dir", lambda _: pytest.fail("custom models entered official lookup"))

    def analysis(pipeline):
        assert pipeline._model_root == custom
        if not usable:
            raise FaceModelUnavailableError("missing custom model", "missing")
        return SimpleNamespace(get=lambda _: [])

    monkeypatch.setattr(FaceClusterPipeline, "_ensure_face_analysis", analysis)
    Image.new("RGB", (128, 128)).save(tmp_path / "photo.jpg")
    get_global_repository(tmp_path).write_rows([
        {"id": "asset", "rel": "photo.jpg", "media_type": 0, "face_status": "pending", "pet_status": "done"}
    ])
    session = create_headless_library_session(tmp_path)
    failures = []
    try:
        worker = face_scan_worker.FaceScanWorker(tmp_path, people_service=session.people)
        worker.modelUnavailable.connect(failures.append)
        worker.finish_input()
        worker.run()
    finally:
        session.shutdown()
    assert failures == ([] if usable else ["invalid_override"])
    with sqlite3.connect(tmp_path / ".iPhoto/global_index.db") as db:
        assert db.execute("SELECT face_status, pet_status FROM assets").fetchone() == (
            "done" if usable else "pending", "done"
        )
