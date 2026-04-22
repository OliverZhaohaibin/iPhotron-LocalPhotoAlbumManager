"""Library-bound helpers for People data and paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import uuid

from iPhoto.cache.index_store import get_global_repository
from iPhoto.config import WORK_DIR_NAME
from iPhoto.domain.models.query import AssetQuery

from .index_coordinator import get_people_index_coordinator
from .pipeline import FaceClusterPipeline, ManualFaceValidationError
from .repository import (
    AssetFaceAnnotation,
    FaceRepository,
    PeopleGroupRecord,
    PeopleGroupSummary,
    PersonSummary,
)
from .status import FACE_STATUS_RETRY, FACE_STATUS_SKIPPED, normalize_face_status

_SHARED_FACE_MODEL_DIR = Path(__file__).resolve().parents[2] / "extension" / "models"


@dataclass(frozen=True)
class FaceLibraryPaths:
    root_dir: Path
    index_db_path: Path
    state_db_path: Path
    thumbnail_dir: Path
    model_dir: Path


@dataclass(frozen=True)
class ManualFaceAddResult:
    asset_id: str
    face_id: str
    person_id: str
    created_new_person: bool


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

    def list_groups(
        self,
        *,
        repository: FaceRepository | None = None,
        summaries: list[PersonSummary] | None = None,
    ) -> list[PeopleGroupSummary]:
        if self._library_root is None:
            return []
        repository = repository or self.repository()
        if repository is None:
            return []
        summary_list = summaries if summaries is not None else repository.get_person_summaries()
        summaries_by_id = {summary.person_id: summary for summary in summary_list}
        return self._build_group_summaries(repository, repository.list_groups(), summaries_by_id)

    def load_dashboard(self) -> tuple[list[PersonSummary], list[PeopleGroupSummary], int]:
        repository = self.repository()
        if repository is None:
            return [], [], 0
        summaries = repository.get_person_summaries()
        groups = self.list_groups(repository=repository, summaries=summaries)
        counts = self.face_status_counts()
        pending = counts.get("pending", 0) + counts.get("retry", 0)
        return summaries, groups, pending

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
        group = get_people_index_coordinator(self._library_root).create_group(valid_member_ids)
        if group is None:
            return None
        return self._build_group_summary(repository, group, summaries_by_id)

    def rename_cluster(self, person_id: str, new_name: str | None) -> None:
        if self._library_root is None:
            return
        get_people_index_coordinator(self._library_root).rename_person(person_id, new_name)

    def pin_block_reason(self, person_id: str) -> str | None:
        """Return a human-readable reason why *person_id* cannot be pinned."""

        repository = self.repository()
        if repository is None or not person_id:
            return None

        block_reason = getattr(repository, "pin_block_reason", None)
        if callable(block_reason):
            reason = block_reason(person_id)
            if isinstance(reason, str):
                normalized = reason.strip()
                if normalized:
                    return normalized

        is_hidden = getattr(repository, "is_person_hidden", None)
        if callable(is_hidden) and bool(is_hidden(person_id)):
            return "This person can't be pinned while hidden."

        return None

    def set_cluster_cover(self, person_id: str, face_id: str) -> bool:
        if self._library_root is None:
            return False
        return get_people_index_coordinator(self._library_root).set_person_cover(person_id, face_id)

    def set_group_cover(self, group_id: str, asset_id: str) -> bool:
        if self._library_root is None:
            return False
        return get_people_index_coordinator(self._library_root).set_group_cover(group_id, asset_id)

    def set_cluster_order(
        self,
        person_ids: list[str] | tuple[str, ...],
    ) -> None:
        if self._library_root is None:
            return
        get_people_index_coordinator(self._library_root).set_person_order(person_ids)

    def merge_clusters(self, source_person_id: str, target_person_id: str) -> bool:
        if self._library_root is None:
            return False
        return get_people_index_coordinator(self._library_root).merge_persons(
            source_person_id,
            target_person_id,
        )

    def cluster_asset_ids(self, person_id: str) -> list[str]:
        repository = self.repository()
        if repository is None or self._library_root is None:
            return []
        asset_ids = repository.get_asset_ids_by_person(person_id)
        return self._valid_asset_ids(asset_ids)

    def build_cluster_query(self, person_id: str) -> AssetQuery:
        return AssetQuery(asset_ids=self.cluster_asset_ids(person_id))

    def has_cluster(self, person_id: str) -> bool:
        repository = self.repository()
        if repository is None or not person_id:
            return False
        return any(summary.person_id == person_id for summary in repository.get_person_summaries())

    def group_asset_ids(self, group_id: str) -> list[str]:
        repository = self.repository()
        if repository is None or self._library_root is None:
            return []
        asset_ids = repository.get_common_asset_ids_for_group(group_id)
        return self._valid_asset_ids(asset_ids)

    def build_group_query(self, group_id: str) -> AssetQuery:
        return AssetQuery(asset_ids=self.group_asset_ids(group_id))

    def has_group(self, group_id: str) -> bool:
        return self.get_group_summary(group_id) is not None

    def list_asset_face_annotations(self, asset_id: str) -> list[AssetFaceAnnotation]:
        repository = self.repository()
        if repository is None or not asset_id:
            return []
        if self._library_root is not None:
            rows_by_id = get_global_repository(self._library_root).get_rows_by_ids([asset_id])
            if asset_id not in rows_by_id:
                return []
        return repository.list_asset_face_annotations(asset_id)

    def list_person_name_suggestions(self) -> list[PersonSummary]:
        return [
            summary
            for summary in self.list_clusters()
            if isinstance(summary.name, str) and summary.name.strip()
        ]

    def add_manual_face(
        self,
        *,
        asset_id: str,
        requested_box: tuple[int, int, int, int],
        name_or_none: str | None,
        person_id: str | None = None,
    ) -> ManualFaceAddResult:
        repository = self.repository()
        paths = self.paths()
        library_root = self._library_root
        if repository is None or paths is None or library_root is None or not asset_id:
            raise ManualFaceValidationError("Manual face tagging is unavailable right now.")

        row = get_global_repository(library_root).get_rows_by_ids([asset_id]).get(asset_id)
        if row is None:
            raise ManualFaceValidationError("The selected photo is no longer available.")
        asset_rel = str(row.get("rel") or row.get("path") or "").strip()
        if not asset_rel:
            raise ManualFaceValidationError("The selected photo path could not be resolved.")
        resolved_library_root = library_root.resolve()
        image_path = (resolved_library_root / asset_rel).resolve()
        try:
            image_path.relative_to(resolved_library_root)
        except ValueError as exc:
            raise ManualFaceValidationError("The selected photo path is invalid.") from exc
        if not image_path.is_file():
            raise ManualFaceValidationError("The selected photo file could not be found.")

        resolved_person_id, preferred_name, created_new_person = self._resolve_manual_face_person(
            repository,
            person_id=person_id,
            name_or_none=name_or_none,
        )
        pipeline = FaceClusterPipeline(model_root=paths.model_dir)
        existing_faces = list(repository.get_faces_by_asset_id(asset_id))
        face = pipeline.build_manual_face_record(
            asset_id=asset_id,
            asset_rel=asset_rel,
            image_path=image_path,
            requested_box=requested_box,
            thumbnail_dir=paths.thumbnail_dir,
            target_person_id=resolved_person_id,
            existing_faces=existing_faces,
        )
        add_result = get_people_index_coordinator(library_root).add_manual_face(
            face,
            person_name=preferred_name,
        )
        if add_result is None:
            raise ManualFaceValidationError("Manual face tagging is unavailable right now.")
        return ManualFaceAddResult(
            asset_id=asset_id,
            face_id=face.face_id,
            person_id=resolved_person_id,
            created_new_person=created_new_person,
        )

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
        cover_paths_by_asset_id: dict[str, Path] | None = None,
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
        cover_asset_id = repository.get_group_cover_asset_id(group.group_id)
        cover_candidates = []
        if cover_asset_id is not None:
            cover_candidates.append(cover_asset_id)
        cover_candidates.extend(asset_id for asset_id in asset_ids if asset_id != cover_asset_id)

        return PeopleGroupSummary(
            group_id=group.group_id,
            name=_format_group_name(member.name for member in members),
            member_person_ids=tuple(member.person_id for member in members),
            members=members,
            asset_count=len(asset_ids),
            cover_asset_path=self._cover_asset_path(
                cover_candidates,
                rows_by_id=cover_paths_by_asset_id,
            ),
            created_at=group.created_at,
        )

    def _build_group_summaries(
        self,
        repository: FaceRepository,
        groups: list[PeopleGroupRecord],
        summaries_by_id: dict[str, PersonSummary],
    ) -> list[PeopleGroupSummary]:
        prepared: list[tuple[PeopleGroupRecord, tuple[PersonSummary, ...], list[str], list[str]]] = []
        all_cover_candidate_ids: list[str] = []
        for group in groups:
            members = tuple(
                summaries_by_id[person_id]
                for person_id in group.member_person_ids
                if person_id in summaries_by_id
            )
            if len(members) < 2:
                continue
            asset_ids = self._valid_asset_ids(repository.get_common_asset_ids_for_group(group.group_id))
            cover_asset_id = repository.get_group_cover_asset_id(group.group_id)
            cover_candidates = []
            if cover_asset_id is not None:
                cover_candidates.append(cover_asset_id)
            cover_candidates.extend(asset_id for asset_id in asset_ids if asset_id != cover_asset_id)
            all_cover_candidate_ids.extend(cover_candidates)
            prepared.append((group, members, asset_ids, cover_candidates))

        cover_paths_by_asset_id = self._cover_asset_paths(all_cover_candidate_ids)
        summaries: list[PeopleGroupSummary] = []
        for group, members, asset_ids, cover_candidates in prepared:
            summary = PeopleGroupSummary(
                group_id=group.group_id,
                name=_format_group_name(member.name for member in members),
                member_person_ids=tuple(member.person_id for member in members),
                members=members,
                asset_count=len(asset_ids),
                cover_asset_path=self._cover_asset_path(
                    cover_candidates,
                    rows_by_id=cover_paths_by_asset_id,
                ),
                created_at=group.created_at,
            )
            summaries.append(summary)
        return summaries

    def _valid_asset_ids(self, asset_ids: list[str]) -> list[str]:
        if self._library_root is None or not asset_ids:
            return []
        rows_by_id = get_global_repository(self._library_root).get_rows_by_ids(asset_ids)
        return [asset_id for asset_id in asset_ids if asset_id in rows_by_id]

    def _cover_asset_paths(self, asset_ids: list[str]) -> dict[str, Path]:
        if self._library_root is None or not asset_ids:
            return {}
        rows_by_id = get_global_repository(self._library_root).get_rows_by_ids(asset_ids)
        resolved: dict[str, Path] = {}
        for asset_id, row in rows_by_id.items():
            rel_value = row.get("rel") or row.get("path")
            if not rel_value:
                continue
            path = Path(str(rel_value))
            resolved[str(asset_id)] = path if path.is_absolute() else self._library_root / path
        return resolved

    def _cover_asset_path(
        self,
        asset_ids: list[str],
        *,
        rows_by_id: dict[str, Path] | None = None,
    ) -> Path | None:
        if self._library_root is None or not asset_ids:
            return None
        resolved_rows = rows_by_id if rows_by_id is not None else self._cover_asset_paths(asset_ids)
        for asset_id in asset_ids:
            path = resolved_rows.get(asset_id)
            if path is not None:
                return path
        return None

    def _resolve_manual_face_person(
        self,
        repository: FaceRepository,
        *,
        person_id: str | None,
        name_or_none: str | None,
    ) -> tuple[str, str | None, bool]:
        summaries = repository.get_person_summaries()
        summaries_by_id = {summary.person_id: summary for summary in summaries}
        if person_id and person_id in summaries_by_id:
            return str(person_id), None, False

        normalized_name = str(name_or_none or "").strip()
        if not normalized_name:
            raise ManualFaceValidationError("Please enter a name before saving the face.")

        matching = [
            summary
            for summary in summaries
            if isinstance(summary.name, str)
            and summary.name.strip().casefold() == normalized_name.casefold()
        ]
        if len(matching) == 1:
            return matching[0].person_id, None, False
        return uuid.uuid4().hex, normalized_name, True


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
