from __future__ import annotations

import os
import sys
from types import ModuleType

import numpy as np


_DEMO_FACE_CLUSTER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "demo", "face-cluster")
)
if _DEMO_FACE_CLUSTER not in sys.path:
    sys.path.insert(0, _DEMO_FACE_CLUSTER)

from db import FaceRecord
from pipeline import (
    _patch_insightface_alignment_estimate,
    cluster_face_records,
    run_dbscan,
)


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


def test_patch_insightface_alignment_estimate_uses_from_estimate(monkeypatch) -> None:
    class FakeTransform:
        def __init__(self, params):
            self.params = params

    class FakeSimilarityTransform:
        @classmethod
        def from_estimate(cls, src, dst):
            params = np.array(
                [
                    [1.0, 0.0, float(src[0, 0] + dst[0, 0])],
                    [0.0, 1.0, float(src[0, 1] + dst[0, 1])],
                    [0.0, 0.0, 1.0],
                ],
                dtype=np.float32,
            )
            return FakeTransform(params)

    face_align_module = ModuleType("insightface.utils.face_align")
    face_align_module.arcface_dst = np.array(
        [
            [38.2946, 51.6963],
            [73.5318, 51.5014],
            [56.0252, 71.7366],
            [41.5493, 92.3655],
            [70.7299, 92.2041],
        ],
        dtype=np.float32,
    )
    utils_module = ModuleType("insightface.utils")
    utils_module.face_align = face_align_module
    insightface_module = ModuleType("insightface")
    insightface_module.utils = utils_module

    transform_module = ModuleType("skimage.transform")
    transform_module.SimilarityTransform = FakeSimilarityTransform
    skimage_module = ModuleType("skimage")
    skimage_module.transform = transform_module

    monkeypatch.setitem(sys.modules, "insightface", insightface_module)
    monkeypatch.setitem(sys.modules, "insightface.utils", utils_module)
    monkeypatch.setitem(sys.modules, "insightface.utils.face_align", face_align_module)
    monkeypatch.setitem(sys.modules, "skimage", skimage_module)
    monkeypatch.setitem(sys.modules, "skimage.transform", transform_module)

    _patch_insightface_alignment_estimate()

    landmarks = np.array(
        [
            [1.0, 2.0],
            [3.0, 4.0],
            [5.0, 6.0],
            [7.0, 8.0],
            [9.0, 10.0],
        ],
        dtype=np.float32,
    )
    matrix = face_align_module.estimate_norm(landmarks, image_size=112)
    assert matrix.shape == (2, 3)
    assert getattr(face_align_module, "_iphoto_from_estimate_patch", False) is True
