from __future__ import annotations

import os
import sys
import builtins
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
    build_person_records_from_faces,
    _install_insightface_mask_renderer_stubs,
    _install_runtime_typing_compat,
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


def test_face_pipeline_uses_shared_model_root(monkeypatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    class FakeFaceAnalysis:
        def __init__(
            self,
            *,
            name: str,
            root: str,
            allowed_modules: list[str],
            providers: list[str],
        ) -> None:
            calls["name"] = name
            calls["root"] = root
            calls["allowed_modules"] = allowed_modules
            calls["providers"] = providers

        def prepare(self, *, ctx_id: int, det_size: tuple[int, int]) -> None:
            calls["ctx_id"] = ctx_id
            calls["det_size"] = det_size

    insightface_module = ModuleType("insightface")
    app_module = ModuleType("insightface.app")
    face_analysis_module = ModuleType("insightface.app.face_analysis")
    face_analysis_module.FaceAnalysis = FakeFaceAnalysis
    app_module.face_analysis = face_analysis_module
    insightface_module.app = app_module

    monkeypatch.setitem(sys.modules, "insightface", insightface_module)
    monkeypatch.setitem(sys.modules, "insightface.app", app_module)
    monkeypatch.setitem(sys.modules, "insightface.app.face_analysis", face_analysis_module)
    monkeypatch.setattr("iPhoto.people.pipeline._patch_insightface_alignment_estimate", lambda: None)
    monkeypatch.setattr(
        "iPhoto.people.pipeline._resolve_execution_providers",
        lambda: ["CPUExecutionProvider"],
    )

    monkeypatch.setenv("INSIGHTFACE_HOME", str(tmp_path / "legacy-cache"))

    model_root = tmp_path / "extension" / "models"
    pipeline = FaceClusterPipeline(model_root=model_root)

    app = pipeline._ensure_face_analysis()

    assert app is pipeline._ensure_face_analysis()
    assert model_root.is_dir()
    assert calls == {
        "name": "buffalo_s",
        "root": str((tmp_path / "extension").resolve()),
        "allowed_modules": ["detection", "recognition"],
        "providers": ["CPUExecutionProvider"],
        "ctx_id": -1,
        "det_size": (640, 640),
    }
    assert os.environ["INSIGHTFACE_HOME"] == str((tmp_path / "extension").resolve())


def test_face_pipeline_reports_missing_cached_model_with_actionable_message(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeFaceAnalysis:
        def __init__(
            self,
            *,
            name: str,
            root: str,
            allowed_modules: list[str],
            providers: list[str],
        ) -> None:
            del allowed_modules
            raise RuntimeError("network unreachable")

    insightface_module = ModuleType("insightface")
    app_module = ModuleType("insightface.app")
    face_analysis_module = ModuleType("insightface.app.face_analysis")
    face_analysis_module.FaceAnalysis = FakeFaceAnalysis
    app_module.face_analysis = face_analysis_module
    insightface_module.app = app_module

    monkeypatch.setitem(sys.modules, "insightface", insightface_module)
    monkeypatch.setitem(sys.modules, "insightface.app", app_module)
    monkeypatch.setitem(sys.modules, "insightface.app.face_analysis", face_analysis_module)
    monkeypatch.setattr("iPhoto.people.pipeline._patch_insightface_alignment_estimate", lambda: None)
    monkeypatch.setattr(
        "iPhoto.people.pipeline._resolve_execution_providers",
        lambda: ["CPUExecutionProvider"],
    )

    model_root = tmp_path / "extension" / "models"
    pipeline = FaceClusterPipeline(model_root=model_root)

    monkeypatch.delenv("INSIGHTFACE_HOME", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        pipeline._ensure_face_analysis()

    message = str(excinfo.value)
    assert "not cached" in message
    assert str(model_root.resolve()) in message
    assert "github.com" in message


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
