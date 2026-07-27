from __future__ import annotations

import os
import resource
import time
from pathlib import Path

import numpy as np
import pytest

from iPhoto.pets.records import PetDetectionRecord, PetRecord
from iPhoto.pets.repository import PetRepository
from iPhoto.pets.repository_utils import normalize_vector, utc_now_iso

pytestmark = pytest.mark.pets_scale_contract


def test_incremental_commit_scales_to_50k_without_full_rewrite(tmp_path: Path) -> None:
    if os.environ.get("IPHOTO_RUN_PETS_SCALE_CONTRACT") != "1":
        pytest.skip("large synthetic contract is run by the pets-scale-contract PR job")

    one_k = _benchmark_incremental(tmp_path / "one-k", 1_000)
    ten_k = _benchmark_incremental(tmp_path / "ten-k", 10_000)
    fifty_k = _benchmark_incremental(tmp_path / "fifty-k", 50_000)

    assert one_k["seconds"] <= 5.0
    assert fifty_k["seconds"] <= max(ten_k["seconds"] * 8.0, 0.5)
    assert fifty_k["seconds"] <= 5.0
    assert fifty_k["wal_delta"] <= 10 * 1024 * 1024
    assert fifty_k["rss_bytes"] <= 1536 * 1024 * 1024
    assert not any(
        statement.strip().upper() in {"DELETE FROM PETS", "DELETE FROM PET_DETECTIONS"}
        for statement in fifty_k["sql"]
    )


def _benchmark_incremental(root: Path, count: int) -> dict[str, object]:
    root.mkdir(parents=True)
    repository = PetRepository(root / "pet_index.db")
    detections, pets = _synthetic_snapshot(count)
    repository.replace_all(detections, pets)
    wal_path = Path(f"{repository.db_path}-wal")
    wal_before = wal_path.stat().st_size if wal_path.exists() else 0

    sql: list[str] = []
    original_connect = repository._connect

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(sql.append)
        return connection

    repository._connect = traced_connect  # type: ignore[method-assign]
    increment = [
        _detection(
            detection_id=f"increment-{index}",
            asset_id=f"increment-asset-{index}",
            embedding=detections[index].embedding,
            pet_id=None,
        )
        for index in range(2)
    ]
    started = time.perf_counter()
    result = repository.replace_assets_incrementally(
        [detection.asset_id for detection in increment],
        increment,
        distance_threshold=0.05,
    )
    elapsed = time.perf_counter() - started
    assert not result.added_pet_ids
    assert len(result.updated_pet_ids) == 2
    wal_after = wal_path.stat().st_size if wal_path.exists() else 0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_bytes = int(rss if os.uname().sysname == "Darwin" else rss * 1024)
    return {
        "seconds": elapsed,
        "wal_delta": max(0, wal_after - wal_before),
        "rss_bytes": rss_bytes,
        "sql": tuple(sql),
    }


def _synthetic_snapshot(count: int) -> tuple[list[PetDetectionRecord], list[PetRecord]]:
    timestamp = utc_now_iso()
    detections: list[PetDetectionRecord] = []
    pets: list[PetRecord] = []
    for index in range(count):
        angle = (index + 1) * 0.0001
        vector = normalize_vector(
            np.asarray(
                [
                    np.cos(angle),
                    np.sin(angle),
                    float((index % 17) / 17),
                    float((index % 31) / 31),
                ],
                dtype=np.float32,
            )
        )
        pet_id = f"pet-{index:06d}"
        detection = _detection(
            detection_id=f"detection-{index:06d}",
            asset_id=f"asset-{index:06d}",
            embedding=vector,
            pet_id=pet_id,
        )
        detections.append(detection)
        pets.append(
            PetRecord(
                pet_id=pet_id,
                name=None,
                key_detection_id=detection.detection_id,
                detection_count=1,
                center_embedding=vector,
                embedding_dim=vector.size,
                created_at=timestamp,
                updated_at=timestamp,
                sample_count=1,
                species_label="dog",
            )
        )
    return detections, pets


def _detection(
    *,
    detection_id: str,
    asset_id: str,
    embedding: np.ndarray,
    pet_id: str | None,
) -> PetDetectionRecord:
    return PetDetectionRecord(
        detection_id=detection_id,
        pet_key=f"v2:{detection_id}",
        asset_id=asset_id,
        asset_rel=f"album/{asset_id}.jpg",
        box_x=1,
        box_y=2,
        box_w=100,
        box_h=120,
        confidence=0.9,
        embedding=embedding,
        embedding_dim=int(embedding.size),
        embedding_model="dinov2_vits14",
        detector_model="yolox_nano_coco",
        thumbnail_path=None,
        pet_id=pet_id,
        detected_at=utc_now_iso(),
        image_width=800,
        image_height=600,
        species_label="dog",
    )
