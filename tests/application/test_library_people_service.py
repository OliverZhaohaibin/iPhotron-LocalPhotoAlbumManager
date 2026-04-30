from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from iPhoto.bootstrap.library_people_service import create_people_service
from iPhoto.bootstrap.library_session import LibrarySession
from iPhoto.people.index_coordinator import PeopleIndexCoordinator
from iPhoto.people.pipeline import DetectedAssetFaces


class FakePeopleAssetRepository:
    def __init__(self) -> None:
        self.rows_by_id: dict[str, dict[str, Any]] = {}
        self.status_updates: list[tuple[tuple[str, ...], str]] = []
        self.single_status_updates: list[tuple[str, str]] = []
        self.counts: dict[str, int] = {}

    def get_rows_by_ids(self, asset_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        return {
            asset_id: dict(self.rows_by_id[asset_id])
            for asset_id in asset_ids
            if asset_id in self.rows_by_id
        }

    def read_rows_by_face_status(
        self,
        statuses: Iterable[str],
        *,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        status_set = set(statuses)
        rows = [
            dict(row)
            for row in self.rows_by_id.values()
            if row.get("face_status") in status_set
        ]
        yield from rows[:limit]

    def update_face_status(self, asset_id: str, status: str) -> None:
        self.single_status_updates.append((asset_id, status))

    def update_face_statuses(self, asset_ids: Iterable[str], status: str) -> None:
        self.status_updates.append((tuple(asset_ids), status))

    def count_by_face_status(self) -> dict[str, int]:
        return dict(self.counts)


def test_library_session_exposes_people_surface(tmp_path: Path) -> None:
    runtime = Mock()
    runtime.repository = object()
    runtime.thumbnail_service = object()
    session = LibrarySession(tmp_path, asset_runtime=runtime)

    try:
        assert session.people is not None
        assert session.people.library_root() == tmp_path
        assert session.people.asset_repository is not None
        assert session.people.coordinator is not None
    finally:
        session.shutdown()


def test_people_service_uses_injected_asset_repository(tmp_path: Path) -> None:
    asset_repository = FakePeopleAssetRepository()
    asset_repository.counts = {"pending": 2, "retry": 1}
    coordinator = PeopleIndexCoordinator(tmp_path, asset_repository=asset_repository)
    service = create_people_service(
        tmp_path,
        asset_repository=asset_repository,
        coordinator=coordinator,
    )

    assert service.face_status_counts() == {"pending": 2, "retry": 1}
    assert service.mark_asset_retry("asset-a") is True
    assert asset_repository.single_status_updates == [("asset-a", "retry")]


def test_people_coordinator_done_bookkeeping_uses_injected_repository(tmp_path: Path) -> None:
    asset_repository = FakePeopleAssetRepository()
    coordinator = PeopleIndexCoordinator(tmp_path, asset_repository=asset_repository)

    event = coordinator.submit_detected_batch(
        [
            DetectedAssetFaces(
                asset_id="asset-a",
                asset_rel="album/a.jpg",
                faces=[],
            )
        ],
        distance_threshold=0.6,
        min_samples=2,
    )

    assert event is not None
    assert event.changed_asset_ids == ("asset-a",)
    assert asset_repository.status_updates == [(("asset-a",), "done")]


def test_people_service_binds_injected_coordinator_to_asset_repository(tmp_path: Path) -> None:
    asset_repository = FakePeopleAssetRepository()
    coordinator = PeopleIndexCoordinator(tmp_path)
    service = create_people_service(
        tmp_path,
        asset_repository=asset_repository,
        coordinator=coordinator,
    )

    assert service.coordinator is coordinator

    event = coordinator.submit_detected_batch(
        [
            DetectedAssetFaces(
                asset_id="asset-a",
                asset_rel="album/a.jpg",
                faces=[],
            )
        ],
        distance_threshold=0.6,
        min_samples=2,
    )

    assert event is not None
    assert asset_repository.status_updates == [(("asset-a",), "done")]
