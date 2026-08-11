from __future__ import annotations

import hashlib
import io
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from iPhoto.bootstrap.library_pet_service import create_pet_service
from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.library.workers.pet_scan_worker import PetScanWorker
from iPhoto.people.face_repository import FaceRepository
from iPhoto.people.records import FaceRecord, ManualFaceRecord, PersonRecord
from iPhoto.people.state_repository import FaceStateRepository
from iPhoto.pets import pipeline as pet_pipeline
from iPhoto.pets.errors import PetModelUnavailableError, PetRuntimeUnavailableError
from iPhoto.pets.index_coordinator import PetIndexCoordinator, reset_pet_index_coordinators
from iPhoto.pets.pipeline import (
    _DINO_SOURCE_REVISION,
    DEFAULT_PET_DETECTOR_MODEL_SHA256,
    DEFAULT_PET_DETECTOR_MODEL_URL,
    PET_CLUSTERING_PIPELINE_VERSION,
    PET_DETECTOR_PIPELINE_VERSION,
    PET_MODEL_MANIFEST,
    DetectedAssetPets,
    PetClusterPipeline,
    _decode_yolox_predictions,
    _dedupe_supported_species_boxes,
    _DetectedPetBox,
    _DinoV2Embedder,
    _map_yolox_box_to_source,
    _pet_box_overlaps_people_boxes,
    _preprocess_yolox,
    _YoloxOnnxPetDetector,
    build_pet_key,
    canonicalize_pet_identities,
    cluster_pet_records,
    default_pet_model_dir,
    ensure_pet_detector_model,
)
from iPhoto.pets.records import PetDetectionRecord, PetRecord
from iPhoto.pets.repository import PetRepository
from iPhoto.pets.repository_utils import normalize_vector, utc_now_iso
from iPhoto.pets.scan_session import PetScanSession
from iPhoto.pets.state_repository import PetStateRepository


class _FakePetAssetRepository:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = [dict(row) for row in rows]
        self.update_calls: list[tuple[tuple[str, ...], str]] = []

    def get_rows_by_ids(self, asset_ids):
        wanted = {str(asset_id) for asset_id in asset_ids}
        return {str(row["id"]): dict(row) for row in self.rows if str(row.get("id")) in wanted}

    def read_rows_by_pet_status(self, statuses, *, limit=None):
        wanted = {str(status) for status in statuses}
        count = 0
        for row in self.rows:
            if row.get("pet_status") not in wanted:
                continue
            yield dict(row)
            count += 1
            if limit is not None and count >= int(limit):
                return

    def update_pet_status(self, asset_id: str, status: str) -> None:
        self.update_pet_statuses([asset_id], status)

    def update_pet_statuses(self, asset_ids, status: str) -> None:
        ids = tuple(str(asset_id) for asset_id in asset_ids if asset_id)
        self.update_calls.append((ids, status))
        wanted = set(ids)
        for row in self.rows:
            if str(row.get("id")) in wanted:
                row["pet_status"] = status

    def count_by_pet_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.rows:
            status = str(row.get("pet_status") or "")
            counts[status] = counts.get(status, 0) + 1
        return counts

    def reset_pet_statuses_for_pipeline_upgrade(self) -> int:
        eligible = [
            str(row["id"])
            for row in self.rows
            if row.get("pet_status") == "done" and int(row.get("media_type") or 0) == 0
        ]
        self.update_pet_statuses(eligible, "pending")
        return len(eligible)


@pytest.fixture(autouse=True)
def clean_state() -> None:
    reset_global_repository()
    reset_pet_index_coordinators()
    yield
    reset_pet_index_coordinators()
    reset_global_repository()


def _detection(
    *,
    detection_id: str,
    asset_id: str = "asset-a",
    pet_key: str | None = None,
    pet_id: str | None = None,
    embedding: np.ndarray | None = None,
    species_label: str | None = None,
    thumbnail_path: str | None = None,
) -> PetDetectionRecord:
    vector = normalize_vector(
        embedding if embedding is not None else np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    )
    return PetDetectionRecord(
        detection_id=detection_id,
        pet_key=pet_key or f"key-{detection_id}",
        asset_id=asset_id,
        asset_rel=f"album/{asset_id}.jpg",
        box_x=10,
        box_y=20,
        box_w=80,
        box_h=90,
        confidence=0.9,
        embedding=vector,
        embedding_dim=int(vector.shape[0]),
        embedding_model="dinov2_vits14",
        detector_model="yolox_nano_coco",
        thumbnail_path=thumbnail_path,
        pet_id=pet_id,
        detected_at=utc_now_iso(),
        image_width=400,
        image_height=300,
        species_label=species_label,
    )


def test_build_pet_key_is_stable_for_small_bbox_jitter() -> None:
    first = build_pet_key(
        asset_id="asset-a",
        bbox=(100, 80, 120, 110),
        image_width=1000,
        image_height=800,
    )
    jittered = build_pet_key(
        asset_id="asset-a",
        bbox=(102, 82, 119, 111),
        image_width=1000,
        image_height=800,
    )
    different_box = build_pet_key(
        asset_id="asset-a",
        bbox=(200, 180, 119, 111),
        image_width=1000,
        image_height=800,
    )

    assert jittered == first
    assert different_box != first


def test_cluster_pet_records_splits_known_detector_species() -> None:
    detections = [
        _detection(
            detection_id="cat-a",
            embedding=np.asarray([1.0, 0.0]),
            species_label="cat",
        ),
        _detection(
            detection_id="cat-b",
            embedding=np.asarray([0.99, 0.01]),
            species_label="cat",
        ),
        _detection(
            detection_id="dog-a",
            embedding=np.asarray([1.0, 0.0]),
            species_label="dog",
        ),
        _detection(
            detection_id="dog-b",
            embedding=np.asarray([0.99, 0.01]),
            species_label="dog",
        ),
    ]

    clustered, pets = cluster_pet_records(
        detections,
        distance_threshold=0.2,
    )

    assert len(pets) == 2
    assert clustered[0].pet_id == clustered[1].pet_id
    assert clustered[2].pet_id == clustered[3].pet_id
    assert clustered[0].pet_id != clustered[2].pet_id
    assert {pet.species_label for pet in pets} == {"cat", "dog"}


def test_cluster_pet_records_clusters_same_unknown_species() -> None:
    detections = [
        _detection(detection_id="old-a", embedding=np.asarray([1.0, 0.0])),
        _detection(detection_id="old-b", embedding=np.asarray([0.99, 0.01])),
    ]

    clustered, pets = cluster_pet_records(
        detections,
        distance_threshold=0.2,
    )

    assert len(pets) == 1
    assert {item.pet_id for item in clustered} == {pets[0].pet_id}


def test_build_pet_records_rejects_mixed_known_species_for_one_identity() -> None:
    cat = _detection(
        detection_id="cat",
        pet_id="pet-a",
        species_label="cat",
    )
    dog = _detection(
        detection_id="dog",
        asset_id="asset-b",
        pet_id="pet-a",
        species_label="dog",
    )

    with pytest.raises(ValueError, match="mixes incompatible species labels"):
        pet_pipeline.build_pet_records_from_detections([cat, dog])


def test_cluster_pet_records_default_clusters_small_similar_pet_samples() -> None:
    detections = [
        _detection(detection_id="cat-a", embedding=np.asarray([1.0, 0.0])),
        _detection(detection_id="cat-b", embedding=np.asarray([0.99, 0.01])),
    ]

    clustered, pets = cluster_pet_records(
        detections,
        distance_threshold=0.2,
    )

    assert len(pets) == 1
    assert {item.pet_id for item in clustered} == {pets[0].pet_id}


def test_cluster_pet_records_ignores_hdbscan_for_default_identity_grouping(
    monkeypatch,
) -> None:
    class _FakeHdbscan:
        def __init__(self, **_kwargs) -> None:
            pass

        def fit_predict(self, _distance: np.ndarray) -> np.ndarray:
            raise AssertionError("cluster_pet_records should not use HDBSCAN")

    monkeypatch.setitem(
        sys.modules,
        "hdbscan",
        SimpleNamespace(HDBSCAN=_FakeHdbscan),
    )
    detections = [
        _detection(
            detection_id="cat-a",
            asset_id="asset-a",
            embedding=np.asarray([1.0, 0.0]),
            species_label="cat",
        ),
        _detection(
            detection_id="cat-b",
            asset_id="asset-b",
            embedding=np.asarray([0.99, 0.01]),
            species_label="cat",
        ),
        _detection(
            detection_id="cat-c",
            asset_id="asset-c",
            embedding=np.asarray([0.0, 1.0]),
            species_label="cat",
        ),
    ]

    clustered, pets = cluster_pet_records(
        detections,
        distance_threshold=0.2,
    )

    assert len(pets) == 2
    assert clustered[0].pet_id == clustered[1].pet_id
    assert clustered[2].pet_id != clustered[0].pet_id


def test_cluster_pet_records_complete_link_blocks_similarity_chain() -> None:
    sixty_degrees = np.asarray([0.5, 0.8660254])
    thirty_degrees = np.asarray([0.8660254, 0.5])
    detections = [
        _detection(
            detection_id="cat-a",
            asset_id="asset-a",
            embedding=np.asarray([1.0, 0.0]),
            species_label="cat",
        ),
        _detection(
            detection_id="cat-b",
            asset_id="asset-b",
            embedding=thirty_degrees,
            species_label="cat",
        ),
        _detection(
            detection_id="cat-c",
            asset_id="asset-c",
            embedding=sixty_degrees,
            species_label="cat",
        ),
    ]

    clustered, pets = cluster_pet_records(
        detections,
        distance_threshold=0.2,
    )

    assert len(pets) == 2
    assert clustered[0].pet_id == clustered[1].pet_id
    assert clustered[2].pet_id != clustered[0].pet_id


def test_decode_yolox_raw_grid_output_expands_pet_box() -> None:
    predictions = np.zeros((3549, 85), dtype=np.float32)
    predictions[0, 0] = 1.0
    predictions[0, 1] = 2.0
    predictions[0, 2] = np.log(20.0)
    predictions[0, 3] = np.log(18.0)
    predictions[0, 4] = 0.9
    predictions[0, 5 + 16] = 0.8

    decoded = _decode_yolox_predictions(predictions, input_size=(416, 416))

    x0, y0, x1, y1, confidence, class_id = decoded[0]
    assert class_id == 16
    assert confidence == pytest.approx(0.72)
    assert x1 - x0 == pytest.approx(160.0)
    assert y1 - y0 == pytest.approx(144.0)


def test_preprocess_yolox_letterboxes_without_distorting_aspect_ratio() -> None:
    image = Image.new("RGB", (400, 200), color=(10, 20, 30))

    preprocessed = _preprocess_yolox(image, input_width=416, input_height=416)

    assert preprocessed.tensor.shape == (1, 3, 416, 416)
    assert preprocessed.resize_ratio == pytest.approx(1.04)
    assert preprocessed.tensor[0, 0, 220, 10] == pytest.approx(114.0)

    mapped = _map_yolox_box_to_source(
        (104.0, 52.0, 208.0, 156.0),
        preprocessed=preprocessed,
        image_width=400,
        image_height=200,
        offset=(20, 30),
    )

    assert mapped == (120, 80, 100, 100)


def test_yolox_detector_scans_tiles_when_full_image_has_no_supported_pet() -> None:
    detector = object.__new__(_YoloxOnnxPetDetector)
    detector._enable_tiled_detection = True
    detector._tile_scan_min_confidence = 0.30
    detector._tile_species = frozenset({"cat", "dog"})
    calls: list[tuple[tuple[int, int], tuple[int, int]]] = []

    def fake_detect_single_image(image, *, offset=(0, 0)):
        calls.append((image.size, offset))
        if offset == (0, 0):
            return [_DetectedPetBox((5, 5, 80, 80), 0.95, "sheep")]
        return [_DetectedPetBox((100, 100, 80, 80), 0.60, "dog")]

    detector._detect_single_image = fake_detect_single_image

    boxes = detector.detect(Image.new("RGB", (400, 300)))

    assert len(calls) == 5
    assert [box.species_label for box in boxes] == ["sheep", "dog"]
    assert [box.confidence for box in boxes] == [pytest.approx(0.95), pytest.approx(0.60)]


def test_yolox_detector_scans_uncovered_tiles_when_full_image_has_large_dog() -> None:
    detector = object.__new__(_YoloxOnnxPetDetector)
    detector._enable_tiled_detection = True
    detector._tile_scan_min_confidence = 0.30
    detector._tile_species = frozenset({"cat", "dog"})
    calls = 0

    def fake_detect_single_image(image, *, offset=(0, 0)):
        nonlocal calls
        calls += 1
        if image.size == (400, 300):
            return [_DetectedPetBox((0, 0, 250, 300), 0.90, "dog")]
        if offset[0] > 0:
            return [_DetectedPetBox((offset[0] + 180, 120, 40, 50), 0.60, "cat")]
        return []

    detector._detect_single_image = fake_detect_single_image

    boxes = detector.detect(Image.new("RGB", (400, 300)))

    assert calls == 5
    assert {box.species_label for box in boxes} == {"cat", "dog"}


def test_dedupe_supported_species_boxes_keeps_best_overlapping_pet_label() -> None:
    boxes = [
        _DetectedPetBox((10, 10, 100, 100), 0.61, "cat"),
        _DetectedPetBox((12, 12, 98, 98), 0.72, "dog"),
        _DetectedPetBox((250, 10, 100, 100), 0.55, "cat"),
    ]

    deduped = _dedupe_supported_species_boxes(boxes)

    assert [(box.species_label, box.confidence) for box in deduped] == [
        ("dog", 0.72),
        ("cat", 0.61),
        ("cat", 0.55),
    ]


def test_dedupe_supported_species_boxes_keeps_two_real_cats_from_img_6518() -> None:
    boxes = [
        _DetectedPetBox((401, 1727, 3883, 3090), 0.674, "cat"),
        _DetectedPetBox((0, 1704, 3260, 2621), 0.425, "cat"),
        _DetectedPetBox((0, 1133, 1449, 1506), 0.318, "cat"),
    ]

    deduped = _dedupe_supported_species_boxes(boxes)

    assert [box.bbox for box in deduped] == [
        (401, 1727, 3883, 3090),
        (0, 1133, 1449, 1506),
    ]


def test_dedupe_supported_species_boxes_suppresses_centered_nested_box() -> None:
    boxes = [
        _DetectedPetBox((0, 0, 200, 200), 0.90, "dog"),
        _DetectedPetBox((70, 70, 60, 60), 0.80, "dog"),
    ]

    assert _dedupe_supported_species_boxes(boxes) == [boxes[0]]


def test_dedupe_supported_species_boxes_keeps_far_center_nested_pet() -> None:
    boxes = [
        _DetectedPetBox((0, 0, 300, 300), 0.90, "cat"),
        _DetectedPetBox((220, 20, 60, 60), 0.80, "cat"),
    ]

    assert _dedupe_supported_species_boxes(boxes) == boxes


def test_dedupe_supported_species_boxes_uses_standard_same_species_iou() -> None:
    boxes = [
        _DetectedPetBox((0, 0, 100, 100), 0.90, "cat"),
        _DetectedPetBox((20, 0, 100, 100), 0.80, "cat"),
    ]

    assert _dedupe_supported_species_boxes(boxes) == [boxes[0]]


def test_people_priority_overlap_matches_iou_and_smaller_box_coverage() -> None:
    people_box = (732, 668, 2089, 2930)

    assert _pet_box_overlaps_people_boxes(
        (29, 122, 2675, 3882),
        [people_box],
    )
    assert _pet_box_overlaps_people_boxes(
        (628, 945, 2908, 4175),
        [people_box],
    )
    assert _pet_box_overlaps_people_boxes(
        (100, 100, 100, 100),
        [(110, 110, 100, 100)],
    )
    assert _pet_box_overlaps_people_boxes(
        (0, 0, 200, 200),
        [(-10, 0, 100, 100)],
    )
    assert not _pet_box_overlaps_people_boxes(
        (0, 0, 200, 200),
        [(-11, 0, 100, 100)],
    )
    assert not _pet_box_overlaps_people_boxes(
        (0, 0, 100, 100),
        [(60, 60, 100, 100)],
    )
    assert not _pet_box_overlaps_people_boxes(
        (0, 0, 100, 100),
        [(200, 200, 100, 100)],
    )


def test_people_overlap_keeps_held_pet_but_suppresses_mural_false_positive() -> None:
    # A normal pet-body candidate can contain a smaller human face without
    # being the same object.  Its bounded image coverage keeps it eligible.
    assert not _pet_box_overlaps_people_boxes(
        (400, 500, 1200, 1400),
        [(760, 620, 300, 340)],
        image_dimensions=(3000, 2400),
    )

    # Real regression captured from DSCF6999.JPG.  YOLOX labels the wall mural
    # as a giant dog while InsightFace finds the painted face inside it.
    assert _pet_box_overlaps_people_boxes(
        (59, 134, 4024, 5216),
        [(732, 668, 2089, 2930)],
        image_dimensions=(4160, 6240),
    )


def test_pet_pipeline_filters_detector_boxes_and_records_metrics(tmp_path: Path) -> None:
    image_dir = tmp_path / "album"
    image_dir.mkdir()
    image_path = image_dir / "a.jpg"
    Image.new("RGB", (400, 300), color=(128, 96, 64)).save(image_path)
    pipeline = PetClusterPipeline(
        model_root=tmp_path / "models",
        allow_model_download=False,
        min_pet_size=48,
    )
    pipeline._detector = SimpleNamespace(
        detect=lambda _image: [
            SimpleNamespace(bbox=(10, 20, 160, 120), confidence=0.91, species_label="dog"),
            SimpleNamespace(bbox=(20, 30, 160, 120), confidence=0.92, species_label="horse"),
            SimpleNamespace(bbox=(30, 40, 20, 20), confidence=0.93, species_label="cat"),
        ]
    )
    pipeline._embedder = SimpleNamespace(
        embed=lambda _image: normalize_vector(np.asarray([1.0, 0.0, 0.0], dtype=np.float32))
    )

    results = pipeline.detect_pets_for_rows(
        [{"id": "asset-a", "rel": "album/a.jpg"}],
        library_root=tmp_path,
        thumbnail_dir=tmp_path / ".iPhoto" / "pets" / "thumbnails",
    )

    assert len(results) == 1
    assert results[0].error is None
    assert len(results[0].detections) == 1
    assert results[0].detections[0].box_w == 160
    assert results[0].detections[0].species_label == "dog"
    assert pipeline.last_scan_metrics.candidate_boxes == 3
    assert pipeline.last_scan_metrics.unsupported_species == 1
    assert pipeline.last_scan_metrics.too_small == 1
    assert pipeline.last_scan_metrics.accepted_detections == 1


def test_pet_pipeline_dedupes_overlapping_supported_species_after_filtering(
    tmp_path: Path,
) -> None:
    image_dir = tmp_path / "album"
    image_dir.mkdir()
    image_path = image_dir / "a.jpg"
    Image.new("RGB", (400, 300), color=(128, 96, 64)).save(image_path)
    pipeline = PetClusterPipeline(
        model_root=tmp_path / "models",
        allow_model_download=False,
        min_pet_size=48,
    )
    pipeline._detector = SimpleNamespace(
        detect=lambda _image: [
            SimpleNamespace(bbox=(10, 20, 160, 120), confidence=0.88, species_label="sheep"),
            SimpleNamespace(bbox=(10, 20, 160, 120), confidence=0.70, species_label="cat"),
            SimpleNamespace(bbox=(12, 22, 158, 118), confidence=0.82, species_label="dog"),
            SimpleNamespace(bbox=(260, 20, 90, 90), confidence=0.76, species_label="cat"),
        ]
    )
    pipeline._embedder = SimpleNamespace(
        embed=lambda _image: normalize_vector(np.asarray([1.0, 0.0, 0.0], dtype=np.float32))
    )

    results = pipeline.detect_pets_for_rows(
        [{"id": "asset-a", "rel": "album/a.jpg"}],
        library_root=tmp_path,
        thumbnail_dir=tmp_path / ".iPhoto" / "pets" / "thumbnails",
    )

    assert len(results) == 1
    assert [round(detection.confidence, 2) for detection in results[0].detections] == [
        0.82,
        0.76,
        0.70,
    ]
    assert pipeline.last_scan_metrics.candidate_boxes == 4
    assert pipeline.last_scan_metrics.unsupported_species == 1
    assert pipeline.last_scan_metrics.accepted_detections == 3


def test_pet_pipeline_filters_people_overlaps_before_embedding(tmp_path: Path) -> None:
    image_dir = tmp_path / "album"
    image_dir.mkdir()
    Image.new("RGB", (400, 300), color=(128, 96, 64)).save(image_dir / "a.jpg")
    pipeline = PetClusterPipeline(
        model_root=tmp_path / "models",
        allow_model_download=False,
        min_pet_size=48,
    )
    pipeline._detector = SimpleNamespace(
        detect=lambda _image: [
            SimpleNamespace(bbox=(10, 20, 160, 120), confidence=0.88, species_label="dog"),
            SimpleNamespace(bbox=(260, 20, 90, 90), confidence=0.76, species_label="cat"),
        ]
    )
    embedded_sizes: list[tuple[int, int]] = []

    def embed(image):
        embedded_sizes.append(image.size)
        return normalize_vector(np.asarray([1.0, 0.0, 0.0], dtype=np.float32))

    pipeline._embedder = SimpleNamespace(embed=embed)

    results = pipeline.detect_pets_for_rows(
        [{"id": "asset-a", "rel": "album/a.jpg"}],
        library_root=tmp_path,
        thumbnail_dir=tmp_path / ".iPhoto" / "pets" / "thumbnails",
        people_boxes_by_asset_id={"asset-a": [(20, 25, 140, 110)]},
    )

    assert len(results[0].detections) == 1
    assert results[0].detections[0].species_label == "cat"
    assert len(embedded_sizes) == 1
    assert pipeline.last_scan_metrics.people_overlaps == 1
    assert pipeline.last_scan_metrics.accepted_detections == 1


def test_pet_pipeline_dedupes_dscf6997_mural_before_people_filtering(
    tmp_path: Path,
) -> None:
    image_dir = tmp_path / "album"
    image_dir.mkdir()
    Image.new("RGB", (416, 624), color=(128, 96, 64)).save(image_dir / "mural.jpg")
    pipeline = PetClusterPipeline(
        model_root=tmp_path / "models",
        allow_model_download=False,
        min_pet_size=40,
    )
    pipeline._detector = SimpleNamespace(
        detect=lambda _image: [
            SimpleNamespace(bbox=(0, 6, 409, 424), confidence=0.767, species_label="dog"),
            SimpleNamespace(bbox=(1, 15, 270, 380), confidence=0.658, species_label="dog"),
        ]
    )
    pipeline._embedder = SimpleNamespace(
        embed=lambda _image: pytest.fail("People filtering must run before embedding")
    )

    results = pipeline.detect_pets_for_rows(
        [{"id": "asset-mural", "rel": "album/mural.jpg"}],
        library_root=tmp_path,
        thumbnail_dir=tmp_path / ".iPhoto" / "pets" / "thumbnails",
        people_boxes_by_asset_id={
            "asset-mural": [
                (246, 378, 27, 28),
                (59, 65, 210, 288),
            ]
        },
    )

    assert len(results) == 1
    assert results[0].detections == []
    assert pipeline.last_scan_metrics.candidate_boxes == 2
    assert pipeline.last_scan_metrics.people_overlaps == 1
    assert pipeline.last_scan_metrics.accepted_detections == 0


def test_canonicalize_pet_identities_prefers_pet_key_vote(tmp_path: Path) -> None:
    state = PetStateRepository(tmp_path / "pet_state.db")
    existing = _detection(
        detection_id="existing",
        pet_key="stable-key",
        pet_id="pet-stable",
        embedding=np.asarray([1.0, 0.0]),
    )
    existing_pet = PetRecord(
        pet_id="pet-stable",
        name="Miso",
        key_detection_id="existing",
        detection_count=2,
        center_embedding=existing.embedding,
        embedding_dim=existing.embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=2,
        profile_state="stable",
    )
    state.sync_scan_results([existing_pet], [existing])

    current = _detection(
        detection_id="current",
        pet_key="stable-key",
        pet_id="temporary",
        embedding=np.asarray([0.0, 1.0]),
    )
    current_pet = PetRecord(
        pet_id="temporary",
        name=None,
        key_detection_id="current",
        detection_count=1,
        center_embedding=current.embedding,
        embedding_dim=current.embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=1,
    )

    detections, pets = canonicalize_pet_identities(
        [current],
        [current_pet],
        state,
        distance_threshold=0.01,
    )

    assert detections[0].pet_id == "pet-stable"
    assert pets[0].pet_id == "pet-stable"
    assert pets[0].name == "Miso"


def test_pet_repository_state_persists_name_hidden_and_rejected_key(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    detection = _detection(detection_id="det-a", pet_id="pet-a", pet_key="pet-key-a")
    pet = PetRecord(
        pet_id="pet-a",
        name=None,
        key_detection_id="det-a",
        detection_count=1,
        center_embedding=detection.embedding,
        embedding_dim=detection.embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=1,
    )
    repository.replace_all([detection], [pet])

    assert repository.rename_pet("pet-a", "Miso")
    assert repository.set_pet_hidden("pet-a", True)
    result = repository.delete_detection("det-a")
    assert result is not None

    repository.replace_all([detection], [pet])
    summaries = repository.get_pet_summaries(include_hidden=True)

    assert summaries == []
    assert repository.state_repository is not None
    assert repository.state_repository.get_rejected_pet_keys(["pet-key-a"]) == {"pet-key-a"}


def test_incremental_state_inputs_share_one_sqlite_read_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = PetStateRepository(tmp_path / "pet_state.db")
    detection = _detection(
        detection_id="snapshot",
        pet_key="snapshot-key",
        pet_id="pet-snapshot",
    )
    pet = PetRecord(
        pet_id="pet-snapshot",
        name="Before",
        key_detection_id=detection.detection_id,
        detection_count=1,
        center_embedding=detection.embedding,
        embedding_dim=detection.embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=1,
        profile_state="unstable",
    )
    state.sync_scan_results([pet], [detection])
    writer = PetStateRepository(state.db_path)
    writer.initialize()
    original_connect = state._connect
    mutation_injected = False

    class _CursorAfterFirstRead:
        def __init__(self, cursor):
            self._cursor = cursor

        def fetchall(self):
            nonlocal mutation_injected
            rows = self._cursor.fetchall()
            if not mutation_injected:
                mutation_injected = True
                writer.rename_pet("pet-snapshot", "After")
            return rows

        def __getattr__(self, name):
            return getattr(self._cursor, name)

    class _ConnectionAfterFirstRead:
        def __init__(self, connection):
            self._connection = connection

        def execute(self, sql, parameters=()):
            cursor = self._connection.execute(sql, parameters)
            if "SELECT pet_key FROM rejected_pet_keys" in str(sql):
                return _CursorAfterFirstRead(cursor)
            return cursor

        def __getattr__(self, name):
            return getattr(self._connection, name)

    monkeypatch.setattr(
        state,
        "_connect",
        lambda: _ConnectionAfterFirstRead(original_connect()),
    )

    snapshot = state._load_incremental_state(["snapshot-key"])

    assert mutation_injected
    assert snapshot.key_map == {"snapshot-key": "pet-snapshot"}
    assert snapshot.durable_profiles["pet-snapshot"].name == "Before"
    assert writer.get_profiles_by_ids(["pet-snapshot"])["pet-snapshot"].name == "After"


def test_delete_detection_replaces_custom_cover_and_removes_thumbnail(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    thumbnail_dir = tmp_path / "thumbnails"
    thumbnail_dir.mkdir()
    deleted_thumbnail = thumbnail_dir / "det-a.png"
    retained_thumbnail = thumbnail_dir / "det-b.png"
    deleted_thumbnail.write_bytes(b"deleted")
    retained_thumbnail.write_bytes(b"retained")
    first = _detection(
        detection_id="det-a",
        asset_id="asset-a",
        pet_id="pet-a",
        thumbnail_path="thumbnails/det-a.png",
    )
    second = _detection(
        detection_id="det-b",
        asset_id="asset-b",
        pet_id="pet-a",
        thumbnail_path="thumbnails/det-b.png",
    )
    pet = PetRecord(
        pet_id="pet-a",
        name=None,
        key_detection_id="det-a",
        detection_count=2,
        center_embedding=first.embedding,
        embedding_dim=first.embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=2,
    )
    repository.replace_all([first, second], [pet])
    assert repository.set_pet_cover("pet-a", "det-a") is True

    assert repository.delete_detection("det-a") is not None

    summary = repository.get_pet_summaries()[0]
    assert summary.key_detection_id == "det-b"
    assert summary.thumbnail_path == retained_thumbnail.resolve()
    assert not deleted_thumbnail.exists()
    assert retained_thumbnail.exists()


def test_replace_all_prunes_unreferenced_pet_thumbnails(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    thumbnail_dir = tmp_path / "thumbnails"
    thumbnail_dir.mkdir()
    referenced_thumbnail = thumbnail_dir / "current.png"
    orphaned_thumbnail = thumbnail_dir / "old.png"
    uncommitted_thumbnail = thumbnail_dir / "inflight.png"
    referenced_thumbnail.write_bytes(b"current")
    orphaned_thumbnail.write_bytes(b"old")
    uncommitted_thumbnail.write_bytes(b"inflight")
    old_detection = _detection(
        detection_id="old",
        pet_id="pet-a",
        thumbnail_path="thumbnails/old.png",
    )
    old_pet = PetRecord(
        pet_id="pet-a",
        name=None,
        key_detection_id="old",
        detection_count=1,
        center_embedding=old_detection.embedding,
        embedding_dim=old_detection.embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=1,
    )
    repository.replace_all([old_detection], [old_pet])
    detection = _detection(
        detection_id="current",
        pet_id="pet-a",
        thumbnail_path="thumbnails/current.png",
    )
    pet = PetRecord(
        pet_id="pet-a",
        name=None,
        key_detection_id="current",
        detection_count=1,
        center_embedding=detection.embedding,
        embedding_dim=detection.embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=1,
    )

    repository.replace_all([detection], [pet])

    assert referenced_thumbnail.exists()
    assert not orphaned_thumbnail.exists()
    assert uncommitted_thumbnail.exists()


def test_pet_repository_persists_detection_and_profile_species(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    detection = _detection(
        detection_id="det-a",
        pet_id="pet-a",
        pet_key="pet-key-a",
        species_label="Cat",
    )
    pet = PetRecord(
        pet_id="pet-a",
        name=None,
        key_detection_id="det-a",
        detection_count=1,
        center_embedding=detection.embedding,
        embedding_dim=detection.embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=1,
        species_label="Cat",
    )

    repository.replace_all([detection], [pet])

    assert repository.get_all_detections()[0].species_label == "cat"
    assert repository.get_all_pet_records()[0].species_label == "cat"
    assert repository.state_repository is not None
    assert repository.state_repository.get_profiles()[0].species_label == "cat"


def test_merge_pets_blocks_mismatched_hidden_state(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    first = _detection(detection_id="det-a", asset_id="asset-a", pet_id="pet-a")
    second = _detection(detection_id="det-b", asset_id="asset-b", pet_id="pet-b")
    pets = [
        PetRecord(
            pet_id="pet-a",
            name="Miso",
            key_detection_id="det-a",
            detection_count=1,
            center_embedding=first.embedding,
            embedding_dim=first.embedding_dim,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
            sample_count=1,
        ),
        PetRecord(
            pet_id="pet-b",
            name="Nori",
            key_detection_id="det-b",
            detection_count=1,
            center_embedding=second.embedding,
            embedding_dim=second.embedding_dim,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
            sample_count=1,
        ),
    ]
    repository.replace_all([first, second], pets)
    assert repository.set_pet_hidden("pet-a", True) is True

    assert repository.merge_pets("pet-a", "pet-b") is None
    assert {summary.pet_id for summary in repository.get_pet_summaries(include_hidden=True)} == {
        "pet-a",
        "pet-b",
    }


def test_merge_pets_repairs_legacy_runtime_without_durable_profiles(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    first = _detection(detection_id="det-a", asset_id="asset-a", pet_id="pet-a")
    second = _detection(detection_id="det-b", asset_id="asset-b", pet_id="pet-b")
    pets = [
        PetRecord(
            pet_id="pet-a",
            name="Miso",
            key_detection_id="det-a",
            detection_count=1,
            center_embedding=first.embedding,
            embedding_dim=first.embedding_dim,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
            sample_count=1,
        ),
        PetRecord(
            pet_id="pet-b",
            name=None,
            key_detection_id="det-b",
            detection_count=1,
            center_embedding=second.embedding,
            embedding_dim=second.embedding_dim,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
            sample_count=1,
        ),
    ]
    repository.replace_all([first, second], pets, sync_runtime_state=False)

    result = repository.merge_pets("pet-a", "pet-b")

    assert result is not None
    assert result.pet_redirects == {"pet-a": "pet-b"}
    assert [summary.pet_id for summary in repository.get_pet_summaries()] == ["pet-b"]
    assert repository.get_pet_summaries()[0].name == "Miso"
    assert repository.get_asset_ids_by_pet("pet-b") == ["asset-a", "asset-b"]
    assert repository.merge_pets("pet-a", "pet-b") is not None
    assert repository.state_repository is not None
    assert [profile.pet_id for profile in repository.state_repository.get_profiles()] == ["pet-b"]


def test_manual_pet_merge_rejects_incompatible_species(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    source_detection = _detection(
        detection_id="det-source",
        asset_id="asset-source",
        pet_id="pet-source",
        embedding=np.asarray([1.0, 0.0]),
        species_label="cat",
    )
    target_detection = _detection(
        detection_id="det-target",
        asset_id="asset-target",
        pet_id="pet-target",
        embedding=np.asarray([0.0, 1.0]),
        species_label="dog",
    )
    pets = [
        PetRecord(
            pet_id="pet-source",
            name=None,
            key_detection_id=source_detection.detection_id,
            detection_count=1,
            center_embedding=source_detection.embedding,
            embedding_dim=source_detection.embedding_dim,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
            sample_count=1,
        ),
        PetRecord(
            pet_id="pet-target",
            name=None,
            key_detection_id=target_detection.detection_id,
            detection_count=1,
            center_embedding=target_detection.embedding,
            embedding_dim=target_detection.embedding_dim,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
            sample_count=1,
        ),
    ]
    repository.replace_all([source_detection, target_detection], pets)
    assert repository.merge_pets("pet-source", "pet-target") is None
    assert {pet.pet_id for pet in repository.get_all_pet_records()} == {
        "pet-source",
        "pet-target",
    }


def test_redirected_unstable_pet_profile_recognizes_new_key(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    source_detection = _detection(
        detection_id="det-source",
        pet_id="pet-source",
        embedding=np.asarray([1.0, 0.0]),
    )
    target_detection = _detection(
        detection_id="det-target",
        asset_id="asset-target",
        pet_id="pet-target",
        embedding=np.asarray([0.0, 1.0]),
    )
    repository.replace_all(
        [source_detection, target_detection],
        [
            PetRecord(
                pet_id="pet-source",
                name=None,
                key_detection_id=source_detection.detection_id,
                detection_count=1,
                center_embedding=source_detection.embedding,
                embedding_dim=source_detection.embedding_dim,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
                sample_count=1,
            ),
            PetRecord(
                pet_id="pet-target",
                name=None,
                key_detection_id=target_detection.detection_id,
                detection_count=1,
                center_embedding=target_detection.embedding,
                embedding_dim=target_detection.embedding_dim,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
                sample_count=1,
            ),
        ],
    )
    assert repository.merge_pets("pet-source", "pet-target") is not None
    new_source_detection = _detection(
        detection_id="det-source-new",
        asset_id="asset-source-new",
        pet_key="new-source-key",
        embedding=np.asarray([0.99, 0.01]),
    )

    detections, pets = PetScanSession().build_snapshot_from_detections(
        repository,
        detections=[*repository.get_all_detections(), new_source_detection],
        distance_threshold=0.2,
    )

    assert {detection.pet_id for detection in detections} == {"pet-target"}
    assert [pet.pet_id for pet in pets] == ["pet-target"]


def test_pipeline_recluster_and_merge_share_coordinator_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    source_detection = _detection(
        detection_id="det-source",
        pet_id="pet-source",
        embedding=np.asarray([1.0, 0.0]),
    )
    target_detection = _detection(
        detection_id="det-target",
        asset_id="asset-target",
        pet_id="pet-target",
        embedding=np.asarray([0.0, 1.0]),
    )
    repository.replace_all(
        [source_detection, target_detection],
        [
            PetRecord(
                pet_id="pet-source",
                name=None,
                key_detection_id=source_detection.detection_id,
                detection_count=1,
                center_embedding=source_detection.embedding,
                embedding_dim=source_detection.embedding_dim,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
                sample_count=1,
            ),
            PetRecord(
                pet_id="pet-target",
                name=None,
                key_detection_id=target_detection.detection_id,
                detection_count=1,
                center_embedding=target_detection.embedding,
                embedding_dim=target_detection.embedding_dim,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
                sample_count=1,
            ),
        ],
    )
    coordinator = PetIndexCoordinator(tmp_path)
    monkeypatch.setattr(coordinator, "_repository", lambda: repository)
    original_recluster = repository.recluster_detections
    recluster_entered = threading.Event()
    release_recluster = threading.Event()
    merge_finished = threading.Event()
    merge_results: list[bool] = []

    def blocked_recluster(*, distance_threshold: float, operation_id: str | None = None) -> int:
        recluster_entered.set()
        assert release_recluster.wait(2)
        return original_recluster(
            distance_threshold=distance_threshold,
            operation_id=operation_id,
        )

    monkeypatch.setattr(repository, "recluster_detections", blocked_recluster)
    recluster_thread = threading.Thread(
        target=lambda: coordinator.recluster_for_pipeline_upgrade(
            clustering_pipeline_version="test-version",
            distance_threshold=0.2,
        )
    )

    def merge() -> None:
        merge_results.append(coordinator.merge_pets("pet-source", "pet-target").merged)
        merge_finished.set()

    recluster_thread.start()
    assert recluster_entered.wait(2)
    merge_thread = threading.Thread(target=merge)
    merge_thread.start()
    assert merge_finished.wait(0.1) is False
    release_recluster.set()
    recluster_thread.join(2)
    merge_thread.join(2)

    assert merge_results == [True]
    assert [pet.pet_id for pet in repository.get_all_pet_records()] == ["pet-target"]


def test_pet_merge_redirect_chain_keeps_all_alias_clusters_linked(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    detections = [
        _detection(
            detection_id=f"det-{pet_id}",
            asset_id=f"asset-{pet_id}",
            pet_id=pet_id,
            embedding=embedding,
        )
        for pet_id, embedding in (
            ("pet-a", np.asarray([1.0, 0.0, 0.0])),
            ("pet-b", np.asarray([0.0, 1.0, 0.0])),
            ("pet-c", np.asarray([0.0, 0.0, 1.0])),
        )
    ]
    repository.replace_all(
        detections,
        [
            PetRecord(
                pet_id=str(detection.pet_id),
                name=None,
                key_detection_id=detection.detection_id,
                detection_count=1,
                center_embedding=detection.embedding,
                embedding_dim=detection.embedding_dim,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
                sample_count=1,
            )
            for detection in detections
        ],
    )

    assert repository.merge_pets("pet-a", "pet-b") is not None
    assert repository.merge_pets("pet-b", "pet-c") is not None
    assert repository.state_repository is not None
    assert repository.state_repository.get_merge_redirect_map() == {
        "pet-a": "pet-c",
        "pet-b": "pet-c",
    }

    reclustered, pets = PetScanSession().build_snapshot_from_detections(
        repository,
        detections=repository.get_all_detections(),
        distance_threshold=0.2,
    )

    assert {detection.pet_id for detection in reclustered} == {"pet-c"}
    assert [pet.pet_id for pet in pets] == ["pet-c"]


def test_pet_summary_asset_count_counts_unique_assets(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    detections = [
        _detection(detection_id="det-a", asset_id="asset-a", pet_id="pet-a"),
        _detection(detection_id="det-b", asset_id="asset-a", pet_id="pet-a"),
        _detection(detection_id="det-c", asset_id="asset-b", pet_id="pet-a"),
    ]
    pet = PetRecord(
        pet_id="pet-a",
        name=None,
        key_detection_id="det-a",
        detection_count=3,
        center_embedding=detections[0].embedding,
        embedding_dim=detections[0].embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=3,
    )

    repository.replace_all(detections, [pet])

    summaries = repository.get_pet_summaries()
    assert summaries[0].detection_count == 3
    assert summaries[0].asset_count == 2


def test_pet_service_summary_asset_count_matches_filtered_query(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    get_global_repository(library_root).write_rows(
        [{"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "pet_status": "done"}]
    )
    service = create_pet_service(library_root)
    repository = service.repository()
    assert repository is not None
    detections = [
        _detection(detection_id="det-a", asset_id="asset-a", pet_id="pet-a"),
        _detection(detection_id="det-b", asset_id="asset-b", pet_id="pet-a"),
    ]
    pet = PetRecord(
        pet_id="pet-a",
        name=None,
        key_detection_id="det-a",
        detection_count=2,
        center_embedding=detections[0].embedding,
        embedding_dim=detections[0].embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=2,
    )
    repository.replace_all(detections, [pet])

    summaries = service.list_pets()

    assert summaries[0].asset_count == 1
    assert service.build_pet_query("pet-a").asset_ids == ["asset-a"]


def test_pet_summaries_expose_profile_species_and_sort_stable_first(
    tmp_path: Path,
) -> None:
    get_global_repository(tmp_path).write_rows(
        [
            {
                "rel": f"stable-{index}.jpg",
                "id": f"asset-stable-{index}",
                "media_type": 0,
                "pet_status": "done",
            }
            for index in range(3)
        ]
        + [
            {"rel": "unstable.jpg", "id": "asset-unstable", "media_type": 0, "pet_status": "done"},
        ]
    )
    service = create_pet_service(tmp_path)
    repository = service.repository()
    assert repository is not None
    stable_detections = [
        _detection(
            detection_id=f"det-stable-{index}",
            asset_id=f"asset-stable-{index}",
            pet_id="pet-stable",
            species_label="dog",
        )
        for index in range(3)
    ]
    unstable_detection = _detection(
        detection_id="det-unstable",
        asset_id="asset-unstable",
        pet_id="pet-unstable",
        species_label="cat",
    )
    repository.replace_all(
        [*stable_detections, unstable_detection],
        [
            PetRecord(
                pet_id="pet-stable",
                name="Rex",
                key_detection_id="det-stable-0",
                detection_count=3,
                center_embedding=stable_detections[0].embedding,
                embedding_dim=stable_detections[0].embedding_dim,
                created_at="2024-01-02T00:00:00Z",
                updated_at=utc_now_iso(),
                sample_count=3,
                profile_state="stable",
                species_label="dog",
            ),
            PetRecord(
                pet_id="pet-unstable",
                name=None,
                key_detection_id="det-unstable",
                detection_count=1,
                center_embedding=unstable_detection.embedding,
                embedding_dim=unstable_detection.embedding_dim,
                created_at="2024-01-01T00:00:00Z",
                updated_at=utc_now_iso(),
                sample_count=1,
                profile_state="unstable",
                species_label="cat",
            ),
        ],
    )

    summaries = service.list_pets()

    assert [summary.pet_id for summary in summaries] == ["pet-stable", "pet-unstable"]
    assert summaries[0].profile_state == "stable"
    assert summaries[0].species_label == "dog"
    assert summaries[1].profile_state == "unstable"
    assert summaries[1].species_label == "cat"


def test_pet_scan_session_replaces_stale_detections_for_same_asset_path(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    old_detection = replace(
        _detection(detection_id="old", asset_id="asset-old", pet_id="pet-old"),
        asset_rel="album/shared.jpg",
    )
    old_pet = PetRecord(
        pet_id="pet-old",
        name=None,
        key_detection_id="old",
        detection_count=1,
        center_embedding=old_detection.embedding,
        embedding_dim=old_detection.embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=1,
    )
    repository.replace_all([old_detection], [old_pet])
    new_detection = replace(
        _detection(detection_id="new", asset_id="asset-new"),
        asset_rel="album/shared.jpg",
    )
    session = PetScanSession()
    session.stage_detection_results(
        [
            DetectedAssetPets(
                asset_id="asset-new",
                asset_rel="album/shared.jpg",
                detections=[new_detection],
            )
        ]
    )

    detections, _pets = session.build_runtime_snapshot(
        repository,
        distance_threshold=0.2,
        existing_detections=repository.get_all_detections(),
    )

    assert [detection.asset_id for detection in detections] == ["asset-new"]


def test_pet_batch_revalidates_people_overlaps_inside_serialized_commit(tmp_path: Path) -> None:
    service = create_pet_service(tmp_path)
    repository = service.repository()
    coordinator = service.coordinator
    assert repository is not None
    assert coordinator is not None
    thumbnail = tmp_path / ".iPhoto" / "pets" / "thumbnails" / "stale.png"
    thumbnail.parent.mkdir(parents=True)
    thumbnail.write_bytes(b"stale")
    stale_detection = replace(
        _detection(
            detection_id="stale",
            asset_id="asset-face",
            thumbnail_path="thumbnails/stale.png",
        ),
        box_x=59,
        box_y=134,
        box_w=4024,
        box_h=5216,
        image_width=4160,
        image_height=6240,
    )

    event = coordinator.submit_detected_batch(
        [
            DetectedAssetPets(
                asset_id="asset-face",
                asset_rel="album/face.jpg",
                detections=[stale_detection],
            )
        ],
        distance_threshold=0.42,
        people_boxes_provider=lambda _asset_ids: {"asset-face": ((732, 668, 2089, 2930),)},
    )

    assert event is not None
    assert repository.get_all_detections() == []
    assert not thumbnail.exists()


def test_pet_reconciliation_removes_dscf6997_mural_detected_before_people(
    tmp_path: Path,
) -> None:
    service = create_pet_service(tmp_path)
    repository = service.repository()
    coordinator = service.coordinator
    assert repository is not None
    assert coordinator is not None
    mural_detection = replace(
        _detection(
            detection_id="dscf6997-mural",
            asset_id="asset-mural",
            species_label="dog",
        ),
        box_x=0,
        box_y=64,
        box_w=4092,
        box_h=4237,
        image_width=4160,
        image_height=6240,
        confidence=0.767,
    )

    coordinator.submit_detected_batch(
        [
            DetectedAssetPets(
                asset_id="asset-mural",
                asset_rel="album/mural.jpg",
                detections=[mural_detection],
            )
        ],
        distance_threshold=0.42,
    )

    assert len(repository.get_all_detections()) == 1

    event = coordinator.reconcile_people_overlaps(
        {
            "asset-mural": (
                (2463, 3780, 266, 281),
                (589, 650, 2103, 2878),
            )
        }
    )

    assert event is not None
    assert event.changed_asset_ids == ("asset-mural",)
    assert repository.get_all_detections() == []


def test_pet_scan_session_rolls_back_runtime_snapshot_when_state_sync_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    old_detection = _detection(detection_id="old", asset_id="asset-old", pet_id="pet-old")
    old_pet = PetRecord(
        pet_id="pet-old",
        name=None,
        key_detection_id="old",
        detection_count=1,
        center_embedding=old_detection.embedding,
        embedding_dim=old_detection.embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=1,
    )
    repository.replace_all([old_detection], [old_pet])
    new_detection = _detection(detection_id="new", asset_id="asset-new", pet_id="pet-new")
    new_pet = PetRecord(
        pet_id="pet-new",
        name=None,
        key_detection_id="new",
        detection_count=1,
        center_embedding=new_detection.embedding,
        embedding_dim=new_detection.embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=1,
    )
    original_sync_runtime_state = repository.sync_runtime_state
    calls = {"count": 0}

    def fail_once() -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("state db locked")
        original_sync_runtime_state()

    monkeypatch.setattr(repository, "sync_runtime_state", fail_once)

    with pytest.raises(RuntimeError, match="state db locked"):
        PetScanSession().commit(repository, detections=[new_detection], pets=[new_pet])

    assert [detection.detection_id for detection in repository.get_all_detections()] == ["old"]
    assert [pet.pet_id for pet in repository.get_all_pet_records()] == ["pet-old"]


def test_pet_reconciliation_removes_people_overlap_and_preserves_other_pet_state(
    tmp_path: Path,
) -> None:
    service = create_pet_service(tmp_path)
    repository = service.repository()
    coordinator = service.coordinator
    assert repository is not None
    assert coordinator is not None
    conflicting = replace(
        _detection(
            detection_id="conflict",
            asset_id="asset-face",
            embedding=np.asarray([1.0, 0.0, 0.0]),
            species_label="dog",
            thumbnail_path="thumbnails/conflict.png",
        ),
        box_x=59,
        box_y=134,
        box_w=4024,
        box_h=5216,
        image_width=4160,
        image_height=6240,
    )
    retained = replace(
        _detection(
            detection_id="retained",
            asset_id="asset-cat",
            embedding=np.asarray([0.0, 1.0, 0.0]),
            species_label="cat",
            thumbnail_path="thumbnails/retained.png",
        ),
        box_x=3000,
        box_y=200,
        box_w=500,
        box_h=700,
        image_width=4160,
        image_height=6240,
    )
    thumbnail_dir = tmp_path / ".iPhoto" / "pets" / "thumbnails"
    conflicting_thumbnail = thumbnail_dir / "conflict.png"
    retained_thumbnail = thumbnail_dir / "retained.png"
    retained_thumbnail.parent.mkdir(parents=True)
    conflicting_thumbnail.write_bytes(b"conflict")
    retained_thumbnail.write_bytes(b"retained")
    detections, pets = cluster_pet_records(
        [conflicting, retained],
        distance_threshold=0.42,
    )
    repository.replace_all(detections, pets)
    retained_detection = next(
        detection
        for detection in repository.get_all_detections()
        if detection.asset_id == "asset-cat"
    )
    assert retained_detection.pet_id
    retained_pet_id = retained_detection.pet_id
    assert repository.rename_pet(retained_detection.pet_id, "Miso")
    assert repository.set_pet_hidden(retained_detection.pet_id, True)
    assert repository.set_pet_cover(retained_detection.pet_id, "retained")

    event = coordinator.reconcile_people_overlaps(
        {"asset-face": ((732, 668, 2089, 2930),)},
    )

    assert event is not None
    assert event.changed_asset_ids == ("asset-face",)
    assert repository.list_asset_pet_annotations("asset-face") == []
    remaining = repository.get_all_detections()
    assert [detection.asset_id for detection in remaining] == ["asset-cat"]
    summaries = repository.get_pet_summaries(include_hidden=True)
    assert len(summaries) == 1
    assert summaries[0].pet_id == retained_pet_id
    assert summaries[0].name == "Miso"
    assert summaries[0].is_hidden is True
    assert summaries[0].thumbnail_path == retained_thumbnail.resolve()
    assert not conflicting_thumbnail.exists()
    assert retained_thumbnail.exists()


def test_pet_service_uses_auto_and_manual_people_faces_as_exclusion_boxes(
    tmp_path: Path,
) -> None:
    faces_root = tmp_path / ".iPhoto" / "faces"
    state = FaceStateRepository(faces_root / "face_state.db")
    state.add_manual_face(
        ManualFaceRecord(
            face_id="manual-face",
            asset_id="asset-a",
            asset_rel="album/a.jpg",
            box_x=20,
            box_y=30,
            box_w=120,
            box_h=140,
            thumbnail_path=None,
            person_id="person-a",
            created_at=utc_now_iso(),
            image_width=400,
            image_height=300,
        ),
        person_name="Alice",
    )
    embedding = normalize_vector(np.asarray([1.0, 0.0, 0.0], dtype=np.float32))
    FaceRepository(faces_root / "face_index.db", faces_root / "face_state.db").replace_all(
        [
            FaceRecord(
                face_id="auto-face",
                face_key="auto-key",
                asset_id="asset-a",
                asset_rel="album/a.jpg",
                box_x=5,
                box_y=10,
                box_w=80,
                box_h=90,
                confidence=0.9,
                embedding=embedding,
                embedding_dim=3,
                thumbnail_path=None,
                person_id="person-auto",
                detected_at=utc_now_iso(),
                image_width=400,
                image_height=300,
            )
        ],
        [
            PersonRecord(
                person_id="person-auto",
                name="Bob",
                key_face_id="auto-face",
                face_count=1,
                center_embedding=embedding,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
                sample_count=1,
            )
        ],
    )
    service = create_pet_service(tmp_path)

    assert service.people_boxes_by_asset_ids(["asset-a", "asset-missing"]) == {
        "asset-a": ((5, 10, 80, 90), (20, 30, 120, 140))
    }


def test_pet_status_helpers_and_scan_merge(tmp_path: Path) -> None:
    repo = get_global_repository(tmp_path)
    repo.write_rows(
        [
            {"rel": "photo.jpg", "id": "asset-photo", "media_type": 0, "pet_status": "pending"},
            {"rel": "clip.mp4", "id": "asset-video", "media_type": 1, "pet_status": "skipped"},
        ]
    )

    assert [row["id"] for row in repo.read_rows_by_pet_status(["pending"])] == ["asset-photo"]
    repo.update_pet_status("asset-photo", "retry")
    repo.update_pet_statuses(["asset-video"], "done")
    rows = repo.get_rows_by_ids(["asset-photo", "asset-video"])
    assert rows["asset-photo"]["pet_status"] == "retry"
    assert rows["asset-video"]["pet_status"] == "done"
    assert repo.count_by_pet_status() == {"retry": 1, "done": 1}

    merged = repo.merge_scan_rows(
        [{"rel": "photo.jpg", "id": "asset-photo", "media_type": 0, "bytes": 1}]
    )
    assert merged[0]["pet_status"] == "pending"


def test_pet_detector_model_downloads_when_missing(tmp_path: Path, monkeypatch) -> None:
    payload = b"pet-detector"
    target = tmp_path / "models" / "pets" / "detector" / "yolox_nano_coco.onnx"
    monkeypatch.setenv("IPHOTO_PET_DETECTOR_MODEL_URL", "https://models.example/yolox.onnx")
    monkeypatch.setenv(
        "IPHOTO_PET_DETECTOR_MODEL_SHA256",
        hashlib.sha256(payload).hexdigest(),
    )
    monkeypatch.setattr(
        pet_pipeline.request,
        "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(payload),
    )
    real_temporary_directory = pet_pipeline.tempfile.TemporaryDirectory
    temporary_directories: list[Path] = []

    def recording_temporary_directory(*args, **kwargs):
        assert Path(kwargs["dir"]) == target.parent
        manager = real_temporary_directory(*args, **kwargs)
        temporary_directories.append(Path(manager.name))
        return manager

    monkeypatch.setattr(
        pet_pipeline.tempfile,
        "TemporaryDirectory",
        recording_temporary_directory,
    )

    resolved = ensure_pet_detector_model(target, allow_model_download=True)

    assert resolved == target
    assert target.read_bytes() == b"pet-detector"
    assert len(temporary_directories) == 1
    assert not temporary_directories[0].exists()


def test_dinov2_runtime_downloads_only_the_fixed_torchscript_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeModel:
        def eval(self):
            return self

        def to(self, _device):
            return self

    class FakeJit:
        @staticmethod
        def load(path: str, *, map_location: str):
            assert Path(path).read_bytes() == b"fixed-torchscript"
            assert map_location == "cpu"
            return FakeModel()

    embedder = _DinoV2Embedder.__new__(_DinoV2Embedder)
    embedder._torch = SimpleNamespace(jit=FakeJit())
    embedder._device = "cpu"
    embedder._model_name = "dinov2_vits14"
    monkeypatch.setattr(pet_pipeline, "_install_certifi_environment", lambda: None)
    monkeypatch.setitem(
        pet_pipeline._EMBEDDER_MANIFEST,
        "torchscript_url",
        "https://models.example.test/dinov2_vits14.pt",
    )
    monkeypatch.setitem(
        pet_pipeline._EMBEDDER_MANIFEST,
        "torchscript_sha256",
        hashlib.sha256(b"fixed-torchscript").hexdigest(),
    )
    monkeypatch.setitem(
        pet_pipeline._EMBEDDER_MANIFEST,
        "torchscript_size",
        len(b"fixed-torchscript"),
    )

    def fake_download(url, path, **kwargs):
        assert url == "https://models.example.test/dinov2_vits14.pt"
        assert kwargs["expected_sha256"] == hashlib.sha256(b"fixed-torchscript").hexdigest()
        assert kwargs["max_bytes"] == len(b"fixed-torchscript")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fixed-torchscript")

    monkeypatch.setattr(pet_pipeline, "_download_file", fake_download)
    model_path = tmp_path / "dinov2_vits14.pt"
    model = embedder._download_dinov2_model(model_path)

    assert isinstance(model, FakeModel)
    assert model_path.read_bytes() == b"fixed-torchscript"
    assert pet_pipeline._dinov2_metadata_path(model_path).is_file()


def test_pet_embedding_source_is_pinned_to_commit() -> None:
    assert len(_DINO_SOURCE_REVISION) == 40
    assert all(character in "0123456789abcdef" for character in _DINO_SOURCE_REVISION)


def test_pet_model_manifest_is_the_runtime_contract_source() -> None:
    detector = PET_MODEL_MANIFEST["detector"]
    assert detector["url"] == DEFAULT_PET_DETECTOR_MODEL_URL
    assert detector["sha256"] == DEFAULT_PET_DETECTOR_MODEL_SHA256
    assert detector["input"] == {
        "layout": "NCHW",
        "channel_order": "BGR",
        "dtype": "float32",
        "range": [0, 255],
        "shape": [1, 3, 416, 416],
    }
    embedder = PET_MODEL_MANIFEST["embedder"]
    assert embedder["source_revision"] == _DINO_SOURCE_REVISION
    assert len(embedder["torchscript_sha256"]) == 64
    assert embedder["torchscript_size"] > 0


def test_pet_detector_pipeline_version_includes_hybrid_deduplication() -> None:
    assert PET_DETECTOR_PIPELINE_VERSION == "yolox-letterbox-tiles-people-priority-v6"


def test_default_pet_model_dir_uses_user_cache(monkeypatch) -> None:
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    assert default_pet_model_dir().name == "pets"
    assert "Caches" in default_pet_model_dir().parts or ".cache" in default_pet_model_dir().parts


def test_pet_scan_worker_resets_done_rows_for_detector_upgrade(tmp_path: Path) -> None:
    asset_repo = _FakePetAssetRepository(
        [
            {"id": "asset-photo", "rel": "photo.jpg", "media_type": 0, "pet_status": "done"},
            {"id": "asset-video", "rel": "clip.mov", "media_type": 1, "pet_status": "done"},
        ]
    )
    service = create_pet_service(tmp_path, asset_repository=asset_repo)
    worker = PetScanWorker(tmp_path, pet_service=service)

    worker._reset_done_rows_for_detector_upgrade()

    assert asset_repo.update_calls == [(("asset-photo",), "pending")]
    assert asset_repo.rows[0]["pet_status"] == "pending"
    assert asset_repo.rows[1]["pet_status"] == "done"
    repository = service.repository()
    assert repository is not None
    assert repository.get_scan_metadata("detector_pipeline_version") is None
    assert repository.get_scan_metadata("detector_migration_target") == (
        PET_DETECTOR_PIPELINE_VERSION
    )
    assert repository.get_scan_metadata("detector_migration_state") == "running"


def test_pet_scan_worker_does_not_reset_current_detector_version(tmp_path: Path) -> None:
    asset_repo = _FakePetAssetRepository(
        [{"id": "asset-photo", "rel": "photo.jpg", "media_type": 0, "pet_status": "done"}]
    )
    service = create_pet_service(tmp_path, asset_repository=asset_repo)
    repository = service.repository()
    assert repository is not None
    repository.set_scan_metadata("detector_pipeline_version", PET_DETECTOR_PIPELINE_VERSION)
    worker = PetScanWorker(tmp_path, pet_service=service)

    worker._reset_done_rows_for_detector_upgrade()

    assert asset_repo.update_calls == []
    assert asset_repo.rows[0]["pet_status"] == "done"


def test_pet_scan_worker_maps_legacy_backfill_marker_to_running_migration(
    tmp_path: Path,
) -> None:
    asset_repo = _FakePetAssetRepository(
        [{"id": "asset-photo", "rel": "photo.jpg", "media_type": 0, "pet_status": "pending"}]
    )
    service = create_pet_service(tmp_path, asset_repository=asset_repo)
    repository = service.repository()
    assert repository is not None
    repository.set_scan_metadata_many(
        {
            "detector_pipeline_version": PET_DETECTOR_PIPELINE_VERSION,
            "pet_backfill_required": "1",
        }
    )
    worker = PetScanWorker(tmp_path, pet_service=service)

    worker._prepare_detector_migration()

    assert asset_repo.update_calls == []
    assert repository.get_scan_metadata("detector_migration_target") == (
        PET_DETECTOR_PIPELINE_VERSION
    )
    assert repository.get_scan_metadata("detector_migration_state") == "running"


def test_pet_scan_worker_reclusters_for_clustering_upgrade_without_resetting_assets(
    tmp_path: Path,
) -> None:
    asset_repo = _FakePetAssetRepository(
        [
            {"id": "asset-cat", "rel": "cat.jpg", "media_type": 0, "pet_status": "done"},
            {"id": "asset-dog", "rel": "dog.jpg", "media_type": 0, "pet_status": "done"},
        ]
    )
    service = create_pet_service(tmp_path, asset_repository=asset_repo)
    repository = service.repository()
    assert repository is not None
    cat = _detection(
        detection_id="det-cat",
        asset_id="asset-cat",
        pet_id="pet-old",
        embedding=np.asarray([1.0, 0.0]),
        species_label="cat",
    )
    dog = _detection(
        detection_id="det-dog",
        asset_id="asset-dog",
        pet_id="pet-old",
        embedding=np.asarray([1.0, 0.0]),
        species_label="dog",
    )
    old_pet = PetRecord(
        pet_id="pet-old",
        name=None,
        key_detection_id="det-cat",
        detection_count=2,
        center_embedding=cat.embedding,
        embedding_dim=cat.embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=2,
    )
    repository.replace_all([cat, dog], [old_pet])
    repository.set_scan_metadata("clustering_pipeline_version", "old-clustering")
    worker = PetScanWorker(tmp_path, pet_service=service)
    pipeline = PetClusterPipeline(
        model_root=tmp_path / "models",
        allow_model_download=False,
    )

    assert worker._recluster_for_clustering_upgrade(pipeline) is True

    assert asset_repo.update_calls == []
    assert [row["pet_status"] for row in asset_repo.rows] == ["done", "done"]
    assert repository.get_scan_metadata("clustering_pipeline_version") == (
        PET_CLUSTERING_PIPELINE_VERSION
    )
    detections = repository.get_all_detections()
    assert len({detection.pet_id for detection in detections}) == 2


def test_pet_scan_worker_recluster_does_not_let_old_key_votes_remerge_split_cats(
    tmp_path: Path,
) -> None:
    asset_repo = _FakePetAssetRepository(
        [
            {"id": "asset-cat-a", "rel": "cat-a.jpg", "media_type": 0, "pet_status": "done"},
            {"id": "asset-cat-b", "rel": "cat-b.jpg", "media_type": 0, "pet_status": "done"},
        ]
    )
    service = create_pet_service(tmp_path, asset_repository=asset_repo)
    repository = service.repository()
    assert repository is not None
    first_cat = _detection(
        detection_id="det-cat-a",
        asset_id="asset-cat-a",
        pet_id="pet-old",
        embedding=np.asarray([1.0, 0.0]),
        species_label="cat",
    )
    second_cat = _detection(
        detection_id="det-cat-b",
        asset_id="asset-cat-b",
        pet_id="pet-old",
        embedding=np.asarray([0.0, 1.0]),
        species_label="cat",
    )
    old_pet = PetRecord(
        pet_id="pet-old",
        name=None,
        key_detection_id="det-cat-a",
        detection_count=2,
        center_embedding=first_cat.embedding,
        embedding_dim=first_cat.embedding_dim,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        sample_count=2,
        species_label="cat",
    )
    repository.replace_all([first_cat, second_cat], [old_pet])
    repository.set_scan_metadata("clustering_pipeline_version", "old-clustering")
    worker = PetScanWorker(tmp_path, pet_service=service)
    pipeline = PetClusterPipeline(
        model_root=tmp_path / "models",
        allow_model_download=False,
    )

    assert worker._recluster_for_clustering_upgrade(pipeline) is True

    detections = repository.get_all_detections()
    pet_ids = {detection.pet_id for detection in detections}
    assert len(pet_ids) == 2
    assert "pet-old" in pet_ids


def test_pet_scan_worker_missing_runtime_keeps_pending(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("IPHOTO_PET_MODEL_AUTO_DOWNLOAD", "0")
    repo = get_global_repository(tmp_path)
    repo.write_rows(
        [{"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "pet_status": "pending"}]
    )
    service = create_pet_service(tmp_path)
    worker = PetScanWorker(tmp_path, pet_service=service)

    worker.run()

    row = repo.get_rows_by_ids(["asset-a"])["asset-a"]
    assert row["pet_status"] == "pending"


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (RuntimeError("program bug"), "Pet scanning paused: program bug"),
        (PetRuntimeUnavailableError("missing dependency"), "missing dependency"),
        (PetModelUnavailableError("missing model"), "missing model"),
    ],
)
def test_pet_scan_worker_only_typed_availability_errors_use_unavailable_path(
    tmp_path: Path,
    monkeypatch,
    error: RuntimeError,
    expected_message: str,
) -> None:
    repo = get_global_repository(tmp_path)
    repo.write_rows(
        [{"rel": "album/a.jpg", "id": "asset-a", "media_type": 0, "pet_status": "pending"}]
    )
    service = create_pet_service(tmp_path)
    repository = service.repository()
    assert repository is not None
    repository.set_scan_metadata(
        "clustering_pipeline_version",
        PET_CLUSTERING_PIPELINE_VERSION,
    )
    worker = PetScanWorker(tmp_path, pet_service=service)
    messages: list[str] = []
    worker.statusChanged.connect(messages.append)

    def fail_batch(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(worker, "_process_batch", fail_batch)
    worker.run()

    assert repo.get_rows_by_ids(["asset-a"])["asset-a"]["pet_status"] == "pending"
    assert messages[-1] == expected_message
