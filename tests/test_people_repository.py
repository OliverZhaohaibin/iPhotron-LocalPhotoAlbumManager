from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from iPhoto.people.repository import FaceRecord, FaceRepository, PersonRecord


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


def _person_record(*, person_id: str, key_face_id: str, face_count: int) -> PersonRecord:
    embedding = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    timestamp = _now_iso()
    return PersonRecord(
        person_id=person_id,
        name="Alice",
        key_face_id=key_face_id,
        face_count=face_count,
        center_embedding=embedding,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_remove_faces_for_assets_deletes_affected_person_rows_first(tmp_path: Path) -> None:
    repository = FaceRepository(tmp_path / "face_index.db")
    face_a = _face_record(
        face_id="face-a",
        asset_id="asset-a",
        asset_rel="album/a.jpg",
        person_id="person-a",
    )
    face_b = _face_record(
        face_id="face-b",
        asset_id="asset-b",
        asset_rel="album/b.jpg",
        person_id="person-a",
    )
    person = _person_record(person_id="person-a", key_face_id="face-a", face_count=2)
    repository.replace_all([face_a, face_b], [person])

    repository.remove_faces_for_assets(["asset-a"], ["album/a.jpg"])

    remaining_faces = repository.get_all_faces()
    assert [face.face_id for face in remaining_faces] == ["face-b"]
    assert repository.get_person_summaries() == []
