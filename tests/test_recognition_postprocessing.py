from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from iPhoto.people.face_repository import FaceRepository
from iPhoto.people.pipeline import FaceClusterPipeline, cluster_face_records
from iPhoto.people.records import FaceRecord, PersonRecord
from iPhoto.people.service import PeopleService
from iPhoto.people.state_repository import FaceStateRepository
from iPhoto.pets.pipeline import PetClusterPipeline, cluster_pet_records
from iPhoto.pets.records import (
    PetDetectionRecord,
    PetMutationFailure,
    PetRecord,
)
from iPhoto.pets.repository import PetRepository
from iPhoto.pets.service import PetService
from iPhoto.pets.state_repository import PetStateRepository


NOW = "2026-08-11T00:00:00+00:00"


def _face(
    face_id: str,
    *,
    asset_id: str,
    person_id: str | None,
    embedding: tuple[float, ...] = (1.0, 0.0, 0.0),
) -> FaceRecord:
    vector = np.asarray(embedding, dtype=np.float32)
    return FaceRecord(
        face_id=face_id,
        face_key=f"key-{face_id}",
        asset_id=asset_id,
        asset_rel=f"album/{asset_id}.jpg",
        box_x=10,
        box_y=10,
        box_w=80,
        box_h=80,
        confidence=0.95,
        embedding=vector,
        embedding_dim=vector.size,
        thumbnail_path=None,
        person_id=person_id,
        detected_at=NOW,
        image_width=1000,
        image_height=1000,
    )


def _person(person_id: str, faces: list[FaceRecord]) -> PersonRecord:
    return PersonRecord(
        person_id=person_id,
        name=None,
        key_face_id=faces[0].face_id,
        face_count=len(faces),
        center_embedding=faces[0].embedding,
        created_at=NOW,
        updated_at=NOW,
        sample_count=len(faces),
    )


def _pet_detection(
    detection_id: str,
    *,
    asset_id: str,
    pet_id: str | None,
    embedding: tuple[float, ...] = (1.0, 0.0, 0.0),
) -> PetDetectionRecord:
    vector = np.asarray(embedding, dtype=np.float32)
    return PetDetectionRecord(
        detection_id=detection_id,
        pet_key=f"pet-key-{detection_id}",
        asset_id=asset_id,
        asset_rel=f"album/{asset_id}.jpg",
        box_x=10,
        box_y=10,
        box_w=80,
        box_h=80,
        confidence=0.9,
        embedding=vector,
        embedding_dim=vector.size,
        embedding_model="test",
        detector_model="test",
        thumbnail_path=None,
        pet_id=pet_id,
        detected_at=NOW,
        image_width=1000,
        image_height=1000,
        species_label="cat",
    )


def _pet(pet_id: str, detections: list[PetDetectionRecord]) -> PetRecord:
    return PetRecord(
        pet_id=pet_id,
        name=None,
        key_detection_id=detections[0].detection_id,
        detection_count=len(detections),
        center_embedding=detections[0].embedding,
        embedding_dim=detections[0].embedding_dim,
        created_at=NOW,
        updated_at=NOW,
        sample_count=len(detections),
        species_label="cat",
    )


def test_people_noise_is_persistable_without_creating_person() -> None:
    faces = [
        _face("a", asset_id="asset-a", person_id=None, embedding=(1.0, 0.0)),
        _face("b", asset_id="asset-b", person_id=None, embedding=(0.0, 1.0)),
    ]

    clustered, persons = cluster_face_records(
        faces,
        distance_threshold=0.1,
        min_samples=2,
    )

    assert persons == []
    assert [face.person_id for face in clustered] == [None, None]


def test_people_quality_gate_keeps_high_confidence_small_face(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    image_path = library_root / "album" / "photo.jpg"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (2000, 2000), color=(120, 100, 80)).save(image_path)
    candidates = [
        SimpleNamespace(bbox=(100, 100, 600, 600), det_score=0.90, embedding=[1, 0, 0]),
        SimpleNamespace(bbox=(700, 100, 800, 200), det_score=0.70, embedding=[0, 1, 0]),
        SimpleNamespace(bbox=(850, 100, 950, 200), det_score=0.90, embedding=[0, 0, 1]),
        SimpleNamespace(bbox=(1000, 100, 1500, 600), det_score=0.59, embedding=[1, 1, 0]),
        SimpleNamespace(bbox=(1600, 100, 1640, 140), det_score=0.99, embedding=[1, 0, 1]),
    ]
    pipeline = FaceClusterPipeline(model_root=tmp_path / "models")
    pipeline._analysis_app = SimpleNamespace(get=lambda _image: candidates)

    result = pipeline.detect_faces_for_rows(
        [{"id": "asset-a", "rel": "album/photo.jpg"}],
        library_root=library_root,
        thumbnail_dir=tmp_path / "faces" / "thumbnails",
    )[0]

    assert sorted(round(face.confidence, 2) for face in result.faces) == [0.9, 0.9]
    assert pipeline.last_scan_metrics.face_candidates_total == 5
    assert pipeline.last_scan_metrics.face_rejected_confidence == 1
    assert pipeline.last_scan_metrics.face_rejected_tiny_area == 1
    assert pipeline.last_scan_metrics.face_rejected_relative_area == 1


def test_people_promotion_uses_distinct_assets_and_confirmed_is_sticky(
    tmp_path: Path,
) -> None:
    state = FaceStateRepository(tmp_path / "face_state.db")
    first = _face("a", asset_id="asset-a", person_id="person-a")
    duplicate = _face("b", asset_id="asset-a", person_id="person-a")
    second = _face("c", asset_id="asset-b", person_id="person-a")
    person = _person("person-a", [first, duplicate, second])
    state.sync_scan_results([person], [first, duplicate, second])
    promotion = state.get_promotion_records(["person-a"])["person-a"]
    assert promotion.evidence_asset_count == 2
    assert promotion.promotion_state == "candidate"

    third = _face("d", asset_id="asset-c", person_id="person-a")
    state.sync_scan_results([_person("person-a", [first, second, third])], [first, second, third])
    assert state.get_promotion_records(["person-a"])["person-a"].promotion_state == "eligible"

    state.rename_person("person-a", "Alice")
    state.sync_scan_results([_person("person-a", [first])], [first])
    promotion = state.get_promotion_records(["person-a"])["person-a"]
    assert promotion.evidence_asset_count == 1
    assert promotion.promotion_state == "confirmed"


def test_existing_people_profiles_migrate_as_legacy_visible(tmp_path: Path) -> None:
    state_path = tmp_path / "face_state.db"
    with sqlite3.connect(state_path) as conn:
        conn.execute(
            """
            CREATE TABLE person_profiles (
                person_id TEXT PRIMARY KEY,
                name TEXT,
                center_embedding BLOB,
                embedding_dim INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                profile_state TEXT NOT NULL DEFAULT 'unstable'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE face_keys (
                face_key TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                asset_rel TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO person_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("person-old", "Alice", b"", 0, NOW, NOW, 3, "stable"),
        )
        conn.executemany(
            "INSERT INTO face_keys VALUES (?, ?, ?, ?, ?)",
            [
                ("key-a", "person-old", "asset-a", "a.jpg", NOW),
                ("key-b", "person-old", "asset-b", "b.jpg", NOW),
            ],
        )

    promotion = FaceStateRepository(state_path).get_promotion_records(["person-old"])[
        "person-old"
    ]
    assert promotion.promotion_state == "legacy_visible"
    assert promotion.evidence_asset_count == 2


def test_people_service_hides_candidate_until_third_asset(tmp_path: Path) -> None:
    service = PeopleService(tmp_path)
    repository = service.repository()
    assert repository is not None and repository.state_repository is not None
    first = _face("a", asset_id="asset-a", person_id="person-a")
    second = _face("b", asset_id="asset-b", person_id="person-a")
    first_person = _person("person-a", [first, second])
    repository.replace_all([first, second], [first_person], sync_runtime_state=False)
    repository.state_repository.sync_scan_results([first_person], [first, second])

    assert service.list_clusters() == []
    assert [item.person_id for item in service.list_clusters(include_candidates=True)] == [
        "person-a"
    ]

    third = _face("c", asset_id="asset-c", person_id="person-a")
    promoted_person = _person("person-a", [first, second, third])
    repository.replace_all(
        [first, second, third],
        [promoted_person],
        sync_runtime_state=False,
    )
    repository.state_repository.sync_scan_results(
        [promoted_person],
        [first, second, third],
    )
    assert [item.person_id for item in service.list_clusters()] == ["person-a"]


def test_unassigned_face_can_be_moved_and_deleted_durably(tmp_path: Path) -> None:
    repository = FaceRepository(
        tmp_path / "face_index.db",
        tmp_path / "face_state.db",
    )
    assigned = _face("assigned", asset_id="asset-a", person_id="person-a")
    noise = _face("noise", asset_id="asset-b", person_id=None)
    repository.replace_all([assigned, noise], [_person("person-a", [assigned])])

    assert repository.move_face_to_person("noise", "person-a") is not None
    assert repository.state_repository is not None
    assert (
        repository.state_repository.get_promotion_records(["person-a"])[
            "person-a"
        ].promotion_state
        == "confirmed"
    )

    second_noise = _face("noise-delete", asset_id="asset-c", person_id=None)
    repository.replace_all([second_noise], [], sync_runtime_state=False)
    assert repository.delete_face("noise-delete") is not None
    assert repository.state_repository.get_rejected_face_keys(
        [second_noise.face_key]
    ) == {second_noise.face_key}


def test_pet_quality_gate_rejects_only_tiny_weak_candidate(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    image_path = library_root / "album" / "photo.jpg"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (1600, 1600), color=(120, 100, 80)).save(image_path)
    pipeline = PetClusterPipeline(
        model_root=tmp_path / "models",
        allow_model_download=False,
    )
    pipeline._detector = SimpleNamespace(
        detect=lambda _image: [
            SimpleNamespace(bbox=(10, 10, 50, 50), confidence=0.44, species_label="cat"),
            SimpleNamespace(bbox=(100, 10, 50, 50), confidence=0.90, species_label="cat"),
        ]
    )
    pipeline._embedder = SimpleNamespace(
        embed=lambda _image: np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    )

    result = pipeline.detect_pets_for_rows(
        [{"id": "asset-a", "rel": "album/photo.jpg"}],
        library_root=library_root,
        thumbnail_dir=tmp_path / "pets" / "thumbnails",
    )[0]

    assert [round(item.confidence, 2) for item in result.detections] == [0.9]
    assert pipeline.last_scan_metrics.pet_quality_rejected == 1


def test_pet_same_asset_cannot_link_and_manual_merge_conflict(tmp_path: Path) -> None:
    same_asset = [
        _pet_detection("a", asset_id="asset-a", pet_id=None),
        _pet_detection("b", asset_id="asset-a", pet_id=None, embedding=(0.99, 0.01, 0.0)),
    ]
    cross_asset = [
        same_asset[0],
        _pet_detection("c", asset_id="asset-b", pet_id=None, embedding=(0.99, 0.01, 0.0)),
    ]
    _, same_asset_pets = cluster_pet_records(same_asset, distance_threshold=0.2)
    _, cross_asset_pets = cluster_pet_records(cross_asset, distance_threshold=0.2)
    assert len(same_asset_pets) == 2
    assert len(cross_asset_pets) == 1

    repository = PetRepository(
        tmp_path / "pet_index.db",
        tmp_path / "pet_state.db",
    )
    source = _pet_detection("source", asset_id="asset-shared", pet_id="pet-source")
    target = _pet_detection("target", asset_id="asset-shared", pet_id="pet-target")
    repository.replace_all(
        [source, target],
        [_pet("pet-source", [source]), _pet("pet-target", [target])],
    )
    assert repository.merge_pets("pet-source", "pet-target") is None
    assert repository.last_mutation_failure == PetMutationFailure.SAME_ASSET_CONFLICT
    assert repository.same_asset_manual_conflicts == 1


def test_pet_manual_move_returns_same_asset_conflict(tmp_path: Path) -> None:
    service = PetService(tmp_path)
    repository = service.repository()
    assert repository is not None
    source = _pet_detection("source", asset_id="asset-shared", pet_id="pet-source")
    target = _pet_detection("target", asset_id="asset-shared", pet_id="pet-target")
    repository.replace_all(
        [source, target],
        [_pet("pet-source", [source]), _pet("pet-target", [target])],
    )

    outcome = service.move_detection_to_pet_with_outcome("source", "pet-target")

    assert outcome.succeeded is False
    assert outcome.failure == PetMutationFailure.SAME_ASSET_CONFLICT
    service.shutdown()


def test_pet_promotion_uses_cross_asset_evidence(tmp_path: Path) -> None:
    state = PetStateRepository(tmp_path / "pet_state.db")
    first = _pet_detection("a", asset_id="asset-a", pet_id="pet-a")
    duplicate = _pet_detection("b", asset_id="asset-a", pet_id="pet-a")
    state.sync_scan_results([_pet("pet-a", [first, duplicate])], [first, duplicate])
    promotion = state.get_promotion_records(["pet-a"])["pet-a"]
    assert promotion.evidence_asset_count == 1
    assert promotion.promotion_state == "candidate"

    second = _pet_detection("c", asset_id="asset-b", pet_id="pet-a")
    state.sync_scan_results([_pet("pet-a", [first, second])], [first, second])
    promotion = state.get_promotion_records(["pet-a"])["pet-a"]
    assert promotion.evidence_asset_count == 2
    assert promotion.promotion_state == "eligible"


def test_pet_merge_persists_union_evidence_before_runtime_state_sync(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = PetRepository(
        tmp_path / "pet_index.db",
        tmp_path / "pet_state.db",
    )
    source = _pet_detection("source", asset_id="asset-a", pet_id="pet-source")
    target = _pet_detection("target", asset_id="asset-b", pet_id="pet-target")
    repository.replace_all(
        [source, target],
        [_pet("pet-source", [source]), _pet("pet-target", [target])],
    )
    state = repository.state_repository
    assert state is not None
    before = state.get_promotion_records(["pet-source", "pet-target"])
    assert {record.evidence_asset_count for record in before.values()} == {1}
    assert {record.promotion_state for record in before.values()} == {"candidate"}
    monkeypatch.setattr(repository, "complete_runtime_state_sync", lambda _operation_id: None)

    result = repository.merge_pets("pet-source", "pet-target")

    assert result is not None
    promotion = state.get_promotion_records(["pet-target"])["pet-target"]
    assert promotion.evidence_asset_count == 2
    assert promotion.promotion_state == "eligible"
