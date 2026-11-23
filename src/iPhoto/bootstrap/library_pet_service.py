"""Library-scoped Pets service composition."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..application.ports.pets import PetAssetRepositoryPort
from ..cache.index_store import get_global_repository
from ..pets.service import PetService

if TYPE_CHECKING:
    from ..pets.index_coordinator import PetIndexCoordinator
    from ..recognition.mutation_coordinator import RecognitionMutationCoordinator


class IndexStorePetAssetRepository:
    """Adapt the current global index store for Pets status bookkeeping."""

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

    def read_rows_by_pet_status(
        self,
        statuses: Iterable[str],
        *,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        for row in self._repository().read_rows_by_pet_status(statuses, limit=limit):
            if isinstance(row, dict):
                yield dict(row)

    def update_pet_status(self, asset_id: str, status: str) -> None:
        self._repository().update_pet_status(asset_id, status)

    def update_pet_statuses(self, asset_ids: Iterable[str], status: str) -> None:
        self._repository().update_pet_statuses(asset_ids, status)

    def reset_pet_statuses_for_pipeline_upgrade(self) -> int:
        return int(self._repository().reset_pet_statuses_for_pipeline_upgrade())

    def count_by_pet_status(self) -> dict[str, int]:
        return dict(self._repository().count_by_pet_status())

    def _repository(self) -> Any:
        return self._repository_factory(self.library_root)


def create_pet_asset_repository(
    library_root: Path,
    *,
    repository_factory: Callable[[Path], Any] | None = None,
) -> PetAssetRepositoryPort:
    """Create the current Pets asset-index adapter."""

    return IndexStorePetAssetRepository(
        Path(library_root),
        repository_factory=repository_factory,
    )


def create_pet_service(
    library_root: Path,
    *,
    asset_repository: PetAssetRepositoryPort | None = None,
    coordinator: PetIndexCoordinator | None = None,
    mutation_coordinator: RecognitionMutationCoordinator | None = None,
    repository_factory: Callable[[Path], Any] | None = None,
) -> PetService:
    """Create a session-bound Pets service for one library."""

    root = Path(library_root)
    repository = asset_repository or create_pet_asset_repository(
        root,
        repository_factory=repository_factory,
    )
    if coordinator is not None:
        coordinator.set_asset_repository(repository)
    return PetService(
        root,
        asset_repository=repository,
        coordinator=coordinator,
        mutation_coordinator=mutation_coordinator,
    )


__all__ = [
    "IndexStorePetAssetRepository",
    "create_pet_asset_repository",
    "create_pet_service",
]
