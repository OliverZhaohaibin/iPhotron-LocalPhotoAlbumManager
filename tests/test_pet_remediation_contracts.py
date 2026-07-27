from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from iPhoto.bootstrap.library_pet_service import create_pet_service
from iPhoto.cache.index_store import get_global_repository
from iPhoto.people.status import is_face_scan_candidate
from iPhoto.pets import pipeline as pet_pipeline
from iPhoto.pets.pipeline import DetectedAssetPets, PetClusterPipeline, build_pet_key
from iPhoto.pets.index_coordinator import PetIndexCoordinator, PetSnapshotCommittedError
from iPhoto.pets.records import PetDetectionRecord, PetRecord
from iPhoto.pets.repository import PetRepository
from iPhoto.pets.repository_utils import normalize_vector, utc_now_iso
from iPhoto.pets.status import is_pet_scan_candidate
from iPhoto.utils.pathutils import LibraryAssetPathError, resolve_library_asset_path


class _AssetStore:
    def __init__(self, asset_id: str) -> None:
        self.asset_id = asset_id
        self.status = "pending"

    def get_rows_by_ids(self, asset_ids):
        return {
            self.asset_id: {"id": self.asset_id, "pet_status": self.status}
            for asset_id in asset_ids
            if asset_id == self.asset_id
        }

    def read_rows_by_pet_status(self, statuses, *, limit=None):
        del limit
        if self.status in set(statuses):
            yield {"id": self.asset_id, "pet_status": self.status, "media_type": 0}

    def update_pet_status(self, asset_id, status):
        self.update_pet_statuses([asset_id], status)

    def update_pet_statuses(self, asset_ids, status):
        if self.asset_id in set(asset_ids):
            self.status = status

    def reset_pet_statuses_for_pipeline_upgrade(self):
        if self.status != "done":
            return 0
        self.status = "pending"
        return 1

    def count_by_pet_status(self):
        return {self.status: 1}


def test_live_role_integer_parser_is_shared() -> None:
    for value in (1, 1.0, "1"):
        row = {"media_type": 0, "live_role": value, "mime": "image/jpeg"}
        assert not is_pet_scan_candidate(row)
        assert not is_face_scan_candidate(row)


def test_library_asset_path_rejects_traversal_absolute_and_symlink_escape(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    (root / "link.jpg").symlink_to(outside)
    for value in ("../outside.jpg", str(outside), "link.jpg"):
        with pytest.raises(LibraryAssetPathError):
            resolve_library_asset_path(root, value)


def test_pet_key_v2_separates_detector_species() -> None:
    common = {
        "asset_id": "asset-a",
        "bbox": (10, 20, 80, 90),
        "image_width": 400,
        "image_height": 300,
    }
    dog = build_pet_key(**common, species_label="dog")
    cat = build_pet_key(**common, species_label="cat")
    assert dog.startswith("v2:")
    assert dog != cat


def test_v1_key_migration_does_not_copy_old_rejection(tmp_path: Path) -> None:
    index_path = tmp_path / "pet_index.db"
    state_path = tmp_path / "pet_state.db"
    repository = PetRepository(index_path, state_path)
    legacy = replace(
        _detection("legacy", pet_id="pet-old"),
        pet_key="legacy-v1-key",
        pet_key_version="v1",
    )
    repository.replace_all([legacy], [_pet("pet-old", legacy)])
    assert repository.state_repository is not None
    repository.state_repository.add_rejected_pet_key("legacy-v1-key")

    reopened = PetRepository(index_path, state_path)
    detections = reopened.get_all_detections()
    assert len(detections) == 1
    assert detections[0].pet_key.startswith("v2:")
    assert detections[0].pet_key != "legacy-v1-key"


def test_incremental_event_reports_removed_pet_and_journal_finalizes(tmp_path: Path) -> None:
    store = _AssetStore("asset-a")
    service = create_pet_service(tmp_path, asset_repository=store)
    coordinator = service.coordinator
    repository = service.repository()
    assert coordinator is not None and repository is not None

    first = coordinator.submit_detected_batch(
        [DetectedAssetPets("asset-a", "album/a.jpg", [_detection("first")])],
        distance_threshold=0.1,
    )
    assert first is not None and len(first.added_pet_ids) == 1
    pet_id = first.added_pet_ids[0]

    removed = coordinator.submit_detected_batch(
        [DetectedAssetPets("asset-a", "album/a.jpg", [])],
        distance_threshold=0.1,
    )
    assert removed is not None
    assert removed.removed_pet_ids == (pet_id,)
    assert removed.changed_pet_ids == (pet_id,)

    operation_db = tmp_path / ".iPhoto" / "recognition" / "operations.db"
    with sqlite3.connect(operation_db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM operations WHERE state != 'finalized'"
        ).fetchone()[0] == 0


def test_scan_journal_recovers_after_asset_status_commit_failure(tmp_path: Path) -> None:
    class FailingStore(_AssetStore):
        fail_done = True

        def update_pet_statuses(self, asset_ids, status):
            if status == "done" and self.fail_done:
                raise sqlite3.OperationalError("injected asset status failure")
            super().update_pet_statuses(asset_ids, status)

    store = FailingStore("asset-a")
    coordinator = PetIndexCoordinator(tmp_path, asset_repository=store)
    with pytest.raises(PetSnapshotCommittedError):
        coordinator.submit_detected_batch(
            [DetectedAssetPets("asset-a", "album/a.jpg", [_detection("first")])],
            distance_threshold=0.1,
        )
    assert store.status == "pending"
    store.fail_done = False

    recovered = PetIndexCoordinator(tmp_path, asset_repository=store)
    with recovered._lock:
        recovered._recover_operations_locked()
    assert store.status == "done"
    with sqlite3.connect(tmp_path / ".iPhoto" / "recognition" / "operations.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM operations WHERE state != 'finalized'"
        ).fetchone()[0] == 0


def test_failed_asset_keeps_previous_detection_as_explicit_stale_result(
    tmp_path: Path,
) -> None:
    store = _AssetStore("asset-a")
    service = create_pet_service(tmp_path, asset_repository=store)
    coordinator = service.coordinator
    repository = service.repository()
    assert coordinator is not None and repository is not None
    coordinator.submit_detected_batch(
        [DetectedAssetPets("asset-a", "album/a.jpg", [_detection("first")])],
        distance_threshold=0.1,
    )

    event = coordinator.submit_detected_batch(
        [DetectedAssetPets("asset-a", "album/a.jpg", [], error="decode failed")],
        distance_threshold=0.1,
    )
    assert event is not None and event.updated_pet_ids
    detection = repository.get_detection("first")
    assert detection is not None
    assert detection.is_stale
    assert detection.stale_reason == "asset_scan_failed_in_current_generation"
    assert detection.source_generation_id == detection.generation_id


def test_second_bbox_failure_rolls_back_thumbnails_and_metric(
    tmp_path: Path,
    monkeypatch,
) -> None:
    album = tmp_path / "album"
    album.mkdir()
    Image.new("RGB", (400, 300), color=(20, 30, 40)).save(album / "a.jpg")
    pipeline = PetClusterPipeline(
        model_root=tmp_path / "models",
        allow_model_download=False,
    )
    pipeline._detector = SimpleNamespace(
        detect=lambda _image: [
            SimpleNamespace(bbox=(10, 20, 100, 100), confidence=0.9, species_label="dog"),
            SimpleNamespace(bbox=(200, 20, 100, 100), confidence=0.8, species_label="cat"),
        ]
    )
    pipeline._embedder = SimpleNamespace(
        embed=lambda _image: normalize_vector(np.asarray([1.0, 0.0], dtype=np.float32))
    )
    calls = 0

    def save_then_fail(image, bbox, output_path, *, padding_ratio):
        del image, bbox, padding_ratio
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second thumbnail failed")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"thumbnail")

    monkeypatch.setattr(pet_pipeline, "save_pet_thumbnail", save_then_fail)
    thumbnail_dir = tmp_path / ".iPhoto" / "pets" / "thumbnails" / ".staging" / "op"
    results = pipeline.detect_pets_for_rows(
        [{"id": "asset-a", "rel": "album/a.jpg"}],
        library_root=tmp_path,
        thumbnail_dir=thumbnail_dir,
    )

    assert results[0].error == "second thumbnail failed"
    assert not list(thumbnail_dir.glob("*.png"))
    assert pipeline.last_scan_metrics.accepted_detections == 0


def test_embedding_dimension_change_allocates_new_generation(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db")
    old = _detection("old", embedding=np.asarray([1.0, 0.0, 0.0]), pet_id="pet-old")
    old_pet = _pet("pet-old", old)
    repository.replace_all([old], [old_pet])
    repository.activate_embedding_generation(
        generation_id=0,
        embedding_pipeline_version=old.embedding_pipeline_version,
        embedding_dimension=3,
    )
    new = replace(
        _detection("new", asset_id="asset-new", embedding=np.asarray([1.0, 0.0])),
        embedding_pipeline_version="embedding-v2",
    )

    assigned, generation_id = repository.assign_embedding_generation([new])
    assert generation_id == 1
    assert assigned[0].generation_id == 1
    result = repository.replace_assets_incrementally(
        ["asset-new"],
        assigned,
        distance_threshold=0.1,
    )
    assert result.added_pet_ids
    pets = repository.get_all_pet_records()
    assert {(pet.generation_id, pet.embedding_dim) for pet in pets} == {(0, 3), (1, 2)}


def test_unknown_mutations_and_cross_pet_cover_are_rejected(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    first = _detection("first", asset_id="asset-a", pet_id="pet-a")
    second = _detection("second", asset_id="asset-b", pet_id="pet-b")
    repository.replace_all([first, second], [_pet("pet-a", first), _pet("pet-b", second)])

    assert not repository.rename_pet("missing", "Ghost")
    assert not repository.set_pet_hidden("missing", True)
    assert not repository.set_pet_cover("pet-a", "second")
    assert repository.set_pet_cover("pet-a", "first")


def test_dashboard_query_budget_is_constant_from_10_to_1000_pets(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")

    def load(count: int) -> int:
        detections = [
            _detection(
                f"detection-{index}",
                asset_id=f"asset-{index}",
                pet_id=f"pet-{index}",
            )
            for index in range(count)
        ]
        repository.replace_all(
            detections,
            [_pet(f"pet-{index}", detection) for index, detection in enumerate(detections)],
        )
        statements: list[str] = []
        original_runtime_connect = repository._connect
        state = repository.state_repository
        assert state is not None
        original_state_connect = state._connect

        def traced(connect):
            connection = connect()
            connection.set_trace_callback(
                lambda sql: statements.append(sql)
                if sql.lstrip().upper().startswith(("SELECT", "WITH"))
                else None
            )
            return connection

        repository._connect = lambda: traced(original_runtime_connect)  # type: ignore[method-assign]
        state._connect = lambda: traced(original_state_connect)  # type: ignore[method-assign]
        try:
            assert len(repository.get_pet_summaries(include_hidden=True)) == count
        finally:
            repository._connect = original_runtime_connect  # type: ignore[method-assign]
            state._connect = original_state_connect  # type: ignore[method-assign]
        return len(statements)

    ten_queries = load(10)
    thousand_queries = load(1000)
    assert thousand_queries == ten_queries


def test_pipeline_reset_and_bulk_status_update_handle_more_than_sql_limit(
    tmp_path: Path,
) -> None:
    repository = get_global_repository(tmp_path)
    rows = [
        {
            "id": f"asset-{index}",
            "rel": f"album/{index}.jpg",
            "media_type": 0,
            "mime": "image/jpeg",
            "live_role": 0,
            "pet_status": "done",
            "face_status": "done",
        }
        for index in range(1205)
    ]
    rows[-1]["live_role"] = "1"
    rows[-2]["media_type"] = 1
    rows[-2]["mime"] = "video/mp4"
    repository.write_rows(rows)

    assert repository.reset_pet_statuses_for_pipeline_upgrade() == 1203
    assert repository.reset_face_statuses_for_pipeline_upgrade() == 1203
    ids = [f"asset-{index}" for index in range(1203)]
    repository.update_pet_statuses(ids, "done")
    repository.update_face_statuses(ids, "done")
    assert repository.count_by_pet_status()["done"] == 1205
    assert repository.count_by_face_status()["done"] == 1205


def _detection(
    detection_id: str,
    *,
    asset_id: str = "asset-a",
    embedding: np.ndarray | None = None,
    pet_id: str | None = None,
) -> PetDetectionRecord:
    vector = normalize_vector(
        embedding
        if embedding is not None
        else np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    )
    return PetDetectionRecord(
        detection_id=detection_id,
        pet_key=f"v2:{detection_id}",
        asset_id=asset_id,
        asset_rel=f"album/{asset_id}.jpg",
        box_x=10,
        box_y=20,
        box_w=80,
        box_h=90,
        confidence=0.9,
        embedding=vector,
        embedding_dim=int(vector.size),
        embedding_model="dinov2_vits14",
        detector_model="yolox_nano_coco",
        thumbnail_path=None,
        pet_id=pet_id,
        detected_at=utc_now_iso(),
        image_width=400,
        image_height=300,
        species_label="dog",
    )


def _pet(pet_id: str, detection: PetDetectionRecord) -> PetRecord:
    timestamp = utc_now_iso()
    return PetRecord(
        pet_id=pet_id,
        name=None,
        key_detection_id=detection.detection_id,
        detection_count=1,
        center_embedding=detection.embedding,
        embedding_dim=detection.embedding_dim,
        created_at=timestamp,
        updated_at=timestamp,
        sample_count=1,
        species_label=detection.species_label,
        embedding_pipeline_version=detection.embedding_pipeline_version,
        generation_id=detection.generation_id,
    )
