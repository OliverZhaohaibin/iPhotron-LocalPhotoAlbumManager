"""Library-bound helpers for Pets data and paths."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from iPhoto.application.ports.pets import PetAssetRepositoryPort
from iPhoto.domain.models.query import AssetQuery
from iPhoto.utils.pathutils import ensure_work_dir

from .index_coordinator import PetIndexCoordinator, get_pet_index_coordinator
from .pipeline import default_pet_model_dir
from .records import AssetPetAnnotation, PetSummary
from .repository import PetRepository
from .status import PET_STATUS_RETRY, PET_STATUS_SKIPPED, normalize_pet_status


@dataclass(frozen=True)
class PetLibraryPaths:
    root_dir: Path
    index_db_path: Path
    state_db_path: Path
    thumbnail_dir: Path
    model_dir: Path


def shared_pet_model_dir() -> Path:
    return default_pet_model_dir()


def pet_library_paths(library_root: Path) -> PetLibraryPaths:
    root_dir = ensure_work_dir(library_root) / "pets"
    return PetLibraryPaths(
        root_dir=root_dir,
        index_db_path=root_dir / "pet_index.db",
        state_db_path=root_dir / "pet_state.db",
        thumbnail_dir=root_dir / "thumbnails",
        model_dir=shared_pet_model_dir(),
    )


class PetService:
    def __init__(
        self,
        library_root: Path | None = None,
        *,
        asset_repository: PetAssetRepositoryPort | None = None,
        coordinator: PetIndexCoordinator | None = None,
    ) -> None:
        self._library_root = library_root
        self._asset_repository = asset_repository
        self._coordinator = coordinator

    def set_library_root(self, library_root: Path | None) -> None:
        if self._library_root == library_root:
            return
        self._library_root = library_root
        self._asset_repository = None
        self._coordinator = None

    def library_root(self) -> Path | None:
        return self._library_root

    def is_bound(self) -> bool:
        return self._library_root is not None

    @property
    def asset_repository(self) -> PetAssetRepositoryPort | None:
        return self._asset_repository

    @property
    def coordinator(self) -> PetIndexCoordinator | None:
        if self._coordinator is not None:
            return self._coordinator
        if self._library_root is None:
            return None
        self._coordinator = get_pet_index_coordinator(
            self._library_root,
            asset_repository=self._asset_repository,
        )
        return self._coordinator

    def paths(self) -> PetLibraryPaths | None:
        if self._library_root is None:
            return None
        return pet_library_paths(self._library_root)

    def repository(self) -> PetRepository | None:
        paths = self.paths()
        if paths is None:
            return None
        return PetRepository(paths.index_db_path, paths.state_db_path)

    def list_pets(self, *, include_hidden: bool = False) -> list[PetSummary]:
        repository = self.repository()
        if repository is None:
            return []
        return repository.get_pet_summaries(include_hidden=include_hidden)

    def load_dashboard(self, *, include_hidden: bool = False) -> tuple[list[PetSummary], int]:
        summaries = self.list_pets(include_hidden=include_hidden)
        counts = self.pet_status_counts()
        pending = counts.get("pending", 0) + counts.get("retry", 0)
        return summaries, pending

    def rename_pet(self, pet_id: str, new_name: str | None) -> None:
        coordinator = self.coordinator
        if coordinator is not None:
            coordinator.rename_pet(pet_id, new_name)

    def set_pet_hidden(self, pet_id: str, hidden: bool) -> bool:
        coordinator = self.coordinator
        return bool(coordinator and coordinator.set_pet_hidden(pet_id, hidden))

    def merge_pets(self, source_pet_id: str, target_pet_id: str) -> bool:
        coordinator = self.coordinator
        return bool(coordinator and coordinator.merge_pets(source_pet_id, target_pet_id))

    def set_pet_cover(self, pet_id: str, detection_id: str) -> bool:
        coordinator = self.coordinator
        return bool(coordinator and coordinator.set_pet_cover(pet_id, detection_id))

    def delete_detection(self, detection_id: str) -> bool:
        coordinator = self.coordinator
        if coordinator is None:
            return False
        return coordinator.delete_detection(detection_id) is not None

    def move_detection_to_pet(self, detection_id: str, target_pet_id: str) -> bool:
        coordinator = self.coordinator
        if coordinator is None:
            return False
        return coordinator.move_detection_to_pet(detection_id, target_pet_id) is not None

    def move_detection_to_new_pet(self, detection_id: str, new_name: str) -> str | None:
        normalized_name = str(new_name or "").strip()
        if not detection_id or not normalized_name:
            return None
        new_pet_id = uuid.uuid4().hex
        coordinator = self.coordinator
        if coordinator is None:
            return None
        event = coordinator.move_detection_to_new_pet(detection_id, new_pet_id, normalized_name)
        return new_pet_id if event is not None else None

    def pet_asset_ids(self, pet_id: str) -> list[str]:
        repository = self.repository()
        if repository is None or self._library_root is None:
            return []
        return self._valid_asset_ids(repository.get_asset_ids_by_pet(pet_id))

    def build_pet_query(self, pet_id: str) -> AssetQuery:
        return AssetQuery(asset_ids=self.pet_asset_ids(pet_id))

    def has_pet(self, pet_id: str) -> bool:
        return any(summary.pet_id == pet_id for summary in self.list_pets(include_hidden=True))

    def list_asset_pet_annotations(self, asset_id: str) -> list[AssetPetAnnotation]:
        repository = self.repository()
        if repository is None or not asset_id:
            return []
        asset_repository = self.asset_repository
        if asset_repository is not None:
            rows_by_id = asset_repository.get_rows_by_ids([asset_id])
            if asset_id not in rows_by_id:
                return []
        return repository.list_asset_pet_annotations(asset_id)

    def pet_status_counts(self) -> dict[str, int]:
        if self._library_root is None or self.asset_repository is None:
            return {}
        return self.asset_repository.count_by_pet_status()

    def mark_asset_retry(self, asset_id: str) -> bool:
        return self._mark_asset_status(asset_id, PET_STATUS_RETRY)

    def mark_asset_skipped(self, asset_id: str) -> bool:
        return self._mark_asset_status(asset_id, PET_STATUS_SKIPPED)

    def _mark_asset_status(self, asset_id: str, status: str) -> bool:
        asset_repository = self.asset_repository
        if self._library_root is None or asset_repository is None or not asset_id:
            return False
        normalized = normalize_pet_status(status)
        if normalized is None:
            return False
        asset_repository.update_pet_status(asset_id, normalized)
        return True

    def _valid_asset_ids(self, asset_ids: list[str]) -> list[str]:
        if self._library_root is None or not asset_ids:
            return []
        asset_repository = self.asset_repository
        if asset_repository is None:
            return list(asset_ids)
        rows_by_id = asset_repository.get_rows_by_ids(asset_ids)
        return [asset_id for asset_id in asset_ids if asset_id in rows_by_id]
