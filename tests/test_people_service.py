from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.config import WORK_DIR_NAME
from iPhoto.people.repository import FaceRecord, PersonRecord
from iPhoto.people.service import PeopleService, face_library_paths


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


def _person_record(*, person_id: str, key_face_id: str, face_count: int, name: str | None = None) -> PersonRecord:
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
    assert paths.model_dir == paths.root_dir / "models"


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

    face_a = _face_record(face_id="face-a", asset_id="asset-a", asset_rel="album/a.jpg", person_id="person-a")
    face_b = _face_record(face_id="face-b", asset_id="asset-b", asset_rel="album/b.jpg", person_id="person-b")
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
