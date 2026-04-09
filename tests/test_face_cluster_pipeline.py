from __future__ import annotations

import os
import sys

import numpy as np


_DEMO_FACE_CLUSTER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "demo", "face-cluster")
)
if _DEMO_FACE_CLUSTER not in sys.path:
    sys.path.insert(0, _DEMO_FACE_CLUSTER)

from db import FaceRecord
from pipeline import cluster_face_records, run_dbscan


def _face(face_id: str, embedding: np.ndarray, confidence: float) -> FaceRecord:
    return FaceRecord(
        face_id=face_id,
        asset_rel=f"{face_id}.jpg",
        box_x=0,
        box_y=0,
        box_w=100,
        box_h=100,
        confidence=confidence,
        embedding=embedding.astype(np.float32),
        embedding_dim=int(embedding.shape[0]),
        thumbnail_path=f"thumbnails/{face_id}.png",
        person_id=None,
        detected_at="2026-04-09T10:00:00+00:00",
        image_width=200,
        image_height=200,
    )


def test_run_dbscan_finds_two_clusters() -> None:
    embeddings = np.array(
        [
            [1.0, 0.0],
            [0.99, 0.01],
            [0.0, 1.0],
            [0.01, 0.99],
        ],
        dtype=np.float32,
    )
    labels = run_dbscan(embeddings, eps=0.1, min_samples=2)
    assert set(labels.tolist()) == {0, 1}


def test_noise_points_become_singleton_clusters() -> None:
    faces = [
        _face("f1", np.array([1.0, 0.0], dtype=np.float32), 0.95),
        _face("f2", np.array([0.0, 1.0], dtype=np.float32), 0.90),
        _face("f3", np.array([-1.0, 0.0], dtype=np.float32), 0.85),
    ]
    updated_faces, persons = cluster_face_records(
        faces,
        distance_threshold=0.2,
        min_samples=2,
    )
    assert len(persons) == 3
    assert len({face.person_id for face in updated_faces}) == 3


def test_cluster_face_records_selects_highest_confidence_key_face() -> None:
    faces = [
        _face("f1", np.array([1.0, 0.0], dtype=np.float32), 0.70),
        _face("f2", np.array([0.99, 0.01], dtype=np.float32), 0.98),
    ]
    updated_faces, persons = cluster_face_records(
        faces,
        distance_threshold=0.1,
        min_samples=2,
    )
    assert len(persons) == 1
    assert persons[0].key_face_id == "f2"
    assert all(face.person_id == persons[0].person_id for face in updated_faces)
