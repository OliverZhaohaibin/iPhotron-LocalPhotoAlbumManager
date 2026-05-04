"""Library-scoped People service composition."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from ..application.ports import PeopleAssetRepositoryPort
from ..cache.index_store import get_global_repository
from ..people.index_coordinator import (
    PeopleIndexCoordinator,
    get_people_index_coordinator,
)
from ..people.service import PeopleService


class IndexStorePeopleAssetRepository:
    """Adapt the current global index store for People status bookkeeping."""

    def __init__(
        self,
        library_root: Path,
        *,
        repository_factory: Callable[[Path], Any] | None = None,
    ) -> None:
        self.library_root = Path(library_root)
        self._repository_factory = repository_factory or get_global_repository

    def get_rows_by_ids(self, asset_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        return {
            str(asset_id): dict(row)
            for asset_id, row in self._repository().get_rows_by_ids(asset_ids).items()
        }

    def read_rows_by_face_status(
        self,
        statuses: Iterable[str],
        *,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        for row in self._repository().read_rows_by_face_status(statuses, limit=limit):
            if isinstance(row, dict):
                yield dict(row)

    def update_face_status(self, asset_id: str, status: str) -> None:
        self._repository().update_face_status(asset_id, status)

    def update_face_statuses(self, asset_ids: Iterable[str], status: str) -> None:
        self._repository().update_face_statuses(asset_ids, status)

    def count_by_face_status(self) -> dict[str, int]:
        return dict(self._repository().count_by_face_status())

    def _repository(self) -> Any:
        return self._repository_factory(self.library_root)


def create_people_asset_repository(
    library_root: Path,
    *,
    repository_factory: Callable[[Path], Any] | None = None,
) -> PeopleAssetRepositoryPort:
    """Create the current People asset-index adapter."""

    return IndexStorePeopleAssetRepository(
        Path(library_root),
        repository_factory=repository_factory,
    )


def create_people_service(
    library_root: Path,
    *,
    asset_repository: PeopleAssetRepositoryPort | None = None,
    coordinator: PeopleIndexCoordinator | None = None,
    repository_factory: Callable[[Path], Any] | None = None,
) -> PeopleService:
    """Create a session-bound People service for one library."""

    root = Path(library_root)
    repository = asset_repository or create_people_asset_repository(
        root,
        repository_factory=repository_factory,
    )
    if coordinator is None:
        resolved_coordinator = get_people_index_coordinator(
            root,
            asset_repository=repository,
        )
    else:
        coordinator.set_asset_repository(repository)
        resolved_coordinator = coordinator
    return PeopleService(
        root,
        asset_repository=repository,
        coordinator=resolved_coordinator,
    )


__all__ = [
    "IndexStorePeopleAssetRepository",
    "create_people_asset_repository",
    "create_people_service",
]
