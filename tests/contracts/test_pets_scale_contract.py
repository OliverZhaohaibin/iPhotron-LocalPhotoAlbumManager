from __future__ import annotations

import os
import sys
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
    growth_ten_k = _benchmark_growth(tmp_path / "growth-ten-k", 10_000)
    growth_fifty_k = _benchmark_growth(tmp_path / "growth-fifty-k", 50_000)

    assert one_k["seconds"] <= 5.0
    assert fifty_k["seconds"] <= max(ten_k["seconds"] * 8.0, 0.5)
    assert fifty_k["seconds"] <= 5.0
    assert fifty_k["wal_delta"] <= 10 * 1024 * 1024
    assert fifty_k["rss_bytes"] <= 1536 * 1024 * 1024
    assert not any(
        statement.strip().upper() in {"DELETE FROM PETS", "DELETE FROM PET_DETECTIONS"}
        for statement in fifty_k["sql"]
    )
    assert growth_fifty_k["seconds"] <= max(growth_ten_k["seconds"] * 8.0, 1.0)
    assert growth_fifty_k["rss_bytes"] <= 1536 * 1024 * 1024
    assert growth_fifty_k["full_profile_reads"] <= 1


def test_production_shape_50k_with_usearch(tmp_path: Path) -> None:
    if os.environ.get("IPHOTO_RUN_PETS_PRODUCTION_SHAPE_CONTRACT") != "1":
        pytest.skip("production-shape contract is run by its dedicated PR job")
    pytest.importorskip("usearch.index")

    metrics = _benchmark_growth(
        tmp_path / "production-shape-50k",
        50_000,
        dimension=384,
        exercise_restart=True,
    )
    print(f"Pets 50k x 384 metrics: {metrics}")

    if sys.platform.startswith("linux"):
        assert metrics["seconds"] <= 12 * 60
        assert metrics["cold_restart_seconds"] <= 60
        assert metrics["incremental_wal_delta"] <= 64 * 1024 * 1024
        assert metrics["rss_bytes"] <= 3 * 1024 * 1024 * 1024
    else:
        assert metrics["rss_bytes"] <= 4 * 1024 * 1024 * 1024


def test_production_shape_fallback_is_correct_at_1k(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("IPHOTO_RUN_PETS_PRODUCTION_SHAPE_CONTRACT") != "1":
        pytest.skip("fallback production-shape contract accompanies the dedicated PR job")
    monkeypatch.setitem(sys.modules, "usearch", None)
    monkeypatch.setitem(sys.modules, "usearch.index", None)

    metrics = _benchmark_growth(
        tmp_path / "fallback-shape-1k",
        1_000,
        dimension=384,
        exercise_restart=True,
    )
    print(f"Pets fallback 1k x 384 metrics: {metrics}")

    assert metrics["cold_restart_seconds"] > 0
    assert metrics["mutation_seconds"] > 0
    assert metrics["rss_bytes"] <= 4 * 1024 * 1024 * 1024


def _benchmark_incremental(root: Path, count: int) -> dict[str, object]:
    root.mkdir(parents=True)
    repository = PetRepository(root / "pet_index.db", root / "pet_state.db")
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
    # Keep the two probes far apart. Adjacent synthetic vectors are deliberately
    # dense, so an approximate ANN lookup may validly resolve both to the same
    # nearby profile even though the incremental write itself is correct.
    probe_indices = (0, count // 2)
    increment = [
        _detection(
            detection_id=f"increment-{index}",
            asset_id=f"increment-asset-{index}",
            embedding=detections[index].embedding,
            pet_id=None,
        )
        for index in probe_indices
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
    rss_bytes = _peak_rss_bytes()
    return {
        "seconds": elapsed,
        "wal_delta": max(0, wal_after - wal_before),
        "rss_bytes": rss_bytes,
        "sql": tuple(sql),
    }


def _benchmark_growth(
    root: Path,
    count: int,
    *,
    dimension: int = 4,
    exercise_restart: bool = False,
) -> dict[str, object]:
    root.mkdir(parents=True)
    repository = PetRepository(root / "pet_index.db", root / "pet_state.db")
    sql: list[str] = []
    original_connect = repository._connect

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(sql.append)
        return connection

    repository._connect = traced_connect  # type: ignore[method-assign]
    started = time.perf_counter()
    for start in range(0, count, 16):
        batch = [
            _detection(
                detection_id=f"growth-{index:06d}",
                asset_id=f"growth-asset-{index:06d}",
                embedding=_synthetic_vector(index, dimension),
                pet_id=None,
            )
            for index in range(start, min(start + 16, count))
        ]
        repository.replace_assets_incrementally(
            [detection.asset_id for detection in batch],
            batch,
            distance_threshold=-1.0,
        )
    elapsed = time.perf_counter() - started
    rss_bytes = _peak_rss_bytes()
    full_profile_reads = sum(
        1
        for statement in sql
        if "FROM PETS" in statement.upper()
        and "WHERE PET_ID IN" not in statement.upper()
        and statement.lstrip().upper().startswith("SELECT")
    )
    metrics = {
        "seconds": elapsed,
        "rss_bytes": rss_bytes,
        "full_profile_reads": full_profile_reads,
    }
    if not exercise_restart:
        return metrics

    reopened = PetRepository(root / "pet_index.db", root / "pet_state.db")
    wal_path = Path(f"{reopened.db_path}-wal")
    wal_before = wal_path.stat().st_size if wal_path.exists() else 0
    cold_started = time.perf_counter()
    records_before_restart_match = reopened.get_all_pet_records()
    assert len(records_before_restart_match) >= 2
    match_target = records_before_restart_match[0]
    increment = _detection(
        detection_id="restart-increment",
        asset_id="restart-increment-asset",
        embedding=match_target.center_embedding,
        pet_id=None,
    )
    incremental_result = reopened.replace_assets_incrementally(
        [increment.asset_id],
        [increment],
        distance_threshold=0.05,
    )
    cold_elapsed = time.perf_counter() - cold_started
    # Growth creates one-sample unstable profiles. A restart must not turn one
    # of them into an embedding candidate; only exact pet_key reuse may do so.
    assert len(incremental_result.added_pet_ids) == 1
    assert match_target.pet_id not in incremental_result.updated_pet_ids
    wal_after = wal_path.stat().st_size if wal_path.exists() else 0
    records = reopened.get_all_pet_records()
    assert len(records) >= 2
    mutation_started = time.perf_counter()
    merge = reopened.merge_pets(records[0].pet_id, records[1].pet_id)
    assert merge is not None
    merged_detection = next(
        detection
        for detection in reopened.get_all_detections()
        if detection.pet_id == records[1].pet_id
    )
    assert reopened.delete_detection(merged_detection.detection_id) is not None
    assert reopened.get_detection(merged_detection.detection_id) is None
    assert records[0].pet_id not in {record.pet_id for record in reopened.get_all_pet_records()}
    metrics.update(
        {
            "cold_restart_seconds": cold_elapsed,
            "mutation_seconds": time.perf_counter() - mutation_started,
            "incremental_wal_delta": max(0, wal_after - wal_before),
        }
    )
    return metrics


def _synthetic_snapshot(
    count: int,
    *,
    dimension: int = 4,
) -> tuple[list[PetDetectionRecord], list[PetRecord]]:
    timestamp = utc_now_iso()
    detections: list[PetDetectionRecord] = []
    pets: list[PetRecord] = []
    for index in range(count):
        angle = (index + 1) * 0.0001
        vector = _synthetic_vector(index, dimension, angle=angle)
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
                # Synthetic snapshot profiles model already-confirmed identities
                # so the incremental benchmark exercises the stable ANN path.
                sample_count=3,
                profile_state="stable",
                species_label="dog",
            )
        )
    return detections, pets


def _synthetic_vector(
    index: int,
    dimension: int,
    *,
    angle: float | None = None,
) -> np.ndarray:
    vector = np.zeros(max(4, int(dimension)), dtype=np.float32)
    effective_angle = angle if angle is not None else (index + 1) * 0.0001
    vector[0] = np.cos(effective_angle)
    vector[1] = np.sin(effective_angle)
    vector[2] = float((index % 193) / 193)
    vector[3] = float((index % 389) / 389)
    if dimension > 4:
        vector[4 + (index % (dimension - 4))] = 0.25
    return normalize_vector(vector[:dimension])


def _peak_rss_bytes() -> int:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(counters.PeakWorkingSetSize)

    import resource

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(rss if sys.platform == "darwin" else rss * 1024)


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
