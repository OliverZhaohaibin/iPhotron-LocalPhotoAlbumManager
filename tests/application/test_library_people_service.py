from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import numpy as np

from iPhoto.bootstrap.library_people_service import create_people_service
from iPhoto.bootstrap.library_session import LibrarySession
from iPhoto.people.index_coordinator import PeopleIndexCoordinator
from iPhoto.people.pipeline import DetectedAssetFaces
from iPhoto.people.repository import FaceRecord, PersonRecord


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _face_record(
    *,
    face_id: str,
    asset_id: str,
    asset_rel: str,
    person_id: str,
) -> FaceRecord:
    return FaceRecord(
        face_id=face_id,
        face_key=f"key-{face_id}",
        asset_id=asset_id,
        asset_rel=asset_rel,
        box_x=10,
        box_y=12,
        box_w=80,
        box_h=80,
        confidence=0.99,
        embedding=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        embedding_dim=3,
        thumbnail_path=None,
        person_id=person_id,
        detected_at=_now_iso(),
        image_width=400,
        image_height=300,
    )


def _person_record(
    *,
    person_id: str,
    key_face_id: str,
    face_count: int = 1,
    name: str | None = None,
) -> PersonRecord:
    timestamp = _now_iso()
    return PersonRecord(
        person_id=person_id,
        name=name,
        key_face_id=key_face_id,
        face_count=face_count,
        center_embedding=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        created_at=timestamp,
        updated_at=timestamp,
        sample_count=face_count,
        profile_state="stable" if face_count >= 3 else "unstable",
    )


def test_library_session_exposes_people_surface(tmp_path: Path) -> None:
    runtime = Mock()
    runtime.assets = object()
    runtime.repository = object()
    runtime.thumbnail_service = object()
    session = LibrarySession(tmp_path, asset_runtime=runtime, maps=Mock())

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


def test_library_session_people_hidden_and_order_survive_reload(tmp_path: Path) -> None:
    runtime = Mock()
    runtime.assets = object()
    runtime.repository = object()
    runtime.thumbnail_service = object()
    session = LibrarySession(tmp_path, asset_runtime=runtime, maps=Mock())

    faces = [
        _face_record(
            face_id="face-a",
            asset_id="asset-a",
            asset_rel="album/a.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b",
            asset_id="asset-b",
            asset_rel="album/b.jpg",
            person_id="person-b",
        ),
    ]
    persons = [
        _person_record(person_id="person-a", key_face_id="face-a", name="Alice"),
        _person_record(person_id="person-b", key_face_id="face-b", name="Bob"),
    ]

    try:
        assert session.people is not None
        repository = session.people.repository()
        assert repository is not None
        repository.replace_all(faces, persons)

        assert session.people.set_cluster_hidden("person-b", True) is True
        session.people.set_cluster_order(["person-b", "person-a"])

        repository.replace_all(faces, persons)

        visible = session.people.list_clusters()
        with_hidden = session.people.list_clusters(include_hidden=True)
        assert [summary.person_id for summary in visible] == ["person-a"]
        assert [summary.person_id for summary in with_hidden] == ["person-b", "person-a"]
        assert {summary.person_id: summary.is_hidden for summary in with_hidden} == {
            "person-a": False,
            "person-b": True,
        }
    finally:
        session.shutdown()


def test_library_session_people_group_order_survives_reload(tmp_path: Path) -> None:
    runtime = Mock()
    runtime.assets = object()
    runtime.repository = object()
    runtime.thumbnail_service = object()
    session = LibrarySession(tmp_path, asset_runtime=runtime, maps=Mock())

    faces = [
        _face_record(
            face_id="face-a-shared",
            asset_id="asset-ab",
            asset_rel="album/ab.jpg",
            person_id="person-a",
        ),
        _face_record(
            face_id="face-b-shared",
            asset_id="asset-ab",
            asset_rel="album/ab.jpg",
            person_id="person-b",
        ),
        _face_record(
            face_id="face-b-other",
            asset_id="asset-bc",
            asset_rel="album/bc.jpg",
            person_id="person-b",
        ),
        _face_record(
            face_id="face-c-other",
            asset_id="asset-bc",
            asset_rel="album/bc.jpg",
            person_id="person-c",
        ),
    ]
    persons = [
        _person_record(person_id="person-a", key_face_id="face-a-shared", name="Alice"),
        _person_record(person_id="person-b", key_face_id="face-b-shared", face_count=2, name="Bob"),
        _person_record(person_id="person-c", key_face_id="face-c-other", name="Cara"),
    ]

    try:
        assert session.people is not None
        repository = session.people.repository()
        assert repository is not None
        repository.replace_all(faces, persons)
        group_ab = repository.create_group(["person-a", "person-b"])
        group_bc = repository.create_group(["person-b", "person-c"])
        assert group_ab is not None
        assert group_bc is not None

        session.people.set_group_order([group_bc.group_id, group_ab.group_id])
        repository.replace_all(faces, persons)

        assert [
            group.group_id
            for group in session.people.list_groups(
                summaries=session.people.list_clusters(include_hidden=True),
            )
        ] == [group_bc.group_id, group_ab.group_id]
    finally:
        session.shutdown()
