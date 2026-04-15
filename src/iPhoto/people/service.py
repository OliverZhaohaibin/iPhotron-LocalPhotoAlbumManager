"""Library-bound helpers for People data and paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from iPhoto.cache.index_store import get_global_repository
from iPhoto.config import WORK_DIR_NAME
from iPhoto.domain.models.query import AssetQuery

from .repository import FaceRepository, PeopleGroupRecord, PeopleGroupSummary, PersonSummary
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

    def list_groups(self) -> list[PeopleGroupSummary]:
        repository = self.repository()
        if repository is None or self._library_root is None:
            return []
        summaries_by_id = {summary.person_id: summary for summary in self.list_clusters()}
        groups: list[PeopleGroupSummary] = []
        for group in repository.list_groups():
            summary = self._build_group_summary(repository, group, summaries_by_id)
            if summary is not None:
                groups.append(summary)
        return groups

    def create_group(
        self, member_person_ids: list[str] | tuple[str, ...]
    ) -> PeopleGroupSummary | None:
        repository = self.repository()
        if repository is None or self._library_root is None:
            return None
        summaries_by_id = {summary.person_id: summary for summary in self.list_clusters()}
        valid_member_ids = _ordered_valid_person_ids(member_person_ids, summaries_by_id)
        if len(valid_member_ids) < 2:
            return None
        group = repository.create_group(valid_member_ids)
        if group is None:
            return None
        return self._build_group_summary(repository, group, summaries_by_id)

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
        return self._valid_asset_ids(asset_ids)

    def build_cluster_query(self, person_id: str) -> AssetQuery:
        return AssetQuery(asset_ids=self.cluster_asset_ids(person_id))

    def group_asset_ids(self, group_id: str) -> list[str]:
        repository = self.repository()
        if repository is None or self._library_root is None:
            return []
        asset_ids = repository.get_common_asset_ids_for_group(group_id)
        return self._valid_asset_ids(asset_ids)

    def build_group_query(self, group_id: str) -> AssetQuery:
        return AssetQuery(asset_ids=self.group_asset_ids(group_id))

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

    def _build_group_summary(
        self,
        repository: FaceRepository,
        group: PeopleGroupRecord,
        summaries_by_id: dict[str, PersonSummary],
    ) -> PeopleGroupSummary | None:
        if self._library_root is None:
            return None
        members = tuple(
            summaries_by_id[person_id]
            for person_id in group.member_person_ids
            if person_id in summaries_by_id
        )
        if len(members) < 2:
            return None

        asset_ids = self._valid_asset_ids(repository.get_common_asset_ids_for_group(group.group_id))
        return PeopleGroupSummary(
            group_id=group.group_id,
            name=_format_group_name(member.name for member in members),
            member_person_ids=tuple(member.person_id for member in members),
            members=members,
            asset_count=len(asset_ids),
            cover_asset_path=self._cover_asset_path(asset_ids),
            created_at=group.created_at,
        )

    def _valid_asset_ids(self, asset_ids: list[str]) -> list[str]:
        if self._library_root is None or not asset_ids:
            return []
        rows_by_id = get_global_repository(self._library_root).get_rows_by_ids(asset_ids)
        return [asset_id for asset_id in asset_ids if asset_id in rows_by_id]

    def _cover_asset_path(self, asset_ids: list[str]) -> Path | None:
        if self._library_root is None or not asset_ids:
            return None
        rows_by_id = get_global_repository(self._library_root).get_rows_by_ids(asset_ids[:1])
        for asset_id in asset_ids:
            row = rows_by_id.get(asset_id)
            if row is None:
                continue
            rel_value = row.get("rel") or row.get("path")
            if not rel_value:
                continue
            path = Path(str(rel_value))
            return path if path.is_absolute() else self._library_root / path
        return None


def _format_group_name(names: object) -> str:
    cleaned = [str(name).strip() for name in names if name and str(name).strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"


def _ordered_valid_person_ids(
    person_ids: list[str] | tuple[str, ...],
    summaries_by_id: dict[str, PersonSummary],
) -> list[str]:
    valid: list[str] = []
    seen: set[str] = set()
    for person_id in person_ids:
        if person_id in seen or person_id not in summaries_by_id:
            continue
        seen.add(person_id)
        valid.append(person_id)
    return valid
