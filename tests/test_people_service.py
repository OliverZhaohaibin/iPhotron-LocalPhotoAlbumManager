from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.config import WORK_DIR_NAME
from iPhoto.people.repository import FaceRecord, PersonRecord
from iPhoto.people.service import PeopleService, face_library_paths, shared_face_model_dir


@pytest.fixture(autouse=True)
def _reset_global_repository() -> None:
    reset_global_repository()
    yield
    reset_global_repository()


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


def _person_record(
    *, person_id: str, key_face_id: str, face_count: int, name: str | None = None
) -> PersonRecord:
    embedding = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    timestamp = _now_iso()
    return PersonRecord(
        person_id=person_id,
        name=name,
        key_face_id=key_face_id,
        face_count=face_count,
        center_embedding=embedding,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_face_library_paths_live_under_dot_iphoto(tmp_path: Path) -> None:
    paths = face_library_paths(tmp_path)

    assert paths.root_dir == tmp_path / WORK_DIR_NAME / "faces"
    assert paths.index_db_path == paths.root_dir / "face_index.db"
    assert paths.state_db_path == paths.root_dir / "face_state.db"
    assert paths.thumbnail_dir == paths.root_dir / "thumbnails"
    assert paths.model_dir == shared_face_model_dir()
    assert paths.model_dir == Path(__file__).resolve().parents[1] / "src" / "extension" / "models"


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
