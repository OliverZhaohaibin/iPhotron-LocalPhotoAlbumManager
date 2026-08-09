"""Library-bound helpers for Pets data and paths."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from iPhoto.application.ports.pets import PetAssetRepositoryPort
from iPhoto.domain.models.query import AssetQuery
from iPhoto.people.face_repository import FaceRepository
from iPhoto.people.state_repository import FaceStateRepository
from iPhoto.utils.logging import get_logger
from iPhoto.utils.pathutils import ensure_work_dir

from .index_coordinator import (
    PetIndexCoordinator,
    PetSnapshotEvent,
    get_pet_index_coordinator,
)
from .pipeline import default_pet_model_dir
from .records import AssetPetAnnotation, PetSummary
from .repository import PetRepository
from .status import PET_STATUS_RETRY, PET_STATUS_SKIPPED, normalize_pet_status

LOGGER = get_logger()


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
        redirected_pets = self._redirected_source_ids("pet")
        return self._with_valid_pet_asset_counts(
            [
                summary
                for summary in repository.get_pet_summaries(include_hidden=include_hidden)
                if summary.pet_id not in redirected_pets
            ],
            repository,
        )

    def load_dashboard(self, *, include_hidden: bool = False) -> tuple[list[PetSummary], int]:
        summaries = self.list_pets(include_hidden=include_hidden)
        counts = self.pet_status_counts()
        pending = counts.get("pending", 0) + counts.get("retry", 0)
        return summaries, pending

    def people_boxes_by_asset_ids(
        self,
        asset_ids: Iterable[str],
    ) -> dict[str, tuple[tuple[int, int, int, int], ...]]:
        repository = self._face_repository()
        if repository is None:
            return {}
        boxes_by_asset_id: dict[str, tuple[tuple[int, int, int, int], ...]] = {}
        try:
            redirected_people = self._redirected_source_ids("person")
            for asset_id in dict.fromkeys(str(value) for value in asset_ids if value):
                annotations = [
                    annotation
                    for annotation in repository.list_asset_face_annotations(asset_id)
                    if annotation.person_id not in redirected_people
                ]
                if annotations:
                    boxes_by_asset_id[asset_id] = tuple(
                        (
                            annotation.box_x,
                            annotation.box_y,
                            annotation.box_w,
                            annotation.box_h,
                        )
                        for annotation in annotations
                    )
        except Exception:  # noqa: BLE001 - People is an optional peer runtime
            LOGGER.warning(
                "People boxes unavailable while filtering pet detections for %s",
                self._library_root,
                exc_info=True,
            )
            return {}
        return boxes_by_asset_id

    def reconcile_people_overlaps(
        self,
        asset_ids: Iterable[str] | None = None,
    ) -> PetSnapshotEvent | None:
        repository = self.repository()
        coordinator = self.coordinator
        if repository is None or coordinator is None:
            return None
        scoped_ids = (
            tuple(dict.fromkeys(str(value) for value in asset_ids if value))
            if asset_ids is not None
            else tuple(
                dict.fromkeys(
                    detection.asset_id
                    for detection in repository.get_all_detections()
                    if detection.asset_id
                )
            )
        )
        if not scoped_ids:
            return None
        people_boxes = self.people_boxes_by_asset_ids(scoped_ids)
        return coordinator.reconcile_people_overlaps(people_boxes)

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
        if pet_id in self._redirected_source_ids("pet"):
            return []
        return self._valid_asset_ids(self._asset_ids_with_redirects(pet_id, repository))

    def build_pet_query(self, pet_id: str) -> AssetQuery:
        return AssetQuery(asset_ids=self.pet_asset_ids(pet_id))

    def has_pet(self, pet_id: str) -> bool:
        if pet_id in self._redirected_source_ids("pet"):
            return False
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

    def _with_valid_pet_asset_counts(
        self,
        summaries: list[PetSummary],
        repository: PetRepository,
    ) -> list[PetSummary]:
        if self._library_root is None or not summaries:
            return summaries
        return [
            replace(
                summary,
                asset_count=len(self._valid_asset_ids(self._asset_ids_with_redirects(summary.pet_id, repository))),
            )
            for summary in summaries
        ]

    def _asset_ids_with_redirects(
        self,
        pet_id: str,
        repository: PetRepository,
    ) -> list[str]:
        asset_ids = list(repository.get_asset_ids_by_pet(pet_id))
        seen = set(asset_ids)
        face_repository = self._face_repository()
        for redirect in self._identity_redirects():
            if redirect.target_kind != "pet" or redirect.target_id != pet_id:
                continue
            redirected_ids = (
                face_repository.get_asset_ids_by_person(redirect.source_id)
                if redirect.source_kind == "person" and face_repository is not None
                else repository.get_asset_ids_by_pet(redirect.source_id)
            )
            for asset_id in redirected_ids:
                if asset_id in seen:
                    continue
                seen.add(asset_id)
                asset_ids.append(asset_id)
        return asset_ids

    def _redirected_source_ids(self, kind: str) -> set[str]:
        return {
            redirect.source_id
            for redirect in self._identity_redirects()
            if redirect.source_kind == kind
        }

    def _identity_redirects(self):
        if self._library_root is None:
            return []
        face_state_path = ensure_work_dir(self._library_root) / "faces" / "face_state.db"
        return FaceStateRepository(face_state_path).get_identity_redirects()

    def _face_repository(self) -> FaceRepository | None:
        if self._library_root is None:
            return None
        faces_root = ensure_work_dir(self._library_root) / "faces"
        return FaceRepository(faces_root / "face_index.db", faces_root / "face_state.db")
