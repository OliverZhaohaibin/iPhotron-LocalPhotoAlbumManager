from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from iPhoto.library.workers.pet_scan_worker import PetScanWorker
from iPhoto.pets.index_coordinator import PetIndexCoordinator
from iPhoto.pets.pipeline import PET_CLUSTERING_PIPELINE_VERSION
from iPhoto.pets.records import PetDetectionRecord, PetRecord
from iPhoto.pets.repository import EmbeddingContract, PetRepository
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
    assert fifty_k["consolidation_seconds"] <= 5.0
    assert fifty_k["consolidation_wal_delta"] <= 10 * 1024 * 1024
    assert fifty_k["consolidation_queue_clean"] is True
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


def test_warm_incremental_commit_uses_bounded_sqlite_connections(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    warm = [
        _detection(
            detection_id="warm",
            asset_id="warm-asset",
            embedding=_synthetic_vector(0, 4),
            pet_id=None,
        )
    ]
    repository.replace_assets_incrementally(
        ["warm-asset"],
        warm,
        distance_threshold=-1.0,
    )
    state_repository = repository.state_repository
    assert state_repository is not None
    connection_counts = {"runtime": 0, "state": 0}
    runtime_connect = repository._connect
    state_connect = state_repository._connect

    def counted_runtime_connect():
        connection_counts["runtime"] += 1
        return runtime_connect()

    def counted_state_connect():
        connection_counts["state"] += 1
        return state_connect()

    repository._connect = counted_runtime_connect  # type: ignore[method-assign]
    state_repository._connect = counted_state_connect  # type: ignore[method-assign]
    batch_count = 10
    for batch_index in range(batch_count):
        opens_before = sum(connection_counts.values())
        first = 1 + batch_index * 16
        batch = [
            _detection(
                detection_id=f"budget-{index:04d}",
                asset_id=f"budget-asset-{index:04d}",
                embedding=_synthetic_vector(index, 4),
                pet_id=None,
            )
            for index in range(first, first + 16)
        ]
        repository.replace_assets_incrementally(
            [detection.asset_id for detection in batch],
            batch,
            distance_threshold=-1.0,
        )
        assert sum(connection_counts.values()) - opens_before <= 4

    assert sum(connection_counts.values()) <= batch_count * 4


def _benchmark_incremental(root: Path, count: int) -> dict[str, object]:
    root.mkdir(parents=True)
    coordinator = PetIndexCoordinator(root)
    repository = coordinator._repository()
    detections, pets = _synthetic_snapshot(count)
    repository.replace_all(detections, pets)
    repository.activate_embedding_generation(
        generation_id=0,
        embedding_pipeline_version=detections[0].embedding_pipeline_version,
        embedding_dimension=detections[0].embedding_dim,
    )
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

    # Queue one isolated species so the worker exercises the real durable drain
    # path without expanding the affected component beyond the single seed.
    cat = _detection(
        detection_id="consolidation-cat",
        asset_id="consolidation-cat-asset",
        embedding=np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
        pet_id=None,
        species_label="cat",
    )
    queued = repository.replace_assets_incrementally(
        [cat.asset_id],
        [cat],
        distance_threshold=0.42,
        clustering_pipeline_target=PET_CLUSTERING_PIPELINE_VERSION,
    )
    assert len(queued.added_pet_ids) == 1
    # The incremental assignment has already warmed and updated the exact ANN
    # context. Consolidation must now stay within the durable queue/component.
    contract = EmbeddingContract.from_detection(cat)
    assert repository._profiles_for_contract(contract).profiles

    def fail_global_path(*_args, **_kwargs):
        raise AssertionError("worker drain used a forbidden full-library path")

    repository.get_all_detections = fail_global_path  # type: ignore[method-assign]
    repository.get_all_pet_records = fail_global_path  # type: ignore[method-assign]
    repository.replace_all = fail_global_path  # type: ignore[method-assign]
    consolidation_wal_before = wal_path.stat().st_size if wal_path.exists() else 0
    worker = PetScanWorker(
        root,
        pet_service=SimpleNamespace(coordinator=coordinator),  # type: ignore[arg-type]
    )
    consolidation_started = time.perf_counter()
    assert worker._consolidate_pending_clustering(SimpleNamespace(distance_threshold=0.42)) is False
    consolidation_elapsed = time.perf_counter() - consolidation_started
    consolidation_wal_after = wal_path.stat().st_size if wal_path.exists() else 0
    consolidation_queue_clean = (
        repository.get_scan_metadata("clustering_pipeline_version")
        == PET_CLUSTERING_PIPELINE_VERSION
        and repository.get_scan_metadata("clustering_consolidation_state") == "clean"
        and not repository.has_pending_clustering_consolidation(
            target_version=PET_CLUSTERING_PIPELINE_VERSION
        )
    )
    wal_after = wal_path.stat().st_size if wal_path.exists() else 0
    rss_bytes = _peak_rss_bytes()
    coordinator.close()
    return {
        "seconds": elapsed,
        "wal_delta": max(0, wal_after - wal_before),
        "consolidation_seconds": consolidation_elapsed,
        "consolidation_wal_delta": max(
            0,
            consolidation_wal_after - consolidation_wal_before,
        ),
        "consolidation_queue_clean": consolidation_queue_clean,
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
    phase_seconds = {
        "assignment_seconds": 0.0,
        "state_sync_seconds": 0.0,
        "index_update_seconds": 0.0,
        "synthetic_seconds": 0.0,
    }
    original_assignment = repository._assign_incremental_pet_ids
    original_state_sync = repository._sync_runtime_state_payload
    original_index_update = repository._update_profile_indexes
    state_repository = repository.state_repository
    original_state_snapshot = (
        state_repository._load_incremental_state if state_repository is not None else None
    )

    def timed_assignment(*args, **kwargs):
        phase_started = time.perf_counter()
        try:
            return original_assignment(*args, **kwargs)
        finally:
            phase_seconds["assignment_seconds"] += time.perf_counter() - phase_started

    def timed_state_snapshot(*args, **kwargs):
        phase_started = time.perf_counter()
        try:
            assert original_state_snapshot is not None
            return original_state_snapshot(*args, **kwargs)
        finally:
            phase_seconds["assignment_seconds"] += time.perf_counter() - phase_started

    def timed_state_sync(*args, **kwargs):
        phase_started = time.perf_counter()
        try:
            return original_state_sync(*args, **kwargs)
        finally:
            phase_seconds["state_sync_seconds"] += time.perf_counter() - phase_started

    def timed_index_update(*args, **kwargs):
        phase_started = time.perf_counter()
        try:
            return original_index_update(*args, **kwargs)
        finally:
            phase_seconds["index_update_seconds"] += time.perf_counter() - phase_started

    repository._assign_incremental_pet_ids = timed_assignment  # type: ignore[method-assign]
    repository._sync_runtime_state_payload = timed_state_sync  # type: ignore[method-assign]
    repository._update_profile_indexes = timed_index_update  # type: ignore[method-assign]
    if state_repository is not None:
        state_repository._load_incremental_state = timed_state_snapshot  # type: ignore[method-assign]
    started = time.perf_counter()
    next_progress = 5_000
    for start in range(0, count, 16):
        synthetic_started = time.perf_counter()
        batch = [
            _detection(
                detection_id=f"growth-{index:06d}",
                asset_id=f"growth-asset-{index:06d}",
                embedding=_synthetic_vector(index, dimension),
                pet_id=None,
            )
            for index in range(start, min(start + 16, count))
        ]
        phase_seconds["synthetic_seconds"] += time.perf_counter() - synthetic_started
        repository.replace_assets_incrementally(
            [detection.asset_id for detection in batch],
            batch,
            distance_threshold=-1.0,
        )
        completed = min(start + 16, count)
        if completed >= next_progress or completed == count:
            print(
                "Pets production-shape progress: "
                f"{completed}/{count} in {time.perf_counter() - started:.2f}s",
                flush=True,
            )
            while next_progress <= completed:
                next_progress += 5_000
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
        **phase_seconds,
        "runtime_mutation_seconds": max(
            0.0,
            elapsed
            - phase_seconds["synthetic_seconds"]
            - phase_seconds["assignment_seconds"]
            - phase_seconds["state_sync_seconds"]
            - phase_seconds["index_update_seconds"],
        ),
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
    species_label: str = "dog",
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
        species_label=species_label,
    )
