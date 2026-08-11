from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from iPhoto.bootstrap.library_pet_service import create_pet_service
from iPhoto.cache.index_store import get_global_repository
from iPhoto.gui.ui.widgets.recognition_annotations import pet_annotation_adapter
from iPhoto.people.repository import FaceRepository
from iPhoto.people.status import is_face_scan_candidate
from iPhoto.pets import pipeline as pet_pipeline
from iPhoto.pets import repository as pet_repository
from iPhoto.pets.index_coordinator import PetIndexCoordinator, PetSnapshotCommittedError
from iPhoto.pets.pipeline import DetectedAssetPets, PetClusterPipeline, build_pet_key
from iPhoto.pets.records import (
    AssetPetAnnotation,
    PetDetectionRecord,
    PetMutationFailure,
    PetRecord,
)
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


class _AssetStatusStore:
    def __init__(self, *asset_ids: str) -> None:
        self.statuses = {asset_id: "done" for asset_id in asset_ids}
        self.fail_done = False

    def update_pet_statuses(self, asset_ids, status):
        if status == "done" and self.fail_done:
            raise sqlite3.OperationalError("injected done-status failure")
        for asset_id in asset_ids:
            if asset_id in self.statuses:
                self.statuses[asset_id] = status


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
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM operations WHERE state != 'finalized'"
            ).fetchone()[0]
            == 0
        )


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
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM operations WHERE state != 'finalized'"
            ).fetchone()[0]
            == 0
        )


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


def test_terminal_failed_status_is_journaled_and_recovered_without_retry_downgrade(
    tmp_path: Path,
) -> None:
    class FailingStore(_AssetStore):
        fail_failed = True

        def update_pet_statuses(self, asset_ids, status):
            if status == "failed" and self.fail_failed:
                raise sqlite3.OperationalError("injected failed-status write")
            super().update_pet_statuses(asset_ids, status)

    store = FailingStore("asset-a")
    coordinator = PetIndexCoordinator(tmp_path, asset_repository=store)
    with pytest.raises(PetSnapshotCommittedError):
        coordinator.submit_detected_batch(
            [DetectedAssetPets("asset-a", "album/a.jpg", [], error="bad image")],
            failed_asset_ids=["asset-a"],
            distance_threshold=0.1,
        )
    assert store.status == "pending"
    operation = coordinator._journal.unfinished()[0]
    assert operation.payload["done_asset_ids"] == []
    assert operation.payload["retry_asset_ids"] == []
    assert operation.payload["failed_asset_ids"] == ["asset-a"]

    store.fail_failed = False
    recovered = PetIndexCoordinator(tmp_path, asset_repository=store)
    with recovered._lock:
        recovered._recover_operations_locked()

    assert store.status == "failed"
    assert recovered._journal.unfinished() == ()


def test_stale_annotation_adapter_keeps_canonical_name_pure() -> None:
    annotation = AssetPetAnnotation(
        detection_id="det-stale",
        pet_id="pet-stale",
        display_name="Milo",
        box_x=1,
        box_y=2,
        box_w=3,
        box_h=4,
        image_width=100,
        image_height=100,
        canonical_display_name="Milo",
        is_stale=True,
    )

    adapted = pet_annotation_adapter(annotation)

    assert adapted.canonical_display_name == "Milo"
    assert adapted.is_stale is True


@pytest.mark.parametrize("include_done_asset", [False, True])
def test_pet_batch_cannot_mutate_before_global_journal_ownership(
    tmp_path: Path,
    monkeypatch,
    include_done_asset: bool,
) -> None:
    store = _AssetStore("asset-a")
    coordinator = PetIndexCoordinator(tmp_path, asset_repository=store)
    repository = coordinator._repository()
    coordinator.submit_detected_batch(
        [DetectedAssetPets("asset-a", "album/a.jpg", [_detection("first")])],
        distance_threshold=0.1,
    )
    before = repository.get_detection("first")
    assert before is not None and before.is_stale is False
    assert store.status == "done"

    foreign_operation_id: str | None = None

    def lose_global_ownership(_kind, _payload):
        nonlocal foreign_operation_id
        foreign_operation_id = coordinator._journal.prepare(
            "people_scan_commit",
            {"done_asset_ids": [], "retry_asset_ids": []},
        )
        return None

    monkeypatch.setattr(
        coordinator,
        "_try_prepare_operation_locked",
        lose_global_ownership,
    )

    batch = [DetectedAssetPets("asset-a", "album/a.jpg", [], error="decode failed")]
    if include_done_asset:
        batch.append(
            DetectedAssetPets(
                "asset-b",
                "album/b.jpg",
                [_detection("unowned-new", asset_id="asset-b")],
            )
        )
    event = coordinator.submit_detected_batch(
        batch,
        distance_threshold=0.1,
    )

    after = repository.get_detection("first")
    assert event is None
    assert after is not None and after.is_stale is False
    assert repository.get_detection("unowned-new") is None
    assert store.status == "done"
    assert foreign_operation_id is not None
    assert [item.operation_id for item in coordinator._journal.unfinished()] == [
        foreign_operation_id
    ]


def test_pet_merge_reports_shutdown_as_temporary_failure(tmp_path: Path) -> None:
    coordinator = PetIndexCoordinator(tmp_path)
    coordinator.begin_shutdown()

    outcome = coordinator.merge_pets("pet-a", "pet-b")

    assert outcome.merged is False
    assert outcome.failure is PetMutationFailure.SHUTTING_DOWN


def test_pet_merge_reports_business_rejection(tmp_path: Path) -> None:
    coordinator = PetIndexCoordinator(tmp_path)

    outcome = coordinator.merge_pets("missing-source", "missing-target")

    assert outcome.merged is False
    assert outcome.failure is PetMutationFailure.REJECTED


def test_pet_merge_reports_busy_journal_as_recovery_pending(tmp_path: Path) -> None:
    coordinator = PetIndexCoordinator(tmp_path)
    operation_id = coordinator._journal.prepare("future-operation", {})
    coordinator._journal.transition(operation_id, "applying")

    outcome = coordinator.merge_pets("pet-a", "pet-b")

    assert outcome.merged is False
    assert outcome.failure is PetMutationFailure.RECOVERY_PENDING


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
                lambda sql: (
                    statements.append(sql)
                    if sql.lstrip().upper().startswith(("SELECT", "WITH"))
                    else None
                )
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


def test_runtime_commit_marker_recovers_state_without_deleting_published_thumbnail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = _AssetStore("asset-a")
    coordinator = PetIndexCoordinator(tmp_path, asset_repository=store)
    repository = coordinator._repository()
    state = repository.state_repository
    assert state is not None
    staged_dir = tmp_path / ".iPhoto" / "pets" / "thumbnails" / ".staging" / "op"
    published_dir = tmp_path / ".iPhoto" / "pets" / "thumbnails"
    staged_dir.mkdir(parents=True)
    (staged_dir / "first.png").write_bytes(b"thumbnail")
    detection = replace(
        _detection("first"),
        thumbnail_path="thumbnails/first.png",
    )

    def fail_state_sync(*args, **kwargs):
        del args, kwargs
        raise sqlite3.OperationalError("injected state failure")

    monkeypatch.setattr(state, "sync_scan_results", fail_state_sync)
    with pytest.raises(PetSnapshotCommittedError):
        coordinator.submit_detected_batch(
            [DetectedAssetPets("asset-a", "album/a.jpg", [detection])],
            distance_threshold=0.1,
            staged_thumbnail_dir=staged_dir,
            published_thumbnail_dir=published_dir,
        )

    committed = repository.get_all_detections()
    assert [item.detection_id for item in committed] == ["first"]
    assert (published_dir / "first.png").is_file()
    with sqlite3.connect(repository.db_path) as connection:
        assert connection.execute("SELECT state_synced FROM pet_runtime_commits").fetchone()[0] == 0

    recovered = PetIndexCoordinator(tmp_path, asset_repository=store)
    recovered_repository = recovered._repository()
    assert recovered_repository.state_repository is not None
    assert recovered_repository.state_repository.get_profiles()
    assert (published_dir / "first.png").is_file()
    with sqlite3.connect(repository.db_path) as connection:
        assert connection.execute("SELECT state_synced FROM pet_runtime_commits").fetchone()[0] == 1


def test_runtime_commit_cleanup_keeps_unsynced_and_protected_rows(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    repository.initialize()
    with sqlite3.connect(repository.db_path) as conn:
        conn.executemany(
            """
            INSERT INTO pet_runtime_commits (
                operation_id, payload_json, state_synced, created_at, updated_at
            ) VALUES (?, '{}', 1, '2026-01-01', '2026-01-01')
            """,
            [(f"done-{index:04d}",) for index in range(1201)],
        )
        conn.execute(
            """
            INSERT INTO pet_runtime_commits
                (operation_id, payload_json, state_synced, created_at, updated_at)
            VALUES ('pending', '{}', 0, '2026-01-01', '2026-01-01')
            """
        )
        conn.execute(
            """
            INSERT INTO pet_runtime_commits
                (operation_id, payload_json, state_synced, created_at, updated_at)
            VALUES ('protected', '{}', 1, '2026-01-01', '2026-01-01')
            """
        )

    deleted = repository.prune_runtime_commits(
        protected_operation_ids=("protected",),
    )

    assert deleted == 201
    with sqlite3.connect(repository.db_path) as conn:
        rows = conn.execute("SELECT operation_id, state_synced FROM pet_runtime_commits").fetchall()
    assert len(rows) == 1002
    assert ("pending", 0) in rows
    assert ("protected", 1) in rows


def test_exact_key_resurrects_durable_identity_and_user_state(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    first = _detection("stable-key", pet_id="pet-a")
    repository.replace_all([first], [_pet("pet-a", first)])
    assert repository.rename_pet("pet-a", "Miso")
    assert repository.set_pet_hidden("pet-a", True)
    assert repository.set_pet_cover("pet-a", "stable-key")

    repository.replace_assets_incrementally(
        ["asset-a"],
        [],
        distance_threshold=0.1,
    )
    returned = replace(
        _detection("returned", pet_id=None),
        pet_key=first.pet_key,
    )
    repository.replace_assets_incrementally(
        ["asset-a"],
        [returned],
        distance_threshold=0.1,
    )

    restored = repository.get_detection("returned")
    assert restored is not None and restored.pet_id == "pet-a"
    summaries = repository.get_pet_summaries(include_hidden=True)
    assert [(item.pet_id, item.name, item.is_hidden) for item in summaries] == [
        ("pet-a", "Miso", True)
    ]
    assert repository.state_repository is not None
    cover = repository.state_repository.get_cover("pet-a")
    assert cover is not None
    assert cover.detection_id == "returned"


def test_cross_generation_exact_key_atomically_switches_runtime_contract(
    tmp_path: Path,
) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    first = _detection("old-a", asset_id="asset-a", pet_id="pet-a")
    second = _detection("old-b", asset_id="asset-b", pet_id="pet-a")
    old_pet = replace(
        _pet("pet-a", first),
        detection_count=2,
        sample_count=2,
        profile_state="stable",
    )
    repository.replace_all([first, second], [old_pet])
    assert repository.rename_pet("pet-a", "Miso")
    assert repository.set_pet_hidden("pet-a", True)
    assert repository.set_pet_cover("pet-a", "old-b")

    incoming = replace(
        _detection(
            "new-a",
            asset_id="asset-a",
            embedding=np.asarray([1.0, 0.0]),
        ),
        pet_key=first.pet_key,
        embedding_pipeline_version="embedding-v2",
        generation_id=1,
    )
    result = repository.replace_assets_incrementally(
        ["asset-a"],
        [incoming],
        distance_threshold=0.1,
    )

    runtime = repository.get_all_detections()
    assert [(item.detection_id, item.pet_id) for item in runtime] == [("new-a", "pet-a")]
    assert {
        (item.embedding_pipeline_version, item.embedding_dim, item.generation_id)
        for item in runtime
    } == {("embedding-v2", 2, 1)}
    assert result.changed_asset_ids == ("asset-a", "asset-b")
    assert result.retired_asset_ids == ("asset-b",)
    assert result.added_pet_ids == ()
    assert result.updated_pet_ids == ("pet-a",)
    summaries = repository.get_pet_summaries(include_hidden=True)
    assert [(item.pet_id, item.name, item.is_hidden) for item in summaries] == [
        ("pet-a", "Miso", True)
    ]
    assert repository.state_repository is not None
    cover = repository.state_repository.get_cover("pet-a")
    assert cover is not None and cover.is_custom
    assert cover.detection_id == "old-b"


def test_cross_generation_exact_key_anchor_absorbs_compatible_same_batch_detection(
    tmp_path: Path,
) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    first = _detection("old-a", asset_id="asset-a", pet_id="pet-a")
    second = _detection("old-b", asset_id="asset-b", pet_id="pet-a")
    repository.replace_all(
        [first, second],
        [
            replace(
                _pet("pet-a", first),
                detection_count=2,
                sample_count=2,
                profile_state="stable",
            )
        ],
    )
    anchored = replace(
        _detection("new-a", asset_id="asset-a", embedding=np.asarray([1.0, 0.0])),
        pet_key=first.pet_key,
        embedding_pipeline_version="embedding-v2",
        generation_id=1,
    )
    moved_box = replace(
        _detection("new-b", asset_id="asset-b", embedding=np.asarray([0.999, 0.001])),
        embedding_pipeline_version="embedding-v2",
        generation_id=1,
    )

    repository.replace_assets_incrementally(
        ["asset-a", "asset-b"],
        [anchored, moved_box],
        distance_threshold=0.05,
    )

    assert {(item.detection_id, item.pet_id) for item in repository.get_all_detections()} == {
        ("new-a", "pet-a"),
        ("new-b", "pet-a"),
    }
    rebuilt = repository.get_all_pet_records()
    assert [(pet.pet_id, pet.detection_count, pet.generation_id) for pet in rebuilt] == [
        ("pet-a", 2, 1)
    ]


def test_cross_generation_anchor_absorbs_changed_key_in_later_batch_after_restart(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "pet_index.db"
    state_path = tmp_path / "pet_state.db"
    repository = PetRepository(index_path, state_path)
    first = _detection("old-a", asset_id="asset-a", pet_id="pet-a")
    second = _detection("old-b", asset_id="asset-b", pet_id="pet-a")
    repository.replace_all(
        [first, second],
        [
            replace(
                _pet("pet-a", first),
                detection_count=2,
                sample_count=2,
                profile_state="stable",
            )
        ],
    )
    anchored = replace(
        _detection("new-a", asset_id="asset-a", embedding=np.asarray([1.0, 0.0])),
        pet_key=first.pet_key,
        embedding_pipeline_version="embedding-v2",
        generation_id=1,
    )
    repository.replace_assets_incrementally(
        ["asset-a"],
        [anchored],
        distance_threshold=0.05,
    )

    reopened = PetRepository(index_path, state_path)
    unrelated = replace(
        _detection("new-c", asset_id="asset-c", embedding=np.asarray([0.999, 0.001])),
        embedding_pipeline_version="embedding-v2",
        generation_id=1,
    )
    reopened.replace_assets_incrementally(
        ["asset-c"],
        [unrelated],
        distance_threshold=0.05,
    )
    assert reopened.get_detection("new-c").pet_id != "pet-a"  # type: ignore[union-attr]

    moved_box = replace(
        _detection("new-b", asset_id="asset-b", embedding=np.asarray([0.999, 0.001])),
        embedding_pipeline_version="embedding-v2",
        generation_id=1,
    )
    reopened.replace_assets_incrementally(
        ["asset-b"],
        [moved_box],
        distance_threshold=0.05,
    )

    assert reopened.get_detection("new-b").pet_id == "pet-a"  # type: ignore[union-attr]
    with sqlite3.connect(index_path) as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM pet_contract_migration_assets"
        ).fetchone()
    assert remaining == (0,)


def test_contract_migration_assets_follow_pet_merge(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    first = _detection("old-a", asset_id="asset-a", pet_id="pet-a")
    second = _detection("old-b", asset_id="asset-b", pet_id="pet-a")
    target = replace(
        _detection(
            "target",
            asset_id="asset-c",
            embedding=np.asarray([1.0, 0.0]),
            pet_id="pet-b",
        ),
        embedding_pipeline_version="embedding-v2",
        generation_id=1,
    )
    repository.replace_all(
        [first, second, target],
        [
            replace(_pet("pet-a", first), detection_count=2, sample_count=2),
            _pet("pet-b", target),
        ],
    )
    anchored = replace(
        _detection("new-a", asset_id="asset-a", embedding=np.asarray([1.0, 0.0])),
        pet_key=first.pet_key,
        embedding_pipeline_version="embedding-v2",
        generation_id=1,
    )
    repository.replace_assets_incrementally(
        ["asset-a"],
        [anchored],
        distance_threshold=0.05,
    )

    assert repository.merge_pets("pet-a", "pet-b") is not None
    with sqlite3.connect(repository.db_path) as connection:
        rows = connection.execute(
            """
            SELECT pet_id, asset_id
            FROM pet_contract_migration_assets
            """
        ).fetchall()
    assert rows == [("pet-b", "asset-b")]


def test_unfinished_migration_assets_advance_to_next_contract(tmp_path: Path) -> None:
    index_path = tmp_path / "pet_index.db"
    state_path = tmp_path / "pet_state.db"
    repository = PetRepository(index_path, state_path)
    first = _detection("old-a", asset_id="asset-a", pet_id="pet-a")
    second = _detection("old-b", asset_id="asset-b", pet_id="pet-a")
    repository.replace_all(
        [first, second],
        [replace(_pet("pet-a", first), detection_count=2, sample_count=2)],
    )
    v2_anchor = replace(
        _detection("v2-a", asset_id="asset-a", embedding=np.asarray([1.0, 0.0])),
        pet_key=first.pet_key,
        embedding_pipeline_version="embedding-v2",
        generation_id=1,
    )
    repository.replace_assets_incrementally(
        ["asset-a"],
        [v2_anchor],
        distance_threshold=0.05,
    )
    v3_anchor = replace(
        _detection("v3-a", asset_id="asset-a", embedding=np.asarray([1.0, 0.0])),
        pet_key=first.pet_key,
        embedding_pipeline_version="embedding-v3",
        generation_id=2,
    )
    repository.replace_assets_incrementally(
        ["asset-a"],
        [v3_anchor],
        distance_threshold=0.05,
    )

    with sqlite3.connect(index_path) as connection:
        rows = connection.execute(
            """
            SELECT asset_id, embedding_pipeline_version, generation_id
            FROM pet_contract_migration_assets
            """
        ).fetchall()
    assert rows == [("asset-b", "embedding-v3", 2)]

    reopened = PetRepository(index_path, state_path)
    moved_box = replace(
        _detection("v3-b", asset_id="asset-b", embedding=np.asarray([0.999, 0.001])),
        embedding_pipeline_version="embedding-v3",
        generation_id=2,
    )
    reopened.replace_assets_incrementally(
        ["asset-b"],
        [moved_box],
        distance_threshold=0.05,
    )
    assert reopened.get_detection("v3-b").pet_id == "pet-a"  # type: ignore[union-attr]


def test_contract_retirement_marks_unscanned_assets_pending(tmp_path: Path) -> None:
    store = _AssetStatusStore("asset-a", "asset-b")
    coordinator = PetIndexCoordinator(tmp_path, asset_repository=store)  # type: ignore[arg-type]
    repository = coordinator._repository()
    first = _detection("old-a", asset_id="asset-a", pet_id="pet-a")
    second = _detection("old-b", asset_id="asset-b", pet_id="pet-a")
    repository.replace_all(
        [first, second],
        [replace(_pet("pet-a", first), detection_count=2, sample_count=2)],
    )
    incoming = replace(
        _detection("new-a", asset_id="asset-a", embedding=np.asarray([1.0, 0.0])),
        pet_key=first.pet_key,
        embedding_pipeline_version="embedding-v2",
    )

    event = coordinator.submit_detected_batch(
        [DetectedAssetPets("asset-a", "album/asset-a.jpg", [incoming])],
        distance_threshold=0.1,
    )

    assert event is not None
    assert event.changed_asset_ids == ("asset-a", "asset-b")
    assert store.statuses == {"asset-a": "done", "asset-b": "pending"}
    assert repository.get_detections_by_asset_ids(["asset-b"]) == []


def test_contract_retirement_recovery_uses_full_assets_and_restores_pending_status(
    tmp_path: Path,
) -> None:
    store = _AssetStatusStore("asset-a", "asset-b")
    store.fail_done = True
    coordinator = PetIndexCoordinator(tmp_path, asset_repository=store)  # type: ignore[arg-type]
    repository = coordinator._repository()
    first = _detection("old-a", asset_id="asset-a", pet_id="pet-a")
    second = _detection("old-b", asset_id="asset-b", pet_id="pet-a")
    repository.replace_all(
        [first, second],
        [replace(_pet("pet-a", first), detection_count=2, sample_count=2)],
    )
    incoming = replace(
        _detection("new-a", asset_id="asset-a", embedding=np.asarray([1.0, 0.0])),
        pet_key=first.pet_key,
        embedding_pipeline_version="embedding-v2",
    )

    with pytest.raises(PetSnapshotCommittedError):
        coordinator.submit_detected_batch(
            [DetectedAssetPets("asset-a", "album/asset-a.jpg", [incoming])],
            distance_threshold=0.1,
        )
    operation = coordinator._journal.unfinished()[0]
    assert operation.payload["changed_asset_ids"] == ["asset-a", "asset-b"]
    assert operation.payload["retired_asset_ids"] == ["asset-b"]

    store.fail_done = False
    recovered = PetIndexCoordinator(tmp_path, asset_repository=store)  # type: ignore[arg-type]
    with recovered._lock:
        recovered._recover_operations_locked()

    assert store.statuses == {"asset-a": "done", "asset-b": "pending"}
    operation_db = tmp_path / ".iPhoto" / "recognition" / "operations.db"
    with sqlite3.connect(operation_db) as connection:
        event_payload = json.loads(
            connection.execute(
                "SELECT event_json FROM event_outbox WHERE operation_id = ?",
                (operation.operation_id,),
            ).fetchone()[0]
        )
    assert event_payload["changed_asset_ids"] == ["asset-a", "asset-b"]


def test_cross_generation_contract_switch_rolls_back_old_runtime_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    first = _detection("old-a", asset_id="asset-a", pet_id="pet-a")
    second = _detection("old-b", asset_id="asset-b", pet_id="pet-a")
    repository.replace_all(
        [first, second],
        [replace(_pet("pet-a", first), detection_count=2, sample_count=2)],
    )
    assert repository.rename_pet("pet-a", "Miso")
    incoming = replace(
        _detection("new-a", asset_id="asset-a", embedding=np.asarray([1.0, 0.0])),
        pet_key=first.pet_key,
        embedding_pipeline_version="embedding-v2",
        generation_id=1,
    )

    def fail_rebuild(*_args, **_kwargs):
        raise RuntimeError("injected rebuild failure")

    monkeypatch.setattr(pet_pipeline, "build_pet_records_from_detections", fail_rebuild)
    with pytest.raises(RuntimeError, match="injected rebuild failure"):
        repository.replace_assets_incrementally(
            ["asset-a"],
            [incoming],
            distance_threshold=0.1,
        )

    assert {item.detection_id for item in repository.get_all_detections()} == {
        "old-a",
        "old-b",
    }
    assert repository.state_repository is not None
    profile = repository.state_repository.get_profile("pet-a")
    assert profile is not None
    assert profile.name == "Miso"
    assert profile.generation_id == 0


def test_generation_contract_rejects_cross_space_merge_and_move(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    first = _detection("first", pet_id="pet-a")
    second = replace(
        _detection(
            "second",
            asset_id="asset-b",
            embedding=np.asarray([1.0, 0.0]),
            pet_id="pet-b",
        ),
        embedding_pipeline_version="embedding-v2",
        generation_id=1,
    )
    repository.replace_all([first, second], [_pet("pet-a", first), _pet("pet-b", second)])

    assert repository.merge_pets("pet-a", "pet-b") is None
    assert repository.move_detection_to_pet("first", "pet-b") is None


def test_cross_species_move_is_rejected_and_journal_is_finalized(tmp_path: Path) -> None:
    repository = PetRepository(
        tmp_path / ".iPhoto" / "pets" / "pet_index.db",
        tmp_path / ".iPhoto" / "pets" / "pet_state.db",
    )
    cat = replace(_detection("cat", pet_id="pet-cat"), species_label="cat")
    dog = _detection("dog", asset_id="asset-dog", pet_id="pet-dog")
    repository.replace_all([cat, dog], [_pet("pet-cat", cat), _pet("pet-dog", dog)])
    coordinator = PetIndexCoordinator(tmp_path)
    coordinator._repository = lambda: repository  # type: ignore[method-assign]

    assert coordinator.move_detection_to_pet("cat", "pet-dog") is None
    assert repository.get_detection("cat").pet_id == "pet-cat"  # type: ignore[union-attr]
    assert repository.get_detection("dog").pet_id == "pet-dog"  # type: ignore[union-attr]
    assert {pet.pet_id for pet in repository.get_all_pet_records()} == {
        "pet-cat",
        "pet-dog",
    }
    assert coordinator._journal.unfinished() == ()


def test_profile_candidate_index_has_strict_species_parity_with_usearch(
    monkeypatch,
) -> None:
    pytest.importorskip("usearch.index")
    vector = normalize_vector(np.asarray([1.0, 0.0, 0.0]))
    centers = {
        "pet-cat": vector,
        "pet-dog": vector,
        "pet-unknown": vector,
    }
    species = {"pet-cat": "cat", "pet-dog": "dog", "pet-unknown": None}
    accelerated = pet_repository._ProfileCandidateIndex(dict(centers), dict(species))

    with monkeypatch.context() as context:
        context.setitem(sys.modules, "usearch", None)
        context.setitem(sys.modules, "usearch.index", None)
        fallback = pet_repository._ProfileCandidateIndex(dict(centers), dict(species))

    expected = {
        "cat": [(0.0, "pet-cat")],
        "dog": [(0.0, "pet-dog")],
        None: [(0.0, "pet-unknown")],
    }
    for species_label, matches in expected.items():
        accelerated_matches = accelerated.search(
            vector,
            species_label=species_label,
            limit=8,
        )
        fallback_matches = fallback.search(
            vector,
            species_label=species_label,
            limit=8,
        )
        assert [pet_id for _distance, pet_id in accelerated_matches] == [
            pet_id for _distance, pet_id in matches
        ]
        assert [pet_id for _distance, pet_id in fallback_matches] == [
            pet_id for _distance, pet_id in matches
        ]


def test_generation_contract_is_reused_and_activated_in_one_transaction(
    tmp_path: Path,
) -> None:
    repository = PetRepository(tmp_path / "pet_index.db")
    detection = replace(
        _detection("new"),
        embedding_pipeline_version="embedding-v2",
    )
    first, first_generation = repository.assign_embedding_generation([detection])
    second, second_generation = repository.assign_embedding_generation([detection])
    assert first_generation == second_generation
    assert first[0].generation_id == second[0].generation_id
    repository.set_scan_metadata("clustering_pipeline_version", "cluster-v1")

    repository.activate_embedding_generation(
        generation_id=first_generation,
        embedding_pipeline_version="embedding-v2",
        embedding_dimension=detection.embedding_dim,
        detector_pipeline_version="detector-v5",
    )
    with sqlite3.connect(repository.db_path) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM scan_metadata"))
        active = connection.execute(
            """
            SELECT pipeline_version, embedding_dimension, status
            FROM embedding_generations WHERE generation_id = ?
            """,
            (first_generation,),
        ).fetchone()
    assert metadata["active_generation_id"] == str(first_generation)
    assert metadata["active_embedding_pipeline_version"] == "embedding-v2"
    assert metadata["active_embedding_dimension"] == str(detection.embedding_dim)
    assert metadata["detector_pipeline_version"] == "detector-v5"
    assert metadata["clustering_pipeline_version"] == "cluster-v1"
    assert active == ("embedding-v2", detection.embedding_dim, "active")


def test_unstable_profile_only_reuses_identity_by_exact_pet_key(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    first = _detection("first", pet_id="pet-a")
    repository.replace_all([first], [_pet("pet-a", first)])

    similar = _detection(
        "similar",
        asset_id="asset-b",
        embedding=np.asarray([0.999, 0.001, 0.0]),
    )
    result = repository.replace_assets_incrementally(
        ["asset-b"],
        [similar],
        distance_threshold=0.1,
    )
    assert len(result.added_pet_ids) == 1
    assert repository.get_detection("similar").pet_id != "pet-a"  # type: ignore[union-attr]

    exact_key = replace(
        _detection("same-key", asset_id="asset-c"),
        pet_key=first.pet_key,
    )
    repository.replace_assets_incrementally(
        ["asset-c"],
        [exact_key],
        distance_threshold=0.1,
    )
    assert repository.get_detection("same-key").pet_id == "pet-a"  # type: ignore[union-attr]


def test_stable_profile_match_validates_full_cluster_diameter(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    close = [
        _detection(f"close-{index}", asset_id=f"asset-{index}", pet_id="pet-a")
        for index in range(8)
    ]
    distant = _detection(
        "distant",
        asset_id="asset-distant",
        embedding=np.asarray([0.7, np.sqrt(0.51), 0.0]),
        pet_id="pet-a",
    )
    members = [*close, distant]
    center = normalize_vector(np.mean([member.embedding for member in members], axis=0))
    stable = replace(
        _pet("pet-a", close[0]),
        detection_count=len(members),
        sample_count=len(members),
        profile_state="stable",
        center_embedding=center,
        boundary_embeddings=tuple(member.embedding for member in close),
    )
    repository.replace_all(members, [stable])

    candidate = _detection("candidate", asset_id="asset-candidate")
    result = repository.replace_assets_incrementally(
        [candidate.asset_id],
        [candidate],
        distance_threshold=0.15,
    )

    assert len(result.added_pet_ids) == 1
    assert repository.get_detection("candidate").pet_id != "pet-a"  # type: ignore[union-attr]


def test_bounded_cluster_excludes_zero_detection_replacement_assets(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    outlier = _detection(
        "outlier",
        asset_id="asset-a",
        embedding=np.asarray([0.94, 0.341, 0.0]),
        pet_id="pet-a",
    )
    retained = [
        _detection(f"retained-{index}", asset_id=f"asset-{index}", pet_id="pet-a")
        for index in ("b", "c")
    ]
    members = [outlier, *retained]
    stable = replace(
        _pet("pet-a", outlier),
        detection_count=3,
        sample_count=3,
        profile_state="stable",
        center_embedding=normalize_vector(
            np.mean([member.embedding for member in members], axis=0)
        ),
    )
    repository.replace_all(members, [stable])
    candidate = _detection("candidate", asset_id="asset-d")

    repository.replace_assets_incrementally(
        ["asset-a", "asset-d"],
        [candidate],
        distance_threshold=0.03,
    )

    assigned = repository.get_detection("candidate")
    assert assigned is not None and assigned.pet_id == "pet-a"
    assert repository.get_detection("outlier") is None


def test_bounded_cluster_expands_ann_shortlist_until_ninth_candidate(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db")
    detection = _detection("candidate", asset_id="asset-candidate")
    ordered = [(index / 100.0, f"pet-{index}") for index in range(1, 10)]

    class ExpandingIndex:
        def __init__(self) -> None:
            self.limits: list[int] = []

        def search(self, _embedding, *, species_label, limit):
            assert species_label == "dog"
            self.limits.append(limit)
            return ordered[:limit]

    candidate_index = ExpandingIndex()
    bad_sample = normalize_vector(np.asarray([0.0, 1.0, 0.0]))
    good_sample = normalize_vector(np.asarray([1.0, 0.0, 0.0]))
    member_samples = {
        pet_id: ((f"asset-{pet_id}", bad_sample),) for _distance, pet_id in ordered[:8]
    }
    member_samples["pet-9"] = (("asset-pet-9", good_sample),)

    matched = repository._nearest_compatible_pet_id(
        detection,
        member_samples=member_samples,
        staged_samples={},
        excluded_asset_ids=set(),
        candidate_index=candidate_index,  # type: ignore[arg-type]
        staged_candidate_species={},
        distance_threshold=0.1,
    )

    assert matched == "pet-9"
    assert candidate_index.limits == [8, 16]


@pytest.mark.parametrize(
    ("ordered", "expected_limits"),
    [
        ([(0.2, "too-far")], [8]),
        ([(0.01, f"pet-{index}") for index in range(8)], [8, 16]),
    ],
)
def test_bounded_cluster_progressive_search_stops_at_threshold_or_exhaustion(
    tmp_path: Path,
    ordered: list[tuple[float, str]],
    expected_limits: list[int],
) -> None:
    repository = PetRepository(tmp_path / "pet_index.db")

    class FiniteIndex:
        def __init__(self) -> None:
            self.limits: list[int] = []

        def search(self, _embedding, *, species_label, limit):
            assert species_label == "dog"
            self.limits.append(limit)
            return ordered[:limit]

    candidate_index = FiniteIndex()
    bad_sample = normalize_vector(np.asarray([0.0, 1.0, 0.0]))
    matched = repository._nearest_compatible_pet_id(
        _detection("candidate"),
        member_samples={pet_id: ((pet_id, bad_sample),) for _distance, pet_id in ordered},
        staged_samples={},
        excluded_asset_ids=set(),
        candidate_index=candidate_index,  # type: ignore[arg-type]
        staged_candidate_species={},
        distance_threshold=0.1,
    )

    assert matched == ""
    assert candidate_index.limits == expected_limits


@pytest.mark.parametrize(
    ("farthest_member_distance", "expected_pet_id"),
    [(0.62, "pet-a"), (0.64, "")],
)
def test_stable_incremental_match_uses_link_and_cluster_diameter_bounds(
    tmp_path: Path,
    farthest_member_distance: float,
    expected_pet_id: str,
) -> None:
    repository = PetRepository(tmp_path / "pet_index.db")
    candidate = _detection("candidate")

    class CandidateIndex:
        def search(self, _embedding, *, species_label, limit):
            assert species_label == "dog"
            assert limit == 8
            return [(0.3, "pet-a")]

    def vector_at_cosine_distance(distance: float) -> np.ndarray:
        cosine = 1.0 - distance
        return normalize_vector(np.asarray([cosine, np.sqrt(1.0 - cosine * cosine), 0.0]))

    matched = repository._nearest_compatible_pet_id(
        candidate,
        member_samples={
            "pet-a": (
                ("asset-near", vector_at_cosine_distance(0.40)),
                ("asset-far", vector_at_cosine_distance(farthest_member_distance)),
            )
        },
        staged_samples={},
        excluded_asset_ids=set(),
        candidate_index=CandidateIndex(),  # type: ignore[arg-type]
        staged_candidate_species={},
        distance_threshold=0.42,
    )

    assert matched == expected_pet_id


def test_stable_incremental_matching_is_input_order_invariant(tmp_path: Path) -> None:
    def assignments(root: Path, incoming: list[PetDetectionRecord]) -> dict[str, bool]:
        repository = PetRepository(root / "pet_index.db", root / "pet_state.db")
        members = [
            _detection(f"base-{index}", asset_id=f"base-{index}", pet_id="pet-a")
            for index in range(3)
        ]
        stable = replace(
            _pet("pet-a", members[0]),
            detection_count=3,
            sample_count=3,
            profile_state="stable",
        )
        repository.replace_all(members, [stable])
        repository.replace_assets_incrementally(
            [detection.asset_id for detection in incoming],
            incoming,
            distance_threshold=0.2,
        )
        return {
            detection.detection_id: repository.get_detection(detection.detection_id).pet_id  # type: ignore[union-attr]
            == "pet-a"
            for detection in incoming
        }

    positive = _detection(
        "positive",
        asset_id="asset-a",
        embedding=np.asarray([0.8660254, 0.5, 0.0]),
    )
    negative = _detection(
        "negative",
        asset_id="asset-b",
        embedding=np.asarray([0.8660254, -0.5, 0.0]),
    )

    forward = assignments(tmp_path / "forward", [positive, negative])
    reverse = assignments(tmp_path / "reverse", [negative, positive])

    assert forward == reverse == {"positive": True, "negative": False}


def test_state_repository_chunks_all_large_identity_reads(tmp_path: Path) -> None:
    repository = PetRepository(tmp_path / "pet_index.db", tmp_path / "pet_state.db")
    detections = [_detection(f"detection-{index}", pet_id=f"pet-{index}") for index in range(1205)]
    repository.replace_all(
        detections,
        [_pet(f"pet-{index}", detection) for index, detection in enumerate(detections)],
    )
    state = repository.state_repository
    assert state is not None
    pet_ids = [f"pet-{index}" for index in range(1205)]
    pet_keys = [f"v2:detection-{index}" for index in range(1205)]
    assert len(state.get_profiles_by_ids(pet_ids)) == 1205
    assert len(state.get_profile_name_map(pet_ids)) == 1205
    assert len(state.get_pet_key_map(pet_keys)) == 1205
    assert state.get_rejected_pet_keys(pet_keys) == set()
    assert len(state.get_pet_hidden_map(pet_ids)) == 1205


def test_model_resolver_skips_empty_cache_for_complete_bundled_embedder(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cache = tmp_path / "cache"
    bundled = tmp_path / "bundled"
    relative = Path("embedding/dinov2_vits14")
    (cache / relative).mkdir(parents=True)
    bundled_dir = bundled / relative
    bundled_dir.mkdir(parents=True)
    model_path = bundled_dir / "dinov2_vits14.pt"
    model_path.write_bytes(b"verified-torchscript")
    manifest = pet_pipeline.PET_MODEL_MANIFEST["embedder"]
    monkeypatch.setitem(
        manifest,
        "torchscript_sha256",
        hashlib.sha256(model_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setitem(manifest, "torchscript_size", model_path.stat().st_size)
    model_path.with_suffix(".pt.metadata.json").write_text(
        json.dumps(
            {
                "model_name": "dinov2_vits14",
                "source_repository": manifest["source_repository"],
                "source_revision": manifest["source_revision"],
                "torchscript_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
                "torchscript_size": model_path.stat().st_size,
                "input_shape": manifest["input_shape"],
                "output_shape": manifest["output_shape"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)
    monkeypatch.setattr(
        pet_pipeline,
        "pet_model_search_roots",
        lambda: (cache, bundled),
    )

    assert pet_pipeline.resolve_pet_model_path(relative, directory=True) == bundled_dir


def test_model_resolver_removes_corrupt_user_cache_and_uses_bundled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cache = tmp_path / "cache"
    bundled = tmp_path / "bundled"
    relative = Path("detector/yolox_nano_coco.onnx")
    cached_model = cache / relative
    bundled_model = bundled / relative
    cached_model.parent.mkdir(parents=True)
    bundled_model.parent.mkdir(parents=True)
    cached_model.write_bytes(b"corrupt")
    bundled_model.write_bytes(b"valid")

    def validate(path, **kwargs):
        del kwargs
        if Path(path) == cached_model:
            raise RuntimeError("bad hash")

    monkeypatch.setattr(pet_pipeline, "_validate_downloaded_file", validate)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)
    monkeypatch.setattr(
        pet_pipeline,
        "pet_model_search_roots",
        lambda: (cache, bundled),
    )

    assert pet_pipeline.resolve_pet_model_path(relative) == bundled_model
    assert not cached_model.exists()


def test_thumbnail_publish_compensates_when_later_replace_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    staged = tmp_path / "staged"
    published = tmp_path / "published"
    staged.mkdir()
    for name in ("a.png", "b.png", "c.png"):
        (staged / name).write_bytes(name.encode())
    original_replace = Path.replace
    calls = 0

    def fail_second(source: Path, target: Path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected publish failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_second)
    with pytest.raises(OSError, match="injected publish failure"):
        PetIndexCoordinator._publish_staged_thumbnails(staged, published)
    assert not list(published.glob("*.png"))


def test_public_mutation_cannot_overtake_unrecovered_journal_owner(
    tmp_path: Path,
) -> None:
    coordinator = PetIndexCoordinator(tmp_path)
    blocked_operation_id = coordinator._journal.prepare(
        "face_rename",
        {"person_id": "person-a", "name": "Blocked"},
    )
    coordinator._journal.transition(blocked_operation_id, "applying")

    assert coordinator.rename_pet("pet-a", "Must not be created") is None

    unfinished = coordinator._journal.unfinished()
    assert [operation.operation_id for operation in unfinished] == [blocked_operation_id]
    assert coordinator._recovery_error is not None


def test_legacy_outer_pet_merge_is_forward_recovered(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = PetRepository(
        tmp_path / ".iPhoto" / "pets" / "pet_index.db",
        tmp_path / ".iPhoto" / "pets" / "pet_state.db",
    )
    first = _detection("first", pet_id="pet-a")
    second = _detection(
        "second",
        asset_id="asset-b",
        embedding=np.asarray([0.0, 1.0, 0.0]),
        pet_id="pet-b",
    )
    repository.replace_all(
        [first, second],
        [_pet("pet-a", first), _pet("pet-b", second)],
    )
    coordinator = PetIndexCoordinator(tmp_path)
    monkeypatch.setattr(coordinator, "_repository", lambda: repository)
    legacy_operation_id = coordinator._journal.prepare(
        "recognition_merge",
        {"source": "pet:pet-a", "target": "pet:pet-b"},
    )
    coordinator._journal.transition(legacy_operation_id, "applying")

    assert coordinator.merge_pets("pet-a", "pet-b").merged is True

    assert coordinator._journal.unfinished() == ()
    assert {pet.pet_id for pet in repository.get_all_pet_records()} == {"pet-b"}
    assert repository.state_repository is not None
    assert repository.state_repository.get_merge_redirect_map()["pet-a"] == "pet-b"


def test_overlap_reconciliation_recovers_runtime_commit_state_sync(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = PetRepository(
        tmp_path / ".iPhoto" / "pets" / "pet_index.db",
        tmp_path / ".iPhoto" / "pets" / "pet_state.db",
    )
    detection = _detection("overlap", pet_id="pet-a")
    peer = replace(
        _detection("peer", pet_id="pet-b"),
        box_x=250,
        box_y=200,
    )
    repository.replace_all(
        [detection, peer],
        [_pet("pet-a", detection), _pet("pet-b", peer)],
    )
    face_repository = FaceRepository(
        tmp_path / ".iPhoto" / "faces" / "face_index.db",
        tmp_path / ".iPhoto" / "faces" / "face_state.db",
    )
    group = face_repository.create_group(["pet:pet-a", "pet:pet-b"])
    assert group is not None
    assert face_repository.get_common_asset_ids_for_group(group.group_id) == ["asset-a"]
    coordinator = PetIndexCoordinator(tmp_path)
    monkeypatch.setattr(coordinator, "_repository", lambda: repository)
    state = repository.state_repository
    assert state is not None

    def fail_state_sync(*_args, **_kwargs) -> None:
        raise sqlite3.OperationalError("injected state sync failure")

    monkeypatch.setattr(state, "sync_scan_results", fail_state_sync)
    with pytest.raises(sqlite3.OperationalError, match="injected state sync failure"):
        coordinator.reconcile_people_overlaps(
            {"asset-a": ((10, 20, 80, 90),)},
        )

    pending = coordinator._journal.unfinished()
    assert [operation.kind for operation in pending] == ["pet_overlap_reconcile"]
    operation_id = pending[0].operation_id
    assert repository.get_runtime_commit(operation_id)["state_synced"] is False  # type: ignore[index]
    assert repository.get_detection("overlap") is None

    recovered = PetIndexCoordinator(tmp_path)
    with recovered._lock:
        recovered._recover_operations_locked()
    recovered_repository = recovered._repository()
    assert recovered_repository.get_runtime_commit(operation_id)["state_synced"] is True  # type: ignore[index]
    assert recovered._journal.unfinished() == ()
    assert recovered_repository.state_repository is not None
    assert recovered_repository.state_repository.get_cover("pet-a") is None
    recovered_face_repository = FaceRepository(
        tmp_path / ".iPhoto" / "faces" / "face_index.db",
        tmp_path / ".iPhoto" / "faces" / "face_state.db",
    )
    assert recovered_face_repository.get_common_asset_ids_for_group(group.group_id) == []


def test_overlap_reconciliation_preserves_retained_mixed_generations(
    tmp_path: Path,
) -> None:
    repository = PetRepository(
        tmp_path / ".iPhoto" / "pets" / "pet_index.db",
        tmp_path / ".iPhoto" / "pets" / "pet_state.db",
    )
    removed = _detection("remove", asset_id="asset-a", pet_id="pet-a")
    retained_old = replace(
        _detection("retain-old", asset_id="asset-a", pet_id="pet-a"),
        box_x=250,
        box_y=200,
    )
    retained_new = replace(
        _detection(
            "retain-new",
            asset_id="asset-b",
            embedding=np.asarray([1.0, 0.0], dtype=np.float32),
            pet_id="pet-b",
        ),
        embedding_pipeline_version="embedding-v2",
        generation_id=1,
    )
    repository.replace_all(
        [removed, retained_old, retained_new],
        [_pet("pet-a", retained_old), _pet("pet-b", retained_new)],
    )
    coordinator = PetIndexCoordinator(tmp_path)

    event = coordinator.reconcile_people_overlaps(
        {
            "asset-a": ((10, 20, 80, 90),),
            "asset-b": (),
        }
    )

    assert event is not None
    remaining = {item.detection_id: item for item in repository.get_all_detections()}
    assert set(remaining) == {"retain-old", "retain-new"}
    assert remaining["retain-old"].generation_id == 0
    assert remaining["retain-new"].generation_id == 1
    assert remaining["retain-old"].pet_id == "pet-a"
    assert remaining["retain-new"].pet_id == "pet-b"


def test_delete_recovery_syncs_state_when_runtime_detection_is_already_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = PetRepository(
        tmp_path / ".iPhoto" / "pets" / "pet_index.db",
        tmp_path / ".iPhoto" / "pets" / "pet_state.db",
    )
    detection = _detection("delete-me", pet_id="pet-a")
    repository.replace_all([detection], [_pet("pet-a", detection)])
    coordinator = PetIndexCoordinator(tmp_path)
    monkeypatch.setattr(coordinator, "_repository", lambda: repository)
    state = repository.state_repository
    assert state is not None

    def fail_state_sync(*_args, **_kwargs) -> None:
        raise sqlite3.OperationalError("injected state sync failure")

    monkeypatch.setattr(state, "sync_scan_results", fail_state_sync)
    with pytest.raises(sqlite3.OperationalError, match="injected state sync failure"):
        coordinator.delete_detection("delete-me")

    pending = coordinator._journal.unfinished()
    assert [operation.kind for operation in pending] == ["pet_delete_detection"]
    operation_id = pending[0].operation_id
    assert repository.get_detection("delete-me") is None
    assert repository.get_runtime_commit(operation_id)["state_synced"] is False  # type: ignore[index]

    recovered = PetIndexCoordinator(tmp_path)
    with recovered._lock:
        recovered._recover_operations_locked()
    recovered_repository = recovered._repository()
    assert recovered_repository.get_runtime_commit(operation_id)["state_synced"] is True  # type: ignore[index]
    assert recovered._journal.unfinished() == ()
    assert recovered_repository.state_repository is not None
    assert detection.pet_key in recovered_repository.state_repository.get_rejected_pet_keys(
        (detection.pet_key,)
    )


@pytest.mark.parametrize("mutation", ["move", "merge"])
def test_move_and_merge_recover_after_runtime_commit(
    tmp_path: Path,
    monkeypatch,
    mutation: str,
) -> None:
    repository = PetRepository(
        tmp_path / ".iPhoto" / "pets" / "pet_index.db",
        tmp_path / ".iPhoto" / "pets" / "pet_state.db",
    )
    first = _detection("first", pet_id="pet-a")
    second = _detection(
        "second",
        asset_id="asset-b",
        embedding=np.asarray([0.0, 1.0, 0.0]),
        pet_id="pet-b",
    )
    repository.replace_all(
        [first, second],
        [_pet("pet-a", first), _pet("pet-b", second)],
    )
    coordinator = PetIndexCoordinator(tmp_path)
    monkeypatch.setattr(coordinator, "_repository", lambda: repository)
    state = repository.state_repository
    assert state is not None

    def fail_state_sync(*_args, **_kwargs) -> None:
        raise sqlite3.OperationalError("injected state sync failure")

    monkeypatch.setattr(state, "sync_scan_results", fail_state_sync)
    with pytest.raises(sqlite3.OperationalError, match="injected state sync failure"):
        if mutation == "move":
            coordinator.move_detection_to_pet("first", "pet-b")
        else:
            coordinator.merge_pets("pet-a", "pet-b")

    operation = coordinator._journal.unfinished()[0]
    assert repository.get_runtime_commit(operation.operation_id)["state_synced"] is False  # type: ignore[index]

    recovered = PetIndexCoordinator(tmp_path)
    with recovered._lock:
        recovered._recover_operations_locked()
    recovered_repository = recovered._repository()
    assert recovered_repository.get_runtime_commit(operation.operation_id)["state_synced"] is True  # type: ignore[index]
    assert recovered._journal.unfinished() == ()
    if mutation == "move":
        assert recovered_repository.get_detection("first").pet_id == "pet-b"  # type: ignore[union-attr]
    else:
        assert {pet.pet_id for pet in recovered_repository.get_all_pet_records()} == {"pet-b"}


def test_local_consolidation_recovers_state_and_pipeline_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = PetRepository(
        tmp_path / ".iPhoto" / "pets" / "pet_index.db",
        tmp_path / ".iPhoto" / "pets" / "pet_state.db",
    )
    first = _detection("first", pet_id="pet-a")
    second = _detection(
        "second",
        asset_id="asset-b",
        embedding=np.asarray([0.99, 0.01, 0.0]),
        pet_id="pet-b",
    )
    repository.replace_all(
        [first, second],
        [_pet("pet-a", first), _pet("pet-b", second)],
    )
    coordinator = PetIndexCoordinator(tmp_path)
    monkeypatch.setattr(coordinator, "_repository", lambda: repository)
    coordinator.prepare_clustering_pipeline(clustering_pipeline_target="cluster-v-next")
    state = repository.state_repository
    assert state is not None

    def fail_state_sync(*_args, **_kwargs) -> None:
        raise sqlite3.OperationalError("injected state sync failure")

    monkeypatch.setattr(state, "sync_scan_results", fail_state_sync)
    with pytest.raises(PetSnapshotCommittedError, match="durable state recovery"):
        coordinator.consolidate_pending_clustering(
            clustering_pipeline_target="cluster-v-next",
            distance_threshold=0.2,
        )

    operation = coordinator._journal.unfinished()[0]
    assert operation.kind == "pet_cluster_consolidate"
    assert repository.get_runtime_commit(operation.operation_id)["state_synced"] is False  # type: ignore[index]

    recovered = PetIndexCoordinator(tmp_path)
    with recovered._lock:
        recovered._recover_operations_locked()
    recovered_repository = recovered._repository()
    assert recovered_repository.get_runtime_commit(operation.operation_id)["state_synced"] is True  # type: ignore[index]
    assert recovered_repository.get_scan_metadata("clustering_pipeline_version") == (
        "cluster-v-next"
    )
    assert recovered._journal.unfinished() == ()
    operation_db = tmp_path / ".iPhoto" / "recognition" / "operations.db"
    with sqlite3.connect(operation_db) as connection:
        outbox_rows = connection.execute(
            "SELECT event_json FROM event_outbox WHERE operation_id = ?",
            (operation.operation_id,),
        ).fetchall()
    assert len(outbox_rows) == 1
    assert json.loads(outbox_rows[0][0])["changed_asset_ids"] == ["asset-a", "asset-b"]


def _detection(
    detection_id: str,
    *,
    asset_id: str = "asset-a",
    embedding: np.ndarray | None = None,
    pet_id: str | None = None,
) -> PetDetectionRecord:
    vector = normalize_vector(
        embedding if embedding is not None else np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
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
