from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from iPhoto.people.pipeline import (
    FaceClusterPipeline,
    ManualFaceValidationError,
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


def test_face_pipeline_uses_shared_model_root(monkeypatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    class FakeFaceAnalysis:
        def __init__(self, *, name: str, root: str, providers: list[str]) -> None:
            calls["name"] = name
            calls["root"] = root
            calls["providers"] = providers

        def prepare(self, *, ctx_id: int, det_size: tuple[int, int]) -> None:
            calls["ctx_id"] = ctx_id
            calls["det_size"] = det_size

    insightface_module = ModuleType("insightface")
    app_module = ModuleType("insightface.app")
    app_module.FaceAnalysis = FakeFaceAnalysis
    insightface_module.app = app_module

    monkeypatch.setitem(sys.modules, "insightface", insightface_module)
    monkeypatch.setitem(sys.modules, "insightface.app", app_module)
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
        "providers": ["CPUExecutionProvider"],
        "ctx_id": -1,
        "det_size": (640, 640),
    }
    assert os.environ["INSIGHTFACE_HOME"] == str((tmp_path / "extension").resolve())


def test_face_pipeline_reports_missing_cached_model_with_actionable_message(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeFaceAnalysis:
        def __init__(self, *, name: str, root: str, providers: list[str]) -> None:
            raise RuntimeError("network unreachable")

    insightface_module = ModuleType("insightface")
    app_module = ModuleType("insightface.app")
    app_module.FaceAnalysis = FakeFaceAnalysis
    insightface_module.app = app_module

    monkeypatch.setitem(sys.modules, "insightface", insightface_module)
    monkeypatch.setitem(sys.modules, "insightface.app", app_module)
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


def test_build_manual_face_record_accepts_larger_enclosing_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pipeline = FaceClusterPipeline(model_root=tmp_path / "models")
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake")
    thumbnail_dir = tmp_path / "thumbs"
    thumbnail_dir.mkdir()

    fake_image = SimpleNamespace(size=(400, 300))
    fake_detection = SimpleNamespace(
        bbox=np.asarray([120.0, 80.0, 200.0, 180.0], dtype=np.float32),
        embedding=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        det_score=0.97,
    )
    fake_app = SimpleNamespace(get=lambda _image: [fake_detection])

    monkeypatch.setattr(pipeline, "_ensure_face_analysis", lambda: fake_app)
    monkeypatch.setattr("iPhoto.people.pipeline.load_image_rgb", lambda _path: fake_image)
    monkeypatch.setattr("iPhoto.people.pipeline.pil_image_to_bgr", lambda _image: object())
    monkeypatch.setattr("iPhoto.people.pipeline.save_face_thumbnail", lambda *_args, **_kwargs: None)

    face = pipeline.build_manual_face_record(
        asset_id="asset-1",
        asset_rel="album/photo.jpg",
        image_path=image_path,
        requested_box=(90, 50, 180, 180),
        thumbnail_dir=thumbnail_dir,
        target_person_id="person-1",
    )

    assert (face.box_x, face.box_y, face.box_w, face.box_h) == (120, 80, 80, 100)
    assert face.person_id == "person-1"
    assert face.is_manual is True


def test_build_manual_face_record_rejects_selection_that_barely_touches_face(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pipeline = FaceClusterPipeline(model_root=tmp_path / "models")
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake")
    thumbnail_dir = tmp_path / "thumbs"
    thumbnail_dir.mkdir()

    fake_image = SimpleNamespace(size=(400, 300))
    fake_detection = SimpleNamespace(
        bbox=np.asarray([120.0, 80.0, 200.0, 180.0], dtype=np.float32),
        embedding=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        det_score=0.97,
    )
    fake_app = SimpleNamespace(get=lambda _image: [fake_detection])

    monkeypatch.setattr(pipeline, "_ensure_face_analysis", lambda: fake_app)
    monkeypatch.setattr("iPhoto.people.pipeline.load_image_rgb", lambda _path: fake_image)
    monkeypatch.setattr("iPhoto.people.pipeline.pil_image_to_bgr", lambda _image: object())
    monkeypatch.setattr("iPhoto.people.pipeline.save_face_thumbnail", lambda *_args, **_kwargs: None)

    with pytest.raises(
        ManualFaceValidationError,
        match="Please place the circle closer to the face before saving.",
    ):
        pipeline.build_manual_face_record(
            asset_id="asset-1",
            asset_rel="album/photo.jpg",
            image_path=image_path,
            requested_box=(70, 50, 60, 60),
            thumbnail_dir=thumbnail_dir,
            target_person_id="person-1",
        )


def test_build_manual_face_record_uses_crop_detection_for_side_face(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pipeline = FaceClusterPipeline(model_root=tmp_path / "models")
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake")
    thumbnail_dir = tmp_path / "thumbs"
    thumbnail_dir.mkdir()

    class _FakeImage:
        def __init__(self, size: tuple[int, int], *, label: str) -> None:
            self.size = size
            self.label = label

        def crop(self, box: tuple[int, int, int, int]):
            return _FakeImage((box[2] - box[0], box[3] - box[1]), label="crop")

    fake_image = _FakeImage((400, 300), label="global")
    global_detection = SimpleNamespace(
        bbox=np.asarray([110.0, 80.0, 190.0, 180.0], dtype=np.float32),
        embedding=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        det_score=0.92,
    )
    crop_detection = SimpleNamespace(
        bbox=np.asarray([50.0, 25.0, 110.0, 125.0], dtype=np.float32),
        embedding=np.asarray([0.0, 1.0, 0.0], dtype=np.float32),
        det_score=0.96,
    )

    def _fake_get(image_obj):
        return [crop_detection] if getattr(image_obj, "label", "") == "crop" else [global_detection]

    fake_app = SimpleNamespace(get=_fake_get)

    monkeypatch.setattr(pipeline, "_ensure_face_analysis", lambda: fake_app)
    monkeypatch.setattr("iPhoto.people.pipeline.load_image_rgb", lambda _path: fake_image)
    monkeypatch.setattr("iPhoto.people.pipeline.pil_image_to_bgr", lambda image_obj: image_obj)
    monkeypatch.setattr("iPhoto.people.pipeline.save_face_thumbnail", lambda *_args, **_kwargs: None)

    face = pipeline.build_manual_face_record(
        asset_id="asset-1",
        asset_rel="album/photo.jpg",
        image_path=image_path,
        requested_box=(180, 80, 120, 140),
        thumbnail_dir=thumbnail_dir,
        target_person_id="person-2",
        existing_faces=[
            FaceRecord(
                face_id="existing-left",
                face_key="key-left",
                asset_id="asset-1",
                asset_rel="album/photo.jpg",
                box_x=110,
                box_y=80,
                box_w=80,
                box_h=100,
                confidence=0.9,
                embedding=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
                embedding_dim=3,
                thumbnail_path=None,
                person_id="person-left",
                detected_at=_now_iso(),
                image_width=400,
                image_height=300,
            )
        ],
    )

    assert (face.box_x, face.box_y, face.box_w, face.box_h) == (230, 105, 60, 100)
    assert face.person_id == "person-2"


def test_build_manual_face_record_duplicate_check_allows_nearby_non_matching_face(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pipeline = FaceClusterPipeline(model_root=tmp_path / "models")
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake")
    thumbnail_dir = tmp_path / "thumbs"
    thumbnail_dir.mkdir()

    fake_image = SimpleNamespace(size=(400, 300), crop=lambda box: SimpleNamespace(size=(box[2] - box[0], box[3] - box[1])))
    detection = SimpleNamespace(
        bbox=np.asarray([220.0, 90.0, 300.0, 190.0], dtype=np.float32),
        embedding=np.asarray([0.0, 1.0, 0.0], dtype=np.float32),
        det_score=0.95,
    )
    fake_app = SimpleNamespace(get=lambda _image: [detection])

    monkeypatch.setattr(pipeline, "_ensure_face_analysis", lambda: fake_app)
    monkeypatch.setattr("iPhoto.people.pipeline.load_image_rgb", lambda _path: fake_image)
    monkeypatch.setattr("iPhoto.people.pipeline.pil_image_to_bgr", lambda image_obj: image_obj)
    monkeypatch.setattr("iPhoto.people.pipeline.save_face_thumbnail", lambda *_args, **_kwargs: None)

    face = pipeline.build_manual_face_record(
        asset_id="asset-1",
        asset_rel="album/photo.jpg",
        image_path=image_path,
        requested_box=(190, 70, 130, 150),
        thumbnail_dir=thumbnail_dir,
        target_person_id="person-2",
        existing_faces=[
            FaceRecord(
                face_id="existing-left",
                face_key="key-left",
                asset_id="asset-1",
                asset_rel="album/photo.jpg",
                box_x=110,
                box_y=80,
                box_w=100,
                box_h=120,
                confidence=0.9,
                embedding=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
                embedding_dim=3,
                thumbnail_path=None,
                person_id="person-left",
                detected_at=_now_iso(),
                image_width=400,
                image_height=300,
            )
        ],
    )

    assert (face.box_x, face.box_y, face.box_w, face.box_h) == (220, 90, 80, 100)
