from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from iPhoto.bootstrap.library_pet_service import create_pet_service
from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.library.workers.pet_scan_worker import PetScanWorker
from iPhoto.pets.index_coordinator import reset_pet_index_coordinators
from iPhoto.pets.pipeline import (
    build_pet_key,
    canonicalize_pet_identities,
    cluster_pet_records,
    ensure_pet_detector_model,
)
from iPhoto.pets.records import PetDetectionRecord, PetRecord
from iPhoto.pets.repository import PetRepository
from iPhoto.pets.repository_utils import normalize_vector, utc_now_iso
from iPhoto.pets.state_repository import PetStateRepository


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
