from __future__ import annotations

import builtins
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from iPhoto.people.manual_faces import (
    ManualFaceValidationError,
    build_manual_face_record,
)
from iPhoto.people.pipeline import (
    FaceClusterPipeline,
    _install_insightface_mask_renderer_stubs,
    _install_runtime_typing_compat,
    build_person_records_from_faces,
    resolve_canonical_person_id,
)
from iPhoto.people.repository import FaceRecord, PersonProfile, PersonRecord


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _face_record(
    *,
    face_id: str,
    person_id: str | None,
    embedding: np.ndarray,
    face_key: str,
) -> FaceRecord:
    return FaceRecord(
        face_id=face_id,
        face_key=face_key,
        asset_id=f"asset-{face_id}",
        asset_rel=f"album/{face_id}.jpg",
        box_x=10,
        box_y=12,
        box_w=80,
        box_h=80,
        confidence=0.99,
        embedding=embedding,
        embedding_dim=int(embedding.shape[0]),
        thumbnail_path=None,
        person_id=person_id,
        detected_at=_now_iso(),
        image_width=400,
        image_height=300,
    )


def _person_record(
    *,
    person_id: str,
    key_face_id: str,
    embedding: np.ndarray,
    face_count: int,
) -> PersonRecord:
    timestamp = _now_iso()
    return PersonRecord(
        person_id=person_id,
        name=None,
        key_face_id=key_face_id,
        face_count=face_count,
        center_embedding=embedding,
        created_at=timestamp,
        updated_at=timestamp,
        sample_count=face_count,
        profile_state="stable" if face_count >= 3 else "unstable",
    )


def _profile(
    *,
    person_id: str,
    embedding: np.ndarray,
    sample_count: int,
) -> PersonProfile:
    timestamp = _now_iso()
    return PersonProfile(
        person_id=person_id,
        name="Alice",
        center_embedding=embedding,
        embedding_dim=int(embedding.shape[0]),
        created_at=timestamp,
        updated_at=timestamp,
        sample_count=sample_count,
        profile_state="stable" if sample_count >= 3 else "unstable",
    )


@pytest.fixture
def local_insightface(monkeypatch):
    calls = []

    class FakeFaceAnalysis:
        def __init__(self, **_kwargs):
            pytest.fail("InsightFace's downloading constructor was called")

        def prepare(self, *, ctx_id, det_size):
            assert (ctx_id, det_size) == (0, (640, 640))

    def create_session(_runtime, path):
        model_path = Path(path)
        assert model_path.is_file()
        calls.append(model_path)
        image_size = {
            "1k3d68.onnx": 192,
            "2d106det.onnx": 192,
            "genderage.onnx": 96,
        }.get(model_path.name, 112)
        return SimpleNamespace(
            get_inputs=lambda: [SimpleNamespace(shape=[1, 3, image_size, image_size])],
            get_outputs=lambda: [None] * (9 if model_path.name == "det_500m.onnx" else 1),
        )

    monkeypatch.setattr("iPhoto.people.pipeline.RecognitionOnnxRuntime.create_session", create_session)

    insightface = ModuleType("insightface")
    app = ModuleType("insightface.app")
    face_analysis = ModuleType("insightface.app.face_analysis")
    face_analysis.FaceAnalysis = FakeFaceAnalysis
    model_zoo = ModuleType("insightface.model_zoo")
    arcface = ModuleType("insightface.model_zoo.arcface_onnx")
    retinaface = ModuleType("insightface.model_zoo.retinaface")
    arcface.ArcFaceONNX = lambda **kw: SimpleNamespace(taskname="recognition", **kw)
    retinaface.RetinaFace = lambda **kw: SimpleNamespace(taskname="detection", **kw)
    onnx_state = ModuleType("onnxruntime.capi.onnxruntime_pybind11_state")
    for name in ("InvalidGraph", "InvalidProtobuf", "NoSuchFile"):
        setattr(onnx_state, name, type(name, (Exception,), {}))
    for module in (insightface, app, face_analysis, model_zoo, arcface, retinaface, onnx_state):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr("iPhoto.people.pipeline._patch_insightface_alignment_estimate", lambda: None)
    return calls


@pytest.mark.parametrize("folder", ["models", "custom-models"])
def test_face_pipeline_loads_only_local_models(local_insightface, tmp_path, folder):
    model_root = tmp_path / folder
    pack = model_root / "buffalo_s"
    pack.mkdir(parents=True)
    paths = [pack / name for name in ("det_500m.onnx", "w600k_mbf.onnx")]
    for path in paths:
        path.write_bytes(b"model")
    pipeline = FaceClusterPipeline(model_root=model_root)

    app = pipeline._ensure_face_analysis()

    assert app is pipeline._ensure_face_analysis()
    assert app.det_model is app.models["detection"]
    assert local_insightface == [path.resolve() for path in paths]


@pytest.mark.parametrize("with_recognition", [True, False])
def test_custom_face_pack_excludes_landmark_and_attribute_models(
    local_insightface, tmp_path, with_recognition
):
    from iPhoto.people.errors import FaceModelUnavailableError

    pack = tmp_path / "custom_pack"
    pack.mkdir()
    names = ["1k3d68.onnx", "2d106det.onnx", "det_500m.onnx", "genderage.onnx"]
    if with_recognition:
        names.append("w600k_mbf.onnx")
    for name in names:
        (pack / name).write_bytes(b"model")
    pipeline = FaceClusterPipeline(model_root=tmp_path, model_pack=pack.name)
    try:
        if with_recognition:
            app = pipeline._ensure_face_analysis()
            assert set(app.models) == {"detection", "recognition"}
            assert Path(app.models["recognition"].model_file) == pack / "w600k_mbf.onnx"
        else:
            with pytest.raises(FaceModelUnavailableError) as exc:
                pipeline._ensure_face_analysis()
            assert exc.value.reason == "incomplete"
    finally:
        pipeline.close()


@pytest.mark.parametrize("reason", ["missing", "incomplete"])
def test_face_pipeline_reports_missing_pack_without_using_insightface_downloader(
    local_insightface, tmp_path, reason
):
    from iPhoto.people.errors import FaceModelUnavailableError

    model_root = tmp_path / "models"
    if reason == "incomplete":
        pack = model_root / "buffalo_s"
        pack.mkdir(parents=True)
        (pack / "det_500m.onnx").write_bytes(b"model")
    pipeline = FaceClusterPipeline(model_root=model_root)

    with pytest.raises(FaceModelUnavailableError, match="Install the People & Pets extension") as exc:
        pipeline._ensure_face_analysis()

    assert exc.value.reason == reason
    assert not local_insightface


@pytest.mark.parametrize("error_name", ["InvalidProtobuf", "RuntimeError"])
def test_face_pipeline_distinguishes_damaged_models_from_runtime_errors(
    local_insightface, monkeypatch, tmp_path, error_name
):
    from iPhoto.people.errors import FaceModelUnavailableError, FaceRuntimeUnavailableError

    pack = tmp_path / "buffalo_s"
    pack.mkdir()
    for name in ("det_500m.onnx", "w600k_mbf.onnx"):
        (pack / name).write_bytes(b"nonempty")
    from iPhoto.recognition.onnx_runtime import RecognitionModelError
    error_type = RecognitionModelError if error_name == "InvalidProtobuf" else RuntimeError

    def broken_model(*args, **kwargs):
        raise error_type("could not load")

    monkeypatch.setattr("iPhoto.people.pipeline.RecognitionOnnxRuntime.create_session", broken_model)
    expected = FaceRuntimeUnavailableError if error_type is RuntimeError else FaceModelUnavailableError
    with pytest.raises(expected) as failed:
        FaceClusterPipeline(model_root=tmp_path)._ensure_face_analysis()
    if expected is FaceModelUnavailableError:
        assert failed.value.reason == "incomplete"


def test_face_pipeline_installs_lightweight_albumentations_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for module_name in [
        "albumentations",
        "albumentations.core",
        "albumentations.core.transforms_interface",
    ]:
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    _install_insightface_mask_renderer_stubs()

    transform_module = sys.modules["albumentations.core.transforms_interface"]
    transform = transform_module.ImageOnlyTransform(always_apply=False, p=1.0)

    assert transform.__class__.__name__ == "ImageOnlyTransform"
    assert sys.modules["albumentations"].core is sys.modules["albumentations.core"]


def test_face_pipeline_installs_runtime_annotation_compat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ["Literal", "NDArray", "ArrayLike", "DTypeLike", "List", "TypedDict", "ndarray"]:
        monkeypatch.delattr(builtins, name, raising=False)

    _install_runtime_typing_compat()

    assert builtins.Literal is not None
    assert builtins.NDArray is not None
    assert builtins.ArrayLike is not None
    assert builtins.DTypeLike is not None
    assert builtins.List is not None
    assert builtins.TypedDict is not None
    assert builtins.ndarray is not None


def test_build_person_records_marks_profiles_stable_at_three_samples() -> None:
    embedding = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    faces = [
        _face_record(face_id="face-a-1", person_id="person-a", embedding=embedding, face_key="key-a-1"),
        _face_record(face_id="face-a-2", person_id="person-a", embedding=embedding, face_key="key-a-2"),
        _face_record(face_id="face-b-1", person_id="person-b", embedding=embedding, face_key="key-b-1"),
        _face_record(face_id="face-b-2", person_id="person-b", embedding=embedding, face_key="key-b-2"),
        _face_record(face_id="face-b-3", person_id="person-b", embedding=embedding, face_key="key-b-3"),
    ]

    persons = build_person_records_from_faces(
        faces,
        names_by_person_id={"person-a": "Alice", "person-b": "Bob"},
    )
    persons_by_id = {person.person_id: person for person in persons}

    assert persons_by_id["person-a"].sample_count == 2
    assert persons_by_id["person-a"].profile_state == "unstable"
    assert persons_by_id["person-b"].sample_count == 3
    assert persons_by_id["person-b"].profile_state == "stable"


def test_resolve_canonical_person_id_ignores_unstable_profiles_for_embedding_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    person = _person_record(
        person_id="cluster-a",
        key_face_id="face-new",
        embedding=embedding,
        face_count=1,
    )
    members = [
        _face_record(
            face_id="face-new",
            person_id="cluster-a",
            embedding=embedding,
            face_key="face-key-new",
        )
    ]
    monkeypatch.setattr("iPhoto.people.pipeline.uuid.uuid4", lambda: SimpleNamespace(hex="new-person"))

    resolved = resolve_canonical_person_id(
        person,
        members,
        profiles={"person-a": _profile(person_id="person-a", embedding=embedding, sample_count=2)},
        face_key_map={},
        distance_threshold=0.2,
    )

    assert resolved == "new-person"


def test_resolve_canonical_person_id_uses_stable_profiles_for_embedding_matches() -> None:
    embedding = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    person = _person_record(
        person_id="cluster-a",
        key_face_id="face-new",
        embedding=embedding,
        face_count=1,
    )
    members = [
        _face_record(
            face_id="face-new",
            person_id="cluster-a",
            embedding=embedding,
            face_key="face-key-new",
        )
    ]

    resolved = resolve_canonical_person_id(
        person,
        members,
        profiles={"person-a": _profile(person_id="person-a", embedding=embedding, sample_count=3)},
        face_key_map={},
        distance_threshold=0.2,
    )

    assert resolved == "person-a"


def test_face_pipeline_reports_unreadable_image_without_error_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    library_root = tmp_path / "library"
    raw_path = library_root / "album" / "photo.NEF"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"not a pillow-readable raw file")
    pipeline = FaceClusterPipeline(model_root=tmp_path / "models")
    fake_face_app = SimpleNamespace(get=pytest.fail)
    monkeypatch.setattr(pipeline, "_ensure_face_analysis", lambda: fake_face_app)

    with caplog.at_level("WARNING", logger="iPhoto.people.pipeline"):
        results = pipeline.detect_faces_for_rows(
            [{"id": "asset-a", "rel": "album/photo.NEF"}],
            library_root=library_root,
            thumbnail_dir=tmp_path / "thumbs",
        )

    assert len(results) == 1
    assert results[0].asset_id == "asset-a"
    assert results[0].faces == []
    assert results[0].error
    assert "Skipping face detection for unreadable image" in caplog.text
    assert "Face detection failed" not in caplog.text


@pytest.mark.parametrize("fallback", ["v2-backup", "v1", "v1-backup", "cache", "bundled"])
@pytest.mark.parametrize("damage", ["corrupt", "incomplete"])
def test_invalid_active_people_models_use_verified_fallback(tmp_path, monkeypatch, fallback, damage):
    import hashlib
    import json
    from iPhoto.people import model_paths

    active = tmp_path / "v2/models"
    roots = {
        "v2-backup": tmp_path / "v2/.backup/models",
        "v1": tmp_path / "v1/models",
        "v1-backup": tmp_path / "v1/.backup/models",
        "cache": tmp_path / "cache",
        "bundled": tmp_path / "bundled",
    }
    identities = {}
    for name in model_paths.BUFFALO_S_FILES:
        content = f"verified {name}".encode()
        identities[f"buffalo_s/{name}"] = {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        good = roots[fallback] / "buffalo_s" / name
        good.parent.mkdir(parents=True, exist_ok=True)
        good.write_bytes(content)
        bad = active / "buffalo_s" / name
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"x" * len(content))
    if damage == "incomplete":
        (active / "buffalo_s" / model_paths.BUFFALO_S_FILES[0]).unlink()
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"files": identities}))
    monkeypatch.setattr(model_paths, "_FACE_CONTRACT_PATH", contract)
    monkeypatch.setattr(model_paths, "installed_recognition_model_roots", lambda: (active, roots["v2-backup"]))
    monkeypatch.setattr(model_paths, "legacy_recognition_model_roots", lambda: (roots["v1"], roots["v1-backup"]))
    monkeypatch.setattr(model_paths, "bundled_face_model_dir", lambda: roots["bundled"])
    assert model_paths.resolve_face_model_dir(tmp_path / "cache") == roots[fallback]


@pytest.mark.parametrize("damage", ["missing", "empty", "same-size-corrupt", "wrong-size", "incomplete"])
def test_no_verified_face_models_never_returns_unverified_cache(tmp_path, monkeypatch, damage):
    import hashlib
    import json
    from iPhoto.people import model_paths

    cache = tmp_path / "cache"
    files = {}
    for name in model_paths.BUFFALO_S_FILES:
        content = f"official {name}".encode()
        files[f"buffalo_s/{name}"] = {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        if damage == "missing":
            continue
        path = cache / "buffalo_s" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes({
            "empty": b"", "same-size-corrupt": b"x" * len(content),
            "wrong-size": b"x", "incomplete": content,
        }[damage])
    if damage == "incomplete":
        (cache / "buffalo_s" / model_paths.BUFFALO_S_FILES[0]).unlink()
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"files": files}))
    monkeypatch.setattr(model_paths, "_FACE_CONTRACT_PATH", contract)
    monkeypatch.setattr(model_paths, "installed_recognition_model_roots", lambda: ())
    monkeypatch.setattr(model_paths, "legacy_recognition_model_roots", lambda: ())
    monkeypatch.setattr(model_paths, "bundled_face_model_dir", lambda: tmp_path / "missing-bundle")
    before = {path: path.read_bytes() for path in cache.rglob("*") if path.is_file()}
    assert model_paths.resolve_face_model_dir(cache) is None
    assert before == {path: path.read_bytes() for path in cache.rglob("*") if path.is_file()}


def test_build_manual_face_record_saves_requested_box_without_face_detection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "photo.jpg"
    thumbnail_dir = tmp_path / "thumbs"
    thumbnail_dir.mkdir()
    fake_image = SimpleNamespace(size=(400, 300))
    saved: dict[str, object] = {}

    def _fail_face_analysis(_self):
        raise AssertionError("manual faces must not load the AI face analyzer")

    def _save_thumbnail(image, bbox, output_path):
        saved["image"] = image
        saved["bbox"] = bbox
        saved["output_path"] = output_path

    monkeypatch.setattr(FaceClusterPipeline, "_ensure_face_analysis", _fail_face_analysis)
    monkeypatch.setattr("iPhoto.people.manual_faces.load_image_rgb", lambda _path: fake_image)
    monkeypatch.setattr("iPhoto.people.manual_faces.save_face_thumbnail", _save_thumbnail)
    monkeypatch.setattr("iPhoto.people.manual_faces.uuid.uuid4", lambda: SimpleNamespace(hex="manual-1"))

    face = build_manual_face_record(
        asset_id="asset-1",
        asset_rel="album/photo.jpg",
        image_path=image_path,
        requested_box=(90, 50, 180, 180),
        thumbnail_dir=thumbnail_dir,
        target_person_id="person-1",
    )

    assert (face.box_x, face.box_y, face.box_w, face.box_h) == (90, 50, 180, 180)
    assert face.person_id == "person-1"
    assert face.thumbnail_path == "thumbs/manual-1.png"
    assert saved["bbox"] == (90, 50, 180, 180)
    assert saved["output_path"] == thumbnail_dir / "manual-1.png"


def test_build_manual_face_record_accepts_non_face_region(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "iPhoto.people.manual_faces.load_image_rgb",
        lambda _path: SimpleNamespace(size=(400, 300)),
    )
    monkeypatch.setattr("iPhoto.people.manual_faces.save_face_thumbnail", lambda *_args, **_kwargs: None)

    face = build_manual_face_record(
        asset_id="asset-1",
        asset_rel="album/photo.jpg",
        image_path=tmp_path / "photo.jpg",
        requested_box=(10, 20, 80, 90),
        thumbnail_dir=tmp_path / "thumbs",
        target_person_id="person-1",
    )

    assert (face.box_x, face.box_y, face.box_w, face.box_h) == (10, 20, 80, 90)


def test_build_manual_face_record_rejects_out_of_bounds_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "iPhoto.people.manual_faces.load_image_rgb",
        lambda _path: SimpleNamespace(size=(400, 300)),
    )

    with pytest.raises(
        ManualFaceValidationError,
        match="Please place the face circle fully inside the photo.",
    ):
        build_manual_face_record(
            asset_id="asset-1",
            asset_rel="album/photo.jpg",
            image_path=tmp_path / "photo.jpg",
            requested_box=(350, 250, 80, 80),
            thumbnail_dir=tmp_path / "thumbs",
            target_person_id="person-1",
        )


def test_build_manual_face_record_rejects_too_small_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "iPhoto.people.manual_faces.load_image_rgb",
        lambda _path: SimpleNamespace(size=(400, 300)),
    )

    with pytest.raises(
        ManualFaceValidationError,
        match="The selected face is too small to save reliably.",
    ):
        build_manual_face_record(
            asset_id="asset-1",
            asset_rel="album/photo.jpg",
            image_path=tmp_path / "photo.jpg",
            requested_box=(10, 20, 20, 90),
            thumbnail_dir=tmp_path / "thumbs",
            target_person_id="person-1",
        )
