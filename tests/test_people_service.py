from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
from PIL import Image

from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.config import WORK_DIR_NAME
from iPhoto.library.workers.face_scan_worker import FaceScanWorker
from iPhoto.people.index_coordinator import (
    PeopleSnapshotCommittedError,
    get_people_index_coordinator,
    reset_people_index_coordinators,
)
from iPhoto.library.workers.scanner_worker import ScannerSignals, ScannerWorker
from iPhoto.people.pipeline import DetectedAssetFaces
from iPhoto.people.records import AssetFaceAnnotation
from iPhoto.people.repository import FaceRecord, ManualFaceRecord, PersonRecord
from iPhoto.people.scan_session import FaceScanSession
from iPhoto.people import service as people_service
from iPhoto.people.service import PeopleService, face_library_paths, shared_face_model_dir


@pytest.fixture(autouse=True)
def _reset_global_repository() -> None:
    reset_global_repository()
    reset_people_index_coordinators()
    yield
    reset_global_repository()
    reset_people_index_coordinators()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _face_record(*, face_id: str, asset_id: str, asset_rel: str, person_id: str) -> FaceRecord:
    embedding = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    return FaceRecord(
        face_id=face_id,
        face_key=f"key-{face_id}",
        asset_id=asset_id,
        asset_rel=asset_rel,
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


def _manual_face_record(
    *,
    face_id: str,
    asset_id: str,
    asset_rel: str,
    person_id: str,
    thumbnail_path: str | None = None,
) -> ManualFaceRecord:
    return ManualFaceRecord(
        face_id=face_id,
        asset_id=asset_id,
        asset_rel=asset_rel,
        box_x=10,
        box_y=12,
        box_w=80,
        box_h=80,
        thumbnail_path=thumbnail_path,
        person_id=person_id,
        created_at=_now_iso(),
        image_width=400,
        image_height=300,
    )


def _write_image(library_root: Path, rel_path: str, size: tuple[int, int] = (400, 300)) -> Path:
    image_path = library_root / rel_path
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=(120, 130, 140)).save(image_path)
    return image_path


def _person_record(
    *, person_id: str, key_face_id: str, face_count: int, name: str | None = None
) -> PersonRecord:
    embedding = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    timestamp = _now_iso()
    sample_count = int(face_count)
    return PersonRecord(
        person_id=person_id,
        name=name,
        key_face_id=key_face_id,
        face_count=face_count,
        center_embedding=embedding,
        created_at=timestamp,
        updated_at=timestamp,
        sample_count=sample_count,
        profile_state="stable" if sample_count >= 3 else "unstable",
    )


def _face_record_with_embedding(
    *,
    face_id: str,
    face_key: str,
    asset_id: str,
    asset_rel: str,
    person_id: str | None,
    embedding: np.ndarray,
) -> FaceRecord:
    return FaceRecord(
        face_id=face_id,
        face_key=face_key,
        asset_id=asset_id,
        asset_rel=asset_rel,
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


def _person_record_with_embedding(
    *,
    person_id: str,
    key_face_id: str,
    face_count: int,
    name: str | None,
    embedding: np.ndarray,
) -> PersonRecord:
    timestamp = _now_iso()
    sample_count = int(face_count)
    return PersonRecord(
        person_id=person_id,
        name=name,
        key_face_id=key_face_id,
        face_count=face_count,
        center_embedding=embedding,
        created_at=timestamp,
        updated_at=timestamp,
        sample_count=sample_count,
        profile_state="stable" if sample_count >= 3 else "unstable",
    )


def test_face_library_paths_live_under_dot_iphoto(tmp_path: Path) -> None:
    paths = face_library_paths(tmp_path)

    assert paths.root_dir == tmp_path / WORK_DIR_NAME / "faces"
    assert paths.index_db_path == paths.root_dir / "face_index.db"
    assert paths.state_db_path == paths.root_dir / "face_state.db"
    assert paths.thumbnail_dir == paths.root_dir / "thumbnails"
    assert paths.model_dir == shared_face_model_dir()
    assert paths.model_dir == Path(__file__).resolve().parents[1] / "src" / "extension" / "models"


def test_default_shared_face_model_dir_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model_dir = tmp_path / "face-models"

    monkeypatch.setenv("IPHOTO_FACE_MODEL_DIR", str(model_dir))

    assert people_service._default_shared_face_model_dir() == model_dir


def test_people_service_rename_merge_and_build_query(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()

    global_repo = get_global_repository(library_root)
    global_repo.write_rows(
        [
            {"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "done"},
            {"rel": "album/b.jpg", "id": "asset-b", "media_type": 0, "face_status": "done"},
        ]
    )

    service = PeopleService(library_root)
    repository = service.repository()
    assert repository is not None

    face_a = _face_record(
        face_id="face-a", asset_id="asset-a", asset_rel="album/a.jpg", person_id="person-a"
    )
    face_b = _face_record(
        face_id="face-b", asset_id="asset-b", asset_rel="album/b.jpg", person_id="person-b"
    )
    person_a = _person_record(person_id="person-a", key_face_id="face-a", face_count=1)
    person_b = _person_record(person_id="person-b", key_face_id="face-b", face_count=1, name="Bob")
    repository.replace_all([face_a, face_b], [person_a, person_b])

    service.rename_cluster("person-a", "Alice")
    summaries = service.list_clusters()
    assert {summary.person_id: summary.name for summary in summaries}["person-a"] == "Alice"

    query = service.build_cluster_query("person-a")
    assert query.asset_ids == ["asset-a"]

    assert service.merge_clusters("person-a", "person-b") is True
    merged = service.list_clusters()
    assert len(merged) == 1
    assert merged[0].person_id == "person-b"
    assert merged[0].face_count == 2
    assert merged[0].name == "Bob"


def test_people_service_creates_groups_and_queries_common_assets(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()

    global_repo = get_global_repository(library_root)
    global_repo.write_rows(
        [
            {
                "rel": "album/shared.jpg",
                "id": "asset-shared",
                "media_type": 0,
                "face_status": "done",
            },
            {
                "rel": "album/a.jpg",
                "id": "asset-a",
                "media_type": 0,
                "face_status": "done",
            },
        ]
    )

    service = PeopleService(library_root)
    repository = service.repository()
    assert repository is not None

    faces = [
        _face_record(
            face_id="face-a-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-b",
        ),
        _face_record(
            face_id="face-a-missing",
            asset_id="asset-missing",
            asset_rel="album/missing.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b-missing",
            asset_id="asset-missing",
            asset_rel="album/missing.jpg",
            person_id="person-b",
        ),
        _face_record(
            face_id="face-a-only",
            asset_id="asset-a",
            asset_rel="album/a.jpg",
            person_id="person-a",
        ),
    ]
    persons = [
        _person_record(
            person_id="person-a",
            key_face_id="face-a-shared",
            face_count=3,
            name="Alice",
        ),
        _person_record(
            person_id="person-b",
            key_face_id="face-b-shared",
            face_count=2,
            name=None,
        ),
    ]
    repository.replace_all(faces, persons)

    assert service.create_group(["person-a"]) is None
    group = service.create_group(["person-a", "person-b"])
    assert group is not None
    assert group.name == "Alice"
    assert group.member_person_ids == ("person-a", "person-b")
    assert group.asset_count == 1
    assert group.cover_asset_path == library_root / "album/shared.jpg"

    listed = service.list_groups()
    assert len(listed) == 1
    assert listed[0].group_id == group.group_id

    query = service.build_group_query(group.group_id)
    assert query.asset_ids == ["asset-shared"]
    assert service.has_group(group.group_id) is True
    assert service.get_group_summary(group.group_id) is not None
    assert service.has_group("missing-group") is False


def test_people_service_uses_persisted_group_cover(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()

    global_repo = get_global_repository(library_root)
    global_repo.write_rows(
        [
            {
                "rel": "album/older.jpg",
                "id": "asset-older",
                "media_type": 0,
                "face_status": "done",
            },
            {
                "rel": "album/newer.jpg",
                "id": "asset-newer",
                "media_type": 0,
                "face_status": "done",
            },
        ]
    )

    service = PeopleService(library_root)
    repository = service.repository()
    assert repository is not None
    faces = [
        _face_record(
            face_id="face-a-older",
            asset_id="asset-older",
            asset_rel="album/older.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b-older",
            asset_id="asset-older",
            asset_rel="album/older.jpg",
            person_id="person-b",
        ),
        _face_record(
            face_id="face-a-newer",
            asset_id="asset-newer",
            asset_rel="album/newer.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b-newer",
            asset_id="asset-newer",
            asset_rel="album/newer.jpg",
            person_id="person-b",
        ),
    ]
    persons = [
        _person_record(
            person_id="person-a",
            key_face_id="face-a-newer",
            face_count=2,
            name="Alice",
        ),
        _person_record(
            person_id="person-b",
            key_face_id="face-b-newer",
            face_count=2,
            name="Bob",
        ),
    ]
    repository.replace_all(faces, persons)

    group = service.create_group(["person-a", "person-b"])
    assert group is not None
    assert group.cover_asset_path == library_root / "album/newer.jpg"

    assert service.set_group_cover(group.group_id, "asset-older") is True
    listed = service.list_groups()
    assert listed[0].cover_asset_path == library_root / "album/older.jpg"


def test_people_service_can_delete_group(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()

    global_repo = get_global_repository(library_root)
    global_repo.write_rows(
        [
            {"rel": "album/shared.jpg", "id": "asset-shared", "media_type": 0, "face_status": "done"},
        ]
    )

    service = PeopleService(library_root)
    repository = service.repository()
    assert repository is not None
    repository.replace_all(
        [
            _face_record(
                face_id="face-a-shared",
                asset_id="asset-shared",
                asset_rel="album/shared.jpg",
                person_id="person-a",
            ),
            _face_record(
                face_id="face-b-shared",
                asset_id="asset-shared",
                asset_rel="album/shared.jpg",
                person_id="person-b",
            ),
        ],
        [
            _person_record(person_id="person-a", key_face_id="face-a-shared", face_count=1, name="Alice"),
            _person_record(person_id="person-b", key_face_id="face-b-shared", face_count=1, name="Bob"),
        ],
    )
    group = service.create_group(["person-a", "person-b"])
    assert group is not None

    assert service.delete_group(group.group_id) is True
    assert service.list_groups() == []


def test_people_service_load_dashboard_reuses_cluster_snapshot_for_groups(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()

    global_repo = get_global_repository(library_root)
    global_repo.write_rows(
        [
            {"rel": "album/shared.jpg", "id": "asset-shared", "media_type": 0, "face_status": "done"},
            {"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "pending"},
        ]
    )

    service = PeopleService(library_root)
    repository = service.repository()
    assert repository is not None
    faces = [
        _face_record(
            face_id="face-a-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-b",
        ),
    ]
    persons = [
        _person_record(
            person_id="person-a",
            key_face_id="face-a-shared",
            face_count=1,
            name="Alice",
        ),
        _person_record(
            person_id="person-b",
            key_face_id="face-b-shared",
            face_count=1,
            name="Bob",
        ),
    ]
    repository.replace_all(faces, persons)
    group = service.create_group(["person-a", "person-b"])
    assert group is not None

    summaries, groups, pending = service.load_dashboard()

    assert [summary.person_id for summary in summaries] == ["person-a", "person-b"]
    assert len(groups) == 1
    assert groups[0].group_id == group.group_id
    assert groups[0].cover_asset_path == library_root / "album/shared.jpg"
    assert pending == 1


def test_people_service_can_hide_people_and_optionally_include_them(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()

    global_repo = get_global_repository(library_root)
    global_repo.write_rows(
        [
            {"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "done"},
            {"rel": "album/b.jpg", "id": "asset-b", "media_type": 0, "face_status": "done"},
        ]
    )

    service = PeopleService(library_root)
    repository = service.repository()
    assert repository is not None
    repository.replace_all(
        [
            _face_record(face_id="face-a", asset_id="asset-a", asset_rel="album/a.jpg", person_id="person-a"),
            _face_record(face_id="face-b", asset_id="asset-b", asset_rel="album/b.jpg", person_id="person-b"),
        ],
        [
            _person_record(person_id="person-a", key_face_id="face-a", face_count=1, name="Alice"),
            _person_record(person_id="person-b", key_face_id="face-b", face_count=1, name="Bob"),
        ],
    )

    assert service.set_cluster_hidden("person-b", True) is True
    assert service.is_cluster_hidden("person-b") is True
    assert [summary.person_id for summary in service.list_clusters()] == ["person-a"]
    assert {summary.person_id: summary.is_hidden for summary in service.list_clusters(include_hidden=True)} == {
        "person-a": False,
        "person-b": True,
    }


def test_people_service_merge_blocks_when_hidden_state_differs(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()

    global_repo = get_global_repository(library_root)
    global_repo.write_rows(
        [
            {"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "done"},
            {"rel": "album/b.jpg", "id": "asset-b", "media_type": 0, "face_status": "done"},
        ]
    )

    service = PeopleService(library_root)
    repository = service.repository()
    assert repository is not None
    repository.replace_all(
        [
            _face_record(face_id="face-a", asset_id="asset-a", asset_rel="album/a.jpg", person_id="person-a"),
            _face_record(face_id="face-b", asset_id="asset-b", asset_rel="album/b.jpg", person_id="person-b"),
        ],
        [
            _person_record(person_id="person-a", key_face_id="face-a", face_count=1, name="Alice"),
            _person_record(person_id="person-b", key_face_id="face-b", face_count=1, name="Bob"),
        ],
    )
    assert service.set_cluster_hidden("person-a", True) is True

    assert service.merge_clusters("person-a", "person-b") is False
    assert {summary.person_id for summary in service.list_clusters(include_hidden=True)} == {
        "person-a",
        "person-b",
    }


def test_people_service_can_mark_retry_and_skipped(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    global_repo = get_global_repository(library_root)
    global_repo.write_rows(
        [{"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "pending"}]
    )

    service = PeopleService(library_root)

    assert service.mark_asset_retry("asset-a") is True
    assert global_repo.get_rows_by_ids(["asset-a"])["asset-a"]["face_status"] == "retry"

    assert service.mark_asset_skipped("asset-a") is True
    assert global_repo.get_rows_by_ids(["asset-a"])["asset-a"]["face_status"] == "skipped"


def test_people_service_lists_asset_face_annotations_and_preserves_names(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    global_repo = get_global_repository(library_root)
    global_repo.write_rows(
        [{"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "done"}]
    )

    service = PeopleService(library_root)
    repository = service.repository()
    assert repository is not None

    face = _face_record(
        face_id="face-a",
        asset_id="asset-a",
        asset_rel="album/a.jpg",
        person_id="person-a",
    )
    person = _person_record(person_id="person-a", key_face_id="face-a", face_count=1)
    repository.replace_all([face], [person])

    initial = service.list_asset_face_annotations("asset-a")
    assert len(initial) == 1
    assert initial[0].display_name is None
    assert initial[0].image_height == 300

    service.rename_cluster("person-a", "Alice")
    updated = service.list_asset_face_annotations("asset-a")
    assert updated[0].display_name == "Alice"


def test_add_manual_face_updates_cover_without_changing_ai_profile(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    _write_image(library_root, "album/a.jpg")
    get_global_repository(library_root).write_rows(
        [{"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "done"}]
    )

    service = PeopleService(library_root)
    repository = service.repository()
    assert repository is not None
    auto_face = _face_record(
        face_id="face-a",
        asset_id="asset-a",
        asset_rel="album/a.jpg",
        person_id="person-a",
    )
    auto_person = _person_record(
        person_id="person-a",
        key_face_id="face-a",
        face_count=1,
        name="Alice",
    )
    repository.replace_all([auto_face], [auto_person])
    assert repository.state_repository is not None
    repository.state_repository.sync_scan_results([auto_person], [auto_face])

    result = service.add_manual_face(
        asset_id="asset-a",
        requested_box=(100, 80, 90, 90),
        name_or_none="Alice",
        person_id="person-a",
    )

    manual_faces = repository.state_repository.get_manual_faces()
    assert [face.face_id for face in manual_faces] == [result.face_id]
    runtime_faces = repository.get_all_faces()
    assert len(runtime_faces) == 1
    assert runtime_faces[0].face_id == auto_face.face_id
    assert runtime_faces[0].person_id == auto_face.person_id
    summaries = service.list_clusters()
    assert len(summaries) == 1
    assert summaries[0].face_count == 2
    assert summaries[0].thumbnail_path == (
        library_root / WORK_DIR_NAME / "faces" / manual_faces[0].thumbnail_path
    ).resolve()
    profiles = {profile.person_id: profile for profile in repository.state_repository.get_profiles()}
    assert profiles["person-a"].sample_count == 1
    assert profiles["person-a"].center_embedding.shape == (3,)
    assert repository.state_repository.get_face_key_map([auto_face.face_key]) == {
        auto_face.face_key: "person-a"
    }
    annotations = service.list_asset_face_annotations("asset-a")
    assert [annotation.is_manual for annotation in annotations] == [False, True]
    assert annotations[1].display_name == "Alice"
    assert (annotations[1].box_x, annotations[1].box_y, annotations[1].box_w, annotations[1].box_h) == (
        100,
        80,
        90,
        90,
    )


def test_add_manual_face_creates_manual_only_person(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    _write_image(library_root, "album/a.jpg")
    get_global_repository(library_root).write_rows(
        [{"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "done"}]
    )

    service = PeopleService(library_root)
    result = service.add_manual_face(
        asset_id="asset-a",
        requested_box=(40, 50, 80, 80),
        name_or_none="Manual Person",
        person_id=None,
    )

    repository = service.repository()
    assert repository is not None
    summaries = service.list_clusters()
    assert len(summaries) == 1
    assert summaries[0].person_id == result.person_id
    assert summaries[0].name == "Manual Person"
    assert summaries[0].face_count == 1
    assert summaries[0].key_face_id == result.face_id
    assert service.build_cluster_query(result.person_id).asset_ids == ["asset-a"]
    assert repository.get_all_faces() == []
    profile = repository.state_repository.get_profiles()[0]
    assert profile.person_id == result.person_id
    assert profile.sample_count == 0
    assert profile.center_embedding.size == 0
    annotations = service.list_asset_face_annotations("asset-a")
    assert len(annotations) == 1
    assert annotations[0].is_manual is True
    assert annotations[0].display_name == "Manual Person"


def test_merge_manual_only_people_keeps_profiles_embedding_free(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    _write_image(library_root, "album/a.jpg")
    _write_image(library_root, "album/b.jpg")
    get_global_repository(library_root).write_rows(
        [
            {"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "done"},
            {"rel": "album/b.jpg", "id": "asset-b", "media_type": 0, "face_status": "done"},
        ]
    )

    service = PeopleService(library_root)
    source = service.add_manual_face(
        asset_id="asset-a",
        requested_box=(40, 50, 80, 80),
        name_or_none="Source",
        person_id=None,
    )
    target = service.add_manual_face(
        asset_id="asset-b",
        requested_box=(60, 70, 80, 80),
        name_or_none="Target",
        person_id=None,
    )

    assert service.merge_clusters(source.person_id, target.person_id) is True

    repository = service.repository()
    assert repository is not None
    summaries = service.list_clusters()
    assert len(summaries) == 1
    assert summaries[0].person_id == target.person_id
    assert summaries[0].name == "Target"
    assert summaries[0].face_count == 2
    assert service.build_cluster_query(target.person_id).asset_ids == ["asset-b", "asset-a"]
    assert repository.get_all_faces() == []
    profiles = {profile.person_id: profile for profile in repository.state_repository.get_profiles()}
    assert set(profiles) == {target.person_id}
    assert profiles[target.person_id].sample_count == 0
    assert profiles[target.person_id].center_embedding.size == 0
    assert {
        face.person_id for face in repository.state_repository.get_manual_faces()
    } == {target.person_id}


def test_merge_manual_person_into_auto_person_preserves_auto_profile_decision(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    _write_image(library_root, "album/auto.jpg")
    _write_image(library_root, "album/manual.jpg")
    get_global_repository(library_root).write_rows(
        [
            {"rel": "album/auto.jpg", "id": "asset-auto", "media_type": 0, "face_status": "done"},
            {"rel": "album/manual.jpg", "id": "asset-manual", "media_type": 0, "face_status": "done"},
        ]
    )

    service = PeopleService(library_root)
    repository = service.repository()
    assert repository is not None
    auto_face = _face_record(
        face_id="face-auto",
        asset_id="asset-auto",
        asset_rel="album/auto.jpg",
        person_id="person-auto",
    )
    auto_person = _person_record(
        person_id="person-auto",
        key_face_id="face-auto",
        face_count=1,
        name="Auto",
    )
    repository.replace_all([auto_face], [auto_person])
    assert repository.state_repository is not None
    repository.state_repository.sync_scan_results([auto_person], [auto_face])
    manual = service.add_manual_face(
        asset_id="asset-manual",
        requested_box=(60, 70, 80, 80),
        name_or_none="Manual",
        person_id=None,
    )

    assert service.merge_clusters(manual.person_id, "person-auto") is True

    summaries = service.list_clusters()
    assert len(summaries) == 1
    assert summaries[0].person_id == "person-auto"
    assert summaries[0].face_count == 2
    assert service.build_cluster_query("person-auto").asset_ids == [
        "asset-manual",
        "asset-auto",
    ]
    profiles = {profile.person_id: profile for profile in repository.state_repository.get_profiles()}
    assert set(profiles) == {"person-auto"}
    assert profiles["person-auto"].sample_count == 1
    assert profiles["person-auto"].center_embedding.shape == (3,)
    assert repository.state_repository.get_face_key_map([auto_face.face_key]) == {
        auto_face.face_key: "person-auto"
    }


def test_people_service_lists_person_name_suggestions_for_named_people_only(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    global_repo = get_global_repository(library_root)
    global_repo.write_rows(
        [
            {"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "done"},
            {"rel": "album/b.jpg", "id": "asset-b", "media_type": 0, "face_status": "done"},
        ]
    )

    service = PeopleService(library_root)
    repository = service.repository()
    assert repository is not None

    faces = [
        _face_record(
            face_id="face-a",
            asset_id="asset-a",
            asset_rel="album/a.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b",
            asset_id="asset-b",
            asset_rel="album/b.jpg",
            person_id="person-b",
        ),
    ]
    persons = [
        _person_record(person_id="person-a", key_face_id="face-a", face_count=1, name="Alice"),
        _person_record(person_id="person-b", key_face_id="face-b", face_count=1, name=None),
    ]
    repository.replace_all(faces, persons)

    suggestions = service.list_person_name_suggestions()

    assert [(summary.person_id, summary.name) for summary in suggestions] == [("person-a", "Alice")]


def test_people_service_dashboard_stays_stable_until_face_scan_session_commits(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()

    global_repo = get_global_repository(library_root)
    global_repo.write_rows(
        [
            {"rel": "album/shared.jpg", "id": "asset-shared", "media_type": 0, "face_status": "done"},
            {
                "rel": "album/new-shared.jpg",
                "id": "asset-new-shared",
                "media_type": 0,
                "face_status": "pending",
            },
        ]
    )

    service = PeopleService(library_root)
    repository = service.repository()
    assert repository is not None

    embedding_a = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    embedding_b = np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
    initial_faces = [
        _face_record_with_embedding(
            face_id="face-a-shared",
            face_key="face-key-a-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-a",
            embedding=embedding_a,
        ),
        _face_record_with_embedding(
            face_id="face-b-shared",
            face_key="face-key-b-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-b",
            embedding=embedding_b,
        ),
    ]
    initial_persons = [
        _person_record_with_embedding(
            person_id="person-a",
            key_face_id="face-a-shared",
            face_count=1,
            name="Alice",
            embedding=embedding_a,
        ),
        _person_record_with_embedding(
            person_id="person-b",
            key_face_id="face-b-shared",
            face_count=1,
            name="Bob",
            embedding=embedding_b,
        ),
    ]
    repository.replace_all(initial_faces, initial_persons)
    assert repository.state_repository is not None
    repository.state_repository.sync_scan_results(initial_persons, initial_faces)
    group = service.create_group(["person-a", "person-b"])
    assert group is not None

    session = FaceScanSession()
    session.stage_detection_results(
        [
            DetectedAssetFaces(
                asset_id="asset-new-shared",
                asset_rel="album/new-shared.jpg",
                faces=[
                    _face_record_with_embedding(
                        face_id="face-a-new-shared",
                        face_key="face-key-a-new-shared",
                        asset_id="asset-new-shared",
                        asset_rel="album/new-shared.jpg",
                        person_id=None,
                        embedding=np.asarray([0.98, 0.02, 0.0], dtype=np.float32),
                    ),
                    _face_record_with_embedding(
                        face_id="face-b-new-shared",
                        face_key="face-key-b-new-shared",
                        asset_id="asset-new-shared",
                        asset_rel="album/new-shared.jpg",
                        person_id=None,
                        embedding=np.asarray([0.02, 0.98, 0.0], dtype=np.float32),
                    ),
                ],
            )
        ]
    )

    summaries_before, groups_before, pending_before = service.load_dashboard()
    assert [summary.person_id for summary in summaries_before] == ["person-a", "person-b"]
    assert groups_before[0].group_id == group.group_id
    assert groups_before[0].asset_count == 1
    assert pending_before == 1

    session.commit(repository, distance_threshold=0.6, min_samples=2)
    global_repo.update_face_status("asset-new-shared", "done")

    summaries_after, groups_after, pending_after = service.load_dashboard()
    assert [summary.person_id for summary in summaries_after] == ["person-a", "person-b"]
    assert groups_after[0].group_id == group.group_id
    assert groups_after[0].asset_count == 2
    assert pending_after == 0


def test_people_service_face_mutation_methods_delegate_to_coordinator(tmp_path: Path) -> None:
    service = PeopleService(tmp_path)
    coordinator = Mock(
        delete_face=Mock(return_value=object()),
        move_face_to_person=Mock(return_value=object()),
        move_face_to_new_person=Mock(return_value=object()),
    )

    with (
        patch("iPhoto.people.service.get_people_index_coordinator", return_value=coordinator),
        patch("iPhoto.people.service.uuid.uuid4", return_value=SimpleNamespace(hex="person-new")),
    ):
        assert service.delete_face("face-1") is True
        assert service.move_face_to_person("face-1", "person-b") is True
        assert service.move_face_to_new_person("face-1", "Alice 2") == "person-new"

    coordinator.delete_face.assert_called_once_with("face-1")
    coordinator.move_face_to_person.assert_called_once_with("face-1", "person-b")
    coordinator.move_face_to_new_person.assert_called_once_with("face-1", "person-new", "Alice 2")


def test_resolve_cluster_cover_face_returns_matching_face_id(tmp_path: Path) -> None:
    service = PeopleService(tmp_path)

    with patch.object(
        service,
        "list_asset_face_annotations",
        return_value=[
            AssetFaceAnnotation(
                face_id="face-other",
                person_id="person-b",
                display_name="Bob",
                box_x=0,
                box_y=0,
                box_w=10,
                box_h=10,
                image_width=100,
                image_height=100,
            ),
            AssetFaceAnnotation(
                face_id="face-match",
                person_id="person-a",
                display_name="Alice",
                box_x=0,
                box_y=0,
                box_w=10,
                box_h=10,
                image_width=100,
                image_height=100,
            ),
        ],
    ):
        assert service.resolve_cluster_cover_face("person-a", "asset-1") == "face-match"
        assert service.resolve_cluster_cover_face("person-c", "asset-1") is None


def test_resolve_group_cover_asset_validates_group_membership(tmp_path: Path) -> None:
    service = PeopleService(tmp_path)

    with patch.object(service, "group_asset_ids", return_value=["asset-1", "asset-2"]):
        assert service.resolve_group_cover_asset("group-a", "asset-1") == "asset-1"
        assert service.resolve_group_cover_asset("group-a", "asset-3") is None


def test_face_scan_worker_enqueue_rows_skips_done_assets(tmp_path: Path) -> None:
    worker = FaceScanWorker(tmp_path)

    worker.enqueue_rows(
        [
            {"id": "asset-done", "media_type": 0, "face_status": "done"},
            {"id": "asset-pending", "media_type": 0, "face_status": "pending"},
            {"id": "asset-retry", "media_type": 0, "face_status": "retry"},
            {"id": "asset-video", "media_type": 1, "face_status": "pending"},
        ]
    )

    batch = worker._next_batch()

    assert [row["id"] for row in batch] == ["asset-pending", "asset-retry"]


def test_people_index_coordinator_commits_realtime_dashboard_snapshot(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()

    global_repo = get_global_repository(library_root)
    global_repo.write_rows(
        [
            {"rel": "album/shared.jpg", "id": "asset-shared", "media_type": 0, "face_status": "done"},
            {"rel": "album/new.jpg", "id": "asset-new", "media_type": 0, "face_status": "pending"},
        ]
    )

    service = PeopleService(library_root)
    repository = service.repository()
    assert repository is not None

    embedding_a = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    embedding_b = np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
    initial_faces = [
        _face_record_with_embedding(
            face_id="face-a-shared",
            face_key="face-key-a-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-a",
            embedding=embedding_a,
        ),
        _face_record_with_embedding(
            face_id="face-b-shared",
            face_key="face-key-b-shared",
            asset_id="asset-shared",
            asset_rel="album/shared.jpg",
            person_id="person-b",
            embedding=embedding_b,
        ),
    ]
    initial_persons = [
        _person_record_with_embedding(
            person_id="person-a",
            key_face_id="face-a-shared",
            face_count=1,
            name="Alice",
            embedding=embedding_a,
        ),
        _person_record_with_embedding(
            person_id="person-b",
            key_face_id="face-b-shared",
            face_count=1,
            name="Bob",
            embedding=embedding_b,
        ),
    ]
    repository.replace_all(initial_faces, initial_persons)
    assert repository.state_repository is not None
    repository.state_repository.sync_scan_results(initial_persons, initial_faces)
    group = service.create_group(["person-a", "person-b"])
    assert group is not None

    coordinator = get_people_index_coordinator(library_root)
    event = coordinator.submit_detected_batch(
        [
            DetectedAssetFaces(
                asset_id="asset-new",
                asset_rel="album/new.jpg",
                faces=[
                    _face_record_with_embedding(
                        face_id="face-a-new",
                        face_key="face-key-a-new",
                        asset_id="asset-new",
                        asset_rel="album/new.jpg",
                        person_id=None,
                        embedding=np.asarray([0.98, 0.02, 0.0], dtype=np.float32),
                    ),
                    _face_record_with_embedding(
                        face_id="face-b-new",
                        face_key="face-key-b-new",
                        asset_id="asset-new",
                        asset_rel="album/new.jpg",
                        person_id=None,
                        embedding=np.asarray([0.02, 0.98, 0.0], dtype=np.float32),
                    ),
                ],
            )
        ],
        distance_threshold=0.6,
        min_samples=2,
    )

    assert event is not None
    assert event.changed_asset_ids == ("asset-new",)
    assert global_repo.get_rows_by_ids(["asset-new"])["asset-new"]["face_status"] == "done"

    summaries, groups, pending = service.load_dashboard()
    assert [summary.person_id for summary in summaries] == ["person-a", "person-b"]
    assert groups[0].group_id == group.group_id
    assert groups[0].asset_count == 2
    assert pending == 0


def test_face_scan_worker_skips_commit_when_cancelled_mid_batch(tmp_path: Path) -> None:
    global_repo = get_global_repository(tmp_path)
    global_repo.write_rows(
        [
            {"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "pending"},
        ]
    )
    worker = FaceScanWorker(tmp_path)
    coordinator = Mock()

    def _detect_faces_for_rows(*_args, **_kwargs):
        worker.cancel()
        return [
            DetectedAssetFaces(
                asset_id="asset-a",
                asset_rel="album/a.jpg",
                faces=[],
            )
        ]

    pipeline = SimpleNamespace(
        detect_faces_for_rows=_detect_faces_for_rows,
        distance_threshold=0.6,
        min_samples=2,
    )

    committed = worker._process_batch(
        [{"id": "asset-a", "rel": "album/a.jpg", "media_type": 0, "face_status": "pending"}],
        coordinator,
        pipeline,
        tmp_path / "thumbs",
    )

    assert committed is False
    coordinator.submit_detected_batch.assert_not_called()
    assert global_repo.get_rows_by_ids(["asset-a"])["asset-a"]["face_status"] == "retry"


def test_face_scan_worker_marks_retry_error_failed_on_second_attempt(tmp_path: Path) -> None:
    global_repo = get_global_repository(tmp_path)
    global_repo.write_rows(
        [
            {"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "retry"},
        ]
    )
    worker = FaceScanWorker(tmp_path)
    messages: list[str] = []
    worker.statusChanged.connect(messages.append)
    coordinator = Mock(submit_detected_batch=Mock(return_value=None))
    pipeline = SimpleNamespace(
        detect_faces_for_rows=Mock(
            return_value=[
                DetectedAssetFaces(
                    asset_id="asset-a",
                    asset_rel="album/a.jpg",
                    faces=[],
                    error="image decode failed",
                )
            ]
        ),
        distance_threshold=0.6,
        min_samples=2,
    )

    committed = worker._process_batch(
        [{"id": "asset-a", "rel": "album/a.jpg", "media_type": 0, "face_status": "retry"}],
        coordinator,
        pipeline,
        tmp_path / "thumbs",
    )

    assert committed is False
    assert global_repo.get_rows_by_ids(["asset-a"])["asset-a"]["face_status"] == "failed"
    assert messages == [
        "Some assets could not be face scanned and will be retried after a rescan."
    ]
    coordinator.submit_detected_batch.assert_called_once()
    assert coordinator.submit_detected_batch.call_args.args[0] == []


def test_face_scan_worker_logs_asset_retry_reason(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    global_repo = get_global_repository(tmp_path)
    global_repo.write_rows(
        [
            {"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "pending"},
        ]
    )
    worker = FaceScanWorker(tmp_path)
    coordinator = Mock(submit_detected_batch=Mock(return_value=None))
    pipeline = SimpleNamespace(
        detect_faces_for_rows=Mock(
            return_value=[
                DetectedAssetFaces(
                    asset_id="asset-a",
                    asset_rel="album/a.jpg",
                    faces=[],
                    error="name 'Literal' is not defined",
                )
            ]
        ),
        distance_threshold=0.6,
        min_samples=2,
    )

    with caplog.at_level("WARNING", logger="iPhoto"):
        worker._process_batch(
            [{"id": "asset-a", "rel": "album/a.jpg", "media_type": 0, "face_status": "pending"}],
            coordinator,
            pipeline,
            tmp_path / "thumbs",
        )

    assert "Face scan failed for asset asset-a (album/a.jpg): name 'Literal' is not defined" in caplog.text


def test_people_index_coordinator_retries_done_status_bookkeeping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()

    coordinator = get_people_index_coordinator(library_root)
    calls: list[tuple[tuple[str, ...], str]] = []

    class FlakyStore:
        def update_face_statuses(self, asset_ids: list[str], status: str) -> None:
            calls.append((tuple(asset_ids), status))
            if status == "done" and len(calls) == 1:
                raise RuntimeError("temporary lock")

    monkeypatch.setattr(
        "iPhoto.people.index_coordinator.get_global_repository",
        lambda _root: FlakyStore(),
    )
    event = coordinator.submit_detected_batch(
        [
            DetectedAssetFaces(
                asset_id="asset-a",
                asset_rel="album/a.jpg",
                faces=[],
            )
        ],
        distance_threshold=0.6,
        min_samples=2,
    )

    assert event is not None
    assert calls == [
        (("asset-a",), "done"),
        (("asset-a",), "done"),
    ]


def test_people_index_coordinator_preserves_committed_snapshot_when_done_status_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()

    global_repo = get_global_repository(library_root)
    global_repo.write_rows(
        [
            {"rel": "album/shared.jpg", "id": "asset-shared", "media_type": 0, "face_status": "done"},
            {"rel": "album/new.jpg", "id": "asset-new", "media_type": 0, "face_status": "pending"},
        ]
    )

    service = PeopleService(library_root)
    repository = service.repository()
    assert repository is not None

    embedding = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    repository.replace_all(
        [
            _face_record_with_embedding(
                face_id="face-a-shared",
                face_key="face-key-a-shared",
                asset_id="asset-shared",
                asset_rel="album/shared.jpg",
                person_id="person-a",
                embedding=embedding,
            ),
        ],
        [
            _person_record_with_embedding(
                person_id="person-a",
                key_face_id="face-a-shared",
                face_count=1,
                name="Alice",
                embedding=embedding,
            )
        ],
    )

    original_update_face_statuses = global_repo.update_face_statuses
    call_count = {"value": 0}

    def fail_done_status_once(asset_ids: list[str], status: str) -> None:
        call_count["value"] += 1
        if status == "done":
            raise RuntimeError("done status locked")
        original_update_face_statuses(asset_ids, status)

    monkeypatch.setattr(global_repo, "update_face_statuses", fail_done_status_once)

    coordinator = get_people_index_coordinator(library_root)
    with pytest.raises(
        PeopleSnapshotCommittedError,
        match="Face scan committed, but updating scan bookkeeping failed.",
    ):
        coordinator.submit_detected_batch(
            [
                DetectedAssetFaces(
                    asset_id="asset-new",
                    asset_rel="album/new.jpg",
                    faces=[
                        _face_record_with_embedding(
                            face_id="face-a-new",
                            face_key="face-key-a-new",
                            asset_id="asset-new",
                            asset_rel="album/new.jpg",
                            person_id=None,
                            embedding=np.asarray([0.99, 0.01, 0.0], dtype=np.float32),
                        ),
                    ],
                )
            ],
            distance_threshold=0.6,
            min_samples=1,
        )

    assert sorted(face.asset_id for face in repository.get_all_faces()) == [
        "asset-new",
        "asset-shared",
    ]
    summaries = service.list_clusters()
    assert len(summaries) == 1
    assert summaries[0].face_count == 2
    query = service.build_cluster_query(summaries[0].person_id)
    assert query.asset_ids == ["asset-new", "asset-shared"]
    assert global_repo.get_rows_by_ids(["asset-new"])["asset-new"]["face_status"] == "pending"
    assert call_count["value"] == 3


def test_face_scan_worker_does_not_mark_retry_after_committed_snapshot_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_repo = get_global_repository(tmp_path)
    global_repo.write_rows(
        [
            {"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "pending"},
        ]
    )
    worker = FaceScanWorker(tmp_path)
    worker.finish_input()
    messages: list[str] = []
    worker.statusChanged.connect(messages.append)

    monkeypatch.setattr(
        "iPhoto.library.workers.face_scan_worker.face_library_paths",
        lambda _root: SimpleNamespace(
            model_dir=tmp_path / "models",
            thumbnail_dir=tmp_path / "thumbs",
        ),
    )

    class FakePipeline:
        distance_threshold = 0.6
        min_samples = 2

        def __init__(self, *, model_root: Path) -> None:
            self.model_root = model_root

        def detect_faces_for_rows(self, batch: list[dict], **_kwargs):
            return [
                DetectedAssetFaces(
                    asset_id=str(batch[0]["id"]),
                    asset_rel=str(batch[0]["rel"]),
                    faces=[],
                )
            ]

    monkeypatch.setattr(
        "iPhoto.library.workers.face_scan_worker.FaceClusterPipeline",
        FakePipeline,
    )
    monkeypatch.setattr(
        "iPhoto.library.workers.face_scan_worker.get_people_index_coordinator",
        lambda _root: Mock(
            submit_detected_batch=Mock(
                side_effect=PeopleSnapshotCommittedError(
                    "Face scan committed, but updating scan bookkeeping failed."
                )
            )
        ),
    )

    worker.run()

    assert global_repo.get_rows_by_ids(["asset-a"])["asset-a"]["face_status"] == "pending"
    assert messages == ["Face scan committed, but updating scan bookkeeping failed."]


def test_face_scan_worker_reports_unexpected_batch_error_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_repo = get_global_repository(tmp_path)
    global_repo.write_rows(
        [
            {"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "face_status": "pending"},
        ]
    )
    worker = FaceScanWorker(tmp_path)
    worker.finish_input()
    messages: list[str] = []
    worker.statusChanged.connect(messages.append)

    monkeypatch.setattr(
        "iPhoto.library.workers.face_scan_worker.face_library_paths",
        lambda _root: SimpleNamespace(
            model_dir=tmp_path / "models",
            thumbnail_dir=tmp_path / "thumbs",
        ),
    )

    class FakePipeline:
        distance_threshold = 0.6
        min_samples = 2

        def __init__(self, *, model_root: Path) -> None:
            self.model_root = model_root

        def detect_faces_for_rows(self, *_args, **_kwargs):
            raise ValueError("compiled dependency missing")

    monkeypatch.setattr(
        "iPhoto.library.workers.face_scan_worker.FaceClusterPipeline",
        FakePipeline,
    )

    worker.run()

    assert global_repo.get_rows_by_ids(["asset-a"])["asset-a"]["face_status"] == "retry"
    assert messages == ["Face scanning paused: compiled dependency missing"]


def test_scanner_worker_does_not_emit_chunk_ready_for_failed_persist(tmp_path: Path) -> None:
    class FailingStore:
        def merge_scan_rows(self, chunk: list[dict]) -> list[dict]:
            raise RuntimeError("db write failed")

    signals = ScannerSignals()
    emitted_chunks: list[tuple[Path, list[dict]]] = []
    failed_batches: list[tuple[Path, int]] = []
    signals.chunkReady.connect(lambda root, chunk: emitted_chunks.append((root, chunk)))
    signals.batchFailed.connect(lambda root, count: failed_batches.append((root, count)))

    worker = ScannerWorker(tmp_path, [], [], signals)
    worker._process_chunk(FailingStore(), [{"id": "asset-1", "rel": "album/a.jpg"}])

    assert emitted_chunks == []
    assert failed_batches == [(tmp_path, 1)]
    assert worker.failed_count == 1
