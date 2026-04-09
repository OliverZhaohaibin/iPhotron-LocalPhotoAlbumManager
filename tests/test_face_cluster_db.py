from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest


_DEMO_FACE_CLUSTER = Path(__file__).resolve().parent.parent / "demo" / "face-cluster"


def test_repository_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(_DEMO_FACE_CLUSTER))
    import local_imports  # noqa: PLC0415
    db = local_imports.import_sibling("db")

    FaceClusterRepository = db.FaceClusterRepository
    FaceRecord = db.FaceRecord
    PersonRecord = db.PersonRecord

    db_path = tmp_path / "face_index.db"
    repository = FaceClusterRepository(db_path)

    embedding = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    person = PersonRecord(
        person_id="person-1",
        key_face_id="face-1",
        face_count=1,
        center_embedding=embedding,
        created_at="2026-04-09T10:00:00+00:00",
        updated_at="2026-04-09T10:00:00+00:00",
    )
    face = FaceRecord(
        face_id="face-1",
        asset_rel="a/b/c.jpg",
        box_x=10,
        box_y=20,
        box_w=30,
        box_h=40,
        confidence=0.98,
        embedding=embedding,
        embedding_dim=3,
        thumbnail_path="thumbnails/face-1.png",
        person_id="person-1",
        detected_at="2026-04-09T10:00:00+00:00",
        image_width=400,
        image_height=300,
    )

    repository.replace_all([face], [person])

    summaries = repository.get_person_summaries()
    assert len(summaries) == 1
    assert summaries[0].person_id == "person-1"
    assert summaries[0].face_count == 1
    assert summaries[0].thumbnail_path == (db_path.parent / "thumbnails/face-1.png").resolve()

    rows = repository.get_faces_by_person("person-1")
    assert len(rows) == 1
    assert rows[0]["face_id"] == "face-1"
    assert rows[0]["asset_rel"] == "a/b/c.jpg"
    assert rows[0]["embedding_dim"] == 3
    assert rows[0]["embedding_bytes"] == len(embedding.tobytes())
    assert rows[0]["embedding"] == "<float32[3]>"
