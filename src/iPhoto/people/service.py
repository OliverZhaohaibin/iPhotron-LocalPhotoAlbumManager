"""Library-bound helpers for People data and paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from iPhoto.cache.index_store import get_global_repository
from iPhoto.config import WORK_DIR_NAME
from iPhoto.domain.models.query import AssetQuery

from .repository import FaceRepository, PersonSummary
from .status import FACE_STATUS_RETRY, FACE_STATUS_SKIPPED, normalize_face_status


_SHARED_FACE_MODEL_DIR = Path(__file__).resolve().parents[2] / "extension" / "models"


@dataclass(frozen=True)
class FaceLibraryPaths:
    root_dir: Path
    index_db_path: Path
    state_db_path: Path
    thumbnail_dir: Path
    model_dir: Path


def shared_face_model_dir() -> Path:
    """Return the shared cache directory for downloaded face models."""
    return _SHARED_FACE_MODEL_DIR


def face_library_paths(library_root: Path) -> FaceLibraryPaths:
    root_dir = library_root / WORK_DIR_NAME / "faces"
    return FaceLibraryPaths(
        root_dir=root_dir,
        index_db_path=root_dir / "face_index.db",
        state_db_path=root_dir / "face_state.db",
        thumbnail_dir=root_dir / "thumbnails",
        model_dir=shared_face_model_dir(),
    )


class PeopleService:
    def __init__(self, library_root: Path | None = None) -> None:
        self._library_root = library_root

    def set_library_root(self, library_root: Path | None) -> None:
        self._library_root = library_root

    def library_root(self) -> Path | None:
        return self._library_root

    def is_bound(self) -> bool:
        return self._library_root is not None

    def paths(self) -> FaceLibraryPaths | None:
        if self._library_root is None:
            return None
        return face_library_paths(self._library_root)

    def repository(self) -> FaceRepository | None:
        paths = self.paths()
        if paths is None:
            return None
        return FaceRepository(paths.index_db_path, paths.state_db_path)

    def list_clusters(self) -> list[PersonSummary]:
        repository = self.repository()
        if repository is None:
            return []
        return repository.get_person_summaries()

    def rename_cluster(self, person_id: str, new_name: str | None) -> None:
        repository = self.repository()
        if repository is None:
            return
        repository.rename_person(person_id, new_name)

    def merge_clusters(self, source_person_id: str, target_person_id: str) -> bool:
        repository = self.repository()
        if repository is None:
            return False
        return repository.merge_persons(source_person_id, target_person_id)

    def cluster_asset_ids(self, person_id: str) -> list[str]:
        repository = self.repository()
        if repository is None or self._library_root is None:
            return []
        asset_ids = repository.get_asset_ids_by_person(person_id)
        if not asset_ids:
            return []
        rows_by_id = get_global_repository(self._library_root).get_rows_by_ids(asset_ids)
        return [asset_id for asset_id in asset_ids if asset_id in rows_by_id]

    def build_cluster_query(self, person_id: str) -> AssetQuery:
        return AssetQuery(asset_ids=self.cluster_asset_ids(person_id))

    def face_status_counts(self) -> dict[str, int]:
        if self._library_root is None:
            return {}
        return get_global_repository(self._library_root).count_by_face_status()

    def mark_asset_retry(self, asset_id: str) -> bool:
        return self._mark_asset_status(asset_id, FACE_STATUS_RETRY)

    def mark_asset_skipped(self, asset_id: str) -> bool:
        return self._mark_asset_status(asset_id, FACE_STATUS_SKIPPED)

    def _mark_asset_status(self, asset_id: str, status: str) -> bool:
        if self._library_root is None or not asset_id:
            return False
        normalized = normalize_face_status(status)
        if normalized is None:
            return False
        get_global_repository(self._library_root).update_face_status(asset_id, normalized)
        return True
