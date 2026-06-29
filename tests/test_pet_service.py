from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from iPhoto.bootstrap.library_pet_service import create_pet_service
from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.library.workers.pet_scan_worker import PetScanWorker
from iPhoto.pets.index_coordinator import reset_pet_index_coordinators
from iPhoto.pets.pipeline import (
    PET_DETECTOR_PIPELINE_VERSION,
    PetClusterPipeline,
    _decode_yolox_predictions,
    build_pet_key,
    canonicalize_pet_identities,
    cluster_pet_records,
    ensure_pet_detector_model,
)
from iPhoto.pets.records import PetDetectionRecord, PetRecord
from iPhoto.pets.repository import PetRepository
from iPhoto.pets.repository_utils import normalize_vector, utc_now_iso
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
    species_label: str = "cat",
    embedding: np.ndarray | None = None,
) -> PetDetectionRecord:
    vector = normalize_vector(
        embedding if embedding is not None else np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    )
    return PetDetectionRecord(
        detection_id=detection_id,
        pet_key=pet_key or f"key-{detection_id}",
        asset_id=asset_id,
        asset_rel=f"album/{asset_id}.jpg",
        species_label=species_label,
        box_x=10,
        box_y=20,
        box_w=80,
        box_h=90,
        confidence=0.9,
        embedding=vector,
        embedding_dim=int(vector.shape[0]),
        embedding_model="dinov2_vits14",
        detector_model="yolox_nano_coco",
        thumbnail_path=None,
        pet_id=pet_id,
        detected_at=utc_now_iso(),
        image_width=400,
        image_height=300,
    )


def test_build_pet_key_is_stable_for_small_bbox_jitter() -> None:
    first = build_pet_key(
        asset_id="asset-a",
        bbox=(100, 80, 120, 110),
        image_width=1000,
        image_height=800,
        species_label="cat",
    )
    jittered = build_pet_key(
        asset_id="asset-a",
        bbox=(102, 82, 119, 111),
        image_width=1000,
        image_height=800,
        species_label="cat",
    )
    dog = build_pet_key(
        asset_id="asset-a",
        bbox=(102, 82, 119, 111),
        image_width=1000,
        image_height=800,
        species_label="dog",
    )

    assert jittered == first
    assert dog != first


def test_cluster_pet_records_keeps_species_separate() -> None:
    detections = [
        _detection(detection_id="cat-a", species_label="cat", embedding=np.asarray([1.0, 0.0])),
        _detection(detection_id="cat-b", species_label="cat", embedding=np.asarray([0.99, 0.01])),
        _detection(detection_id="dog-a", species_label="dog", embedding=np.asarray([1.0, 0.0])),
        _detection(detection_id="dog-b", species_label="dog", embedding=np.asarray([0.99, 0.01])),
    ]

    clustered, pets = cluster_pet_records(
        detections,
        distance_threshold=0.2,
        min_samples=2,
        prefer_hdbscan=False,
    )

    assert len(pets) == 2
    cat_ids = {item.pet_id for item in clustered if item.species_label == "cat"}
    dog_ids = {item.pet_id for item in clustered if item.species_label == "dog"}
    assert len(cat_ids) == 1
    assert len(dog_ids) == 1
    assert cat_ids != dog_ids


def test_cluster_pet_records_default_clusters_small_similar_pet_samples() -> None:
    detections = [
        _detection(detection_id="cat-a", embedding=np.asarray([1.0, 0.0])),
        _detection(detection_id="cat-b", embedding=np.asarray([0.99, 0.01])),
    ]

    clustered, pets = cluster_pet_records(
        detections,
        distance_threshold=0.2,
        min_samples=2,
    )

    assert len(pets) == 1
    assert {item.pet_id for item in clustered} == {pets[0].pet_id}


def test_cluster_pet_records_passes_float64_distance_matrix_to_hdbscan(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeHdbscan:
        def __init__(self, **kwargs) -> None:
            captured["kwargs"] = kwargs

        def fit_predict(self, distance: np.ndarray) -> np.ndarray:
            captured["dtype"] = distance.dtype
            captured["contiguous"] = bool(distance.flags.c_contiguous)
            return np.asarray([0, 0, 1], dtype=np.int32)

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
        ),
        _detection(
            detection_id="cat-b",
            asset_id="asset-b",
            embedding=np.asarray([0.99, 0.01]),
        ),
        _detection(
            detection_id="cat-c",
            asset_id="asset-c",
            embedding=np.asarray([0.0, 1.0]),
        ),
    ]

    clustered, pets = cluster_pet_records(
        detections,
        distance_threshold=0.2,
        min_samples=2,
    )

    assert captured["kwargs"] == {
        "metric": "precomputed",
        "min_cluster_size": 2,
        "min_samples": 1,
    }
    assert captured["dtype"] == np.dtype(np.float64)
    assert captured["contiguous"] is True
    assert len(pets) == 2
    assert clustered[0].pet_id == clustered[1].pet_id
    assert clustered[2].pet_id != clustered[0].pet_id


def test_cluster_pet_records_falls_back_when_hdbscan_rejects_distance_dtype(
    monkeypatch,
) -> None:
    class _RejectingHdbscan:
        def __init__(self, **_kwargs) -> None:
            pass

        def fit_predict(self, _distance: np.ndarray) -> np.ndarray:
            raise ValueError(
                "Buffer dtype mismatch, expected 'double_t' but got 'float'"
            )

    monkeypatch.setitem(
        sys.modules,
        "hdbscan",
        SimpleNamespace(HDBSCAN=_RejectingHdbscan),
    )
    detections = [
        _detection(
            detection_id="cat-a",
            asset_id="asset-a",
            embedding=np.asarray([1.0, 0.0]),
        ),
        _detection(
            detection_id="cat-b",
            asset_id="asset-b",
            embedding=np.asarray([0.99, 0.01]),
        ),
        _detection(
            detection_id="cat-c",
            asset_id="asset-c",
            embedding=np.asarray([0.0, 1.0]),
        ),
    ]

    clustered, pets = cluster_pet_records(
        detections,
        distance_threshold=0.2,
        min_samples=2,
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
    assert [detection.species_label for detection in results[0].detections] == ["dog"]
    assert results[0].detections[0].box_w == 160
    assert pipeline.last_scan_metrics.candidate_boxes == 3
    assert pipeline.last_scan_metrics.unsupported_species == 1
    assert pipeline.last_scan_metrics.too_small == 1
    assert pipeline.last_scan_metrics.accepted_detections == 1


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
        species_label="cat",
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
        species_label="cat",
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
        species_label="cat",
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
    source = tmp_path / "remote-yolox.onnx"
    source.write_bytes(b"pet-detector")
    target = tmp_path / "models" / "pets" / "detector" / "yolox_nano_coco.onnx"
    monkeypatch.setenv("IPHOTO_PET_DETECTOR_MODEL_URL", source.as_uri())

    resolved = ensure_pet_detector_model(target, allow_model_download=True)

    assert resolved == target
    assert target.read_bytes() == b"pet-detector"


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
    assert repository.get_scan_metadata("detector_pipeline_version") == (
        PET_DETECTOR_PIPELINE_VERSION
    )


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
