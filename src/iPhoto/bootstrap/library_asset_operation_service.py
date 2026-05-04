"""Library-scoped planning for move, delete, and restore asset operations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import ALBUM_MANIFEST_NAMES
from ..media_classifier import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from ..utils.jsonio import read_json
from .library_asset_lifecycle_service import LibraryAssetLifecycleService

MetadataLookup = Callable[[Path], dict[str, Any] | None]
RestoreToRootPrompt = Callable[[str], bool]


@dataclass(frozen=True)
class AssetMovePlan:
    """Validated file-move request ready for a Qt worker to execute."""

    operation: str
    source_root: Path | None
    destination_root: Path | None
    sources: list[Path] = field(default_factory=list)
    library_root: Path | None = None
    trash_root: Path | None = None
    asset_lifecycle_service: LibraryAssetLifecycleService | None = field(
        default=None,
        compare=False,
    )
    errors: list[str] = field(default_factory=list)
    finished_message: str | None = None
    accepted: bool = True


@dataclass(frozen=True)
class AssetRestorePlan:
    """Restore batches grouped by destination album."""

    batches: list[AssetMovePlan] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return any(batch.accepted for batch in self.batches)


class LibraryAssetOperationService:
    """Prepare file operation requests for one active library session."""

    def __init__(
        self,
        library_root: Path | None,
        *,
        lifecycle_service: LibraryAssetLifecycleService | None = None,
    ) -> None:
        self.library_root = (
            self._normalize_path(Path(library_root)) if library_root else None
        )
        self._lifecycle_service = lifecycle_service or LibraryAssetLifecycleService(
            self.library_root,
        )
        self._album_uuid_cache: dict[str, Path | None] = {}

    @property
    def lifecycle_service(self) -> LibraryAssetLifecycleService:
        return self._lifecycle_service

    def plan_move_request(
        self,
        sources: Iterable[Path],
        destination: Path,
        *,
        current_album_root: Path | None,
        operation: str = "move",
        trash_root: Path | None = None,
        metadata_lookup: MetadataLookup | None = None,
    ) -> AssetMovePlan:
        """Validate and normalize a move/delete/restore request."""

        operation_normalized = operation.lower()
        if operation_normalized not in {"move", "delete", "restore"}:
            return self._rejected_move(
                operation_normalized,
                errors=[f"Unsupported move operation: {operation}"],
            )

        resolved_trash_root = self._normalize_optional_path(trash_root)
        source_root = self._source_root_for(
            operation_normalized,
            current_album_root=current_album_root,
        )
        if source_root is None:
            return self._rejected_move(
                operation_normalized,
                errors=["No album is currently open."],
            )

        try:
            destination_root = Path(destination).expanduser().resolve()
        except OSError as exc:
            return self._rejected_move(
                operation_normalized,
                source_root=source_root,
                errors=[f"Invalid destination: {exc}"],
            )

        if not destination_root.exists() or not destination_root.is_dir():
            return self._rejected_move(
                operation_normalized,
                source_root=source_root,
                destination_root=destination_root,
                errors=[f"Move destination is not a directory: {destination_root}"],
            )

        if self._paths_equal(destination_root, source_root):
            return self._rejected_move(
                operation_normalized,
                source_root=source_root,
                destination_root=destination_root,
                finished_message="Files are already located in this album.",
            )

        is_restore_operation = operation_normalized == "restore"
        is_delete_operation = operation_normalized == "delete"
        is_trash_destination = self._paths_equal(destination_root, resolved_trash_root)

        if is_delete_operation and self.library_root is None:
            return self._rejected_move(
                operation_normalized,
                source_root=source_root,
                destination_root=destination_root,
                errors=[
                    "Basic Library root is unavailable; cannot delete items safely."
                ],
            )

        if is_delete_operation and not is_trash_destination:
            return self._rejected_move(
                operation_normalized,
                source_root=source_root,
                destination_root=destination_root,
                errors=["Recently Deleted folder is unavailable."],
            )

        if is_restore_operation:
            if resolved_trash_root is None:
                return self._rejected_move(
                    operation_normalized,
                    source_root=source_root,
                    destination_root=destination_root,
                    errors=["Recently Deleted folder is unavailable."],
                )
            if not self._paths_equal(source_root, resolved_trash_root):
                return self._rejected_move(
                    operation_normalized,
                    source_root=source_root,
                    destination_root=destination_root,
                    errors=[
                        "Restore operations must be triggered from Recently Deleted."
                    ],
                )
            if self._paths_equal(destination_root, resolved_trash_root):
                return self._rejected_move(
                    operation_normalized,
                    source_root=source_root,
                    destination_root=destination_root,
                    errors=["Cannot restore items back into Recently Deleted."],
                )

        expanded_sources = list(sources)
        if is_delete_operation:
            expanded_sources = self._expand_delete_live_companions(
                expanded_sources,
                metadata_lookup=metadata_lookup,
            )

        normalized, errors = self._normalize_sources(
            expanded_sources,
            source_root=source_root,
            operation=operation_normalized,
        )
        if not normalized:
            message = (
                "No items were deleted."
                if is_delete_operation
                else "No valid files were selected for moving."
            )
            return self._rejected_move(
                operation_normalized,
                source_root=source_root,
                destination_root=destination_root,
                trash_root=(
                    resolved_trash_root
                    if is_trash_destination or is_restore_operation
                    else None
                ),
                errors=errors,
                finished_message=message,
            )

        return AssetMovePlan(
            operation=operation_normalized,
            source_root=source_root,
            destination_root=destination_root,
            sources=normalized,
            library_root=self.library_root,
            trash_root=(
                resolved_trash_root
                if is_trash_destination or is_restore_operation
                else None
            ),
            asset_lifecycle_service=self.lifecycle_service,
            errors=errors,
            accepted=True,
        )

    def plan_delete_request(
        self,
        sources: Iterable[Path],
        *,
        trash_root: Path,
        metadata_lookup: MetadataLookup | None = None,
    ) -> AssetMovePlan:
        """Plan a delete operation into Recently Deleted."""

        return self.plan_move_request(
            sources,
            trash_root,
            current_album_root=None,
            operation="delete",
            trash_root=trash_root,
            metadata_lookup=metadata_lookup,
        )

    def plan_restore_request(
        self,
        sources: Iterable[Path],
        *,
        trash_root: Path,
        metadata_lookup: MetadataLookup | None = None,
        restore_to_root_prompt: RestoreToRootPrompt | None = None,
    ) -> AssetRestorePlan:
        """Plan restore moves grouped by destination album."""

        if self.library_root is None:
            return AssetRestorePlan(
                errors=["Basic Library has not been configured."],
            )

        normalized_trash_root = self._normalize_path(Path(trash_root))
        normalized, errors = self._normalize_restore_sources(
            sources,
            trash_root=normalized_trash_root,
        )
        if not normalized:
            return AssetRestorePlan(errors=errors)

        normalized = self._expand_restore_live_companions(
            normalized,
            trash_root=normalized_trash_root,
            metadata_lookup=metadata_lookup,
        )

        row_lookup = self._read_restore_row_lookup(normalized_trash_root)
        fallback_rows = self._read_fallback_restore_rows(
            normalized,
            trash_root=normalized_trash_root,
            row_lookup=row_lookup,
        )

        grouped: dict[Path, list[Path]] = defaultdict(list)
        for path in normalized:
            destination_root: Path | None = None
            try:
                key = str(self._normalize_path(path))
                row = row_lookup.get(key)
                if not row:
                    row = dict(fallback_rows.get(key, {}))
                destination_root = self._determine_restore_destination(
                    row=row,
                    filename=path.name,
                    restore_to_root_prompt=restore_to_root_prompt,
                )
                if destination_root is None:
                    continue
                destination_root.mkdir(parents=True, exist_ok=True)
            except LookupError as exc:
                errors.append(str(exc))
                continue
            except OSError as exc:
                errors.append(
                    f"Could not prepare restore destination '{destination_root}': {exc}"
                )
                continue
            grouped[destination_root].append(path)

        batches = [
            AssetMovePlan(
                operation="restore",
                source_root=normalized_trash_root,
                destination_root=destination_root,
                sources=paths,
                library_root=self.library_root,
                trash_root=normalized_trash_root,
                asset_lifecycle_service=self.lifecycle_service,
                accepted=bool(paths),
            )
            for destination_root, paths in grouped.items()
        ]
        return AssetRestorePlan(batches=batches, errors=errors)

    def _source_root_for(
        self,
        operation: str,
        *,
        current_album_root: Path | None,
    ) -> Path | None:
        if operation == "delete" and self.library_root is not None:
            return self.library_root
        if current_album_root is not None:
            return self._normalize_path(Path(current_album_root))
        return self.library_root

    def _normalize_sources(
        self,
        sources: Iterable[Path],
        *,
        source_root: Path,
        operation: str,
    ) -> tuple[list[Path], list[str]]:
        normalized: list[Path] = []
        errors: list[str] = []
        seen: set[Path] = set()
        for raw_path in sources:
            candidate = Path(raw_path)
            try:
                resolved = candidate.resolve()
            except OSError as exc:
                errors.append(f"Could not resolve '{candidate}': {exc}")
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            if not resolved.exists():
                if operation == "delete":
                    continue
                errors.append(f"File not found: {resolved}")
                continue
            if resolved.is_dir():
                errors.append(f"Skipping directory move attempt: {resolved.name}")
                continue
            try:
                resolved.relative_to(source_root)
            except ValueError:
                errors.append(f"Path '{resolved}' is not inside the active album.")
                continue
            normalized.append(resolved)
        return normalized, errors

    def _normalize_restore_sources(
        self,
        sources: Iterable[Path],
        *,
        trash_root: Path,
    ) -> tuple[list[Path], list[str]]:
        normalized: list[Path] = []
        errors: list[str] = []
        seen: set[str] = set()
        for raw_path in sources:
            candidate = self._normalize_path(Path(raw_path))
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if not candidate.exists():
                errors.append(f"File not found: {candidate}")
                continue
            try:
                candidate.relative_to(trash_root)
            except ValueError:
                errors.append(f"Selection is outside Recently Deleted: {candidate}")
                continue
            normalized.append(candidate)
        return normalized, errors

    def _expand_delete_live_companions(
        self,
        sources: Iterable[Path],
        *,
        metadata_lookup: MetadataLookup | None,
    ) -> list[Path]:
        normalized = [self._normalize_path(Path(path)) for path in sources]
        seen = {str(path) for path in normalized}
        for still_path in list(normalized):
            metadata = self._metadata_for_path(still_path, metadata_lookup)
            if not metadata or not metadata.get("is_live"):
                continue
            motion_raw = metadata.get("live_motion_abs")
            if not motion_raw:
                continue
            motion_path = self._normalize_path(Path(str(motion_raw)))
            motion_key = str(motion_path)
            if motion_key in seen:
                continue
            seen.add(motion_key)
            normalized.append(motion_path)
        return normalized

    def _expand_restore_live_companions(
        self,
        sources: list[Path],
        *,
        trash_root: Path,
        metadata_lookup: MetadataLookup | None,
    ) -> list[Path]:
        normalized = [self._normalize_path(path) for path in sources]
        seen = {str(path) for path in normalized}
        for still_path in list(normalized):
            metadata = self._metadata_for_path(still_path, metadata_lookup)
            if metadata and metadata.get("is_live"):
                motion_raw = metadata.get("live_motion_abs")
                if motion_raw:
                    motion_path = self._normalize_path(Path(str(motion_raw)))
                    motion_key = str(motion_path)
                    if motion_key not in seen and motion_path.exists():
                        try:
                            motion_path.relative_to(trash_root)
                        except ValueError:
                            pass
                        else:
                            seen.add(motion_key)
                            normalized.append(motion_path)
            if metadata is None or "is_live" not in metadata:
                motion_path = self._same_stem_motion_path(still_path, trash_root)
                if motion_path is None:
                    continue
                motion_key = str(motion_path)
                if motion_key in seen:
                    continue
                seen.add(motion_key)
                normalized.append(motion_path)
        return normalized

    def _same_stem_motion_path(self, still_path: Path, trash_root: Path) -> Path | None:
        if still_path.suffix.lower() not in IMAGE_EXTENSIONS:
            return None
        for suffix in VIDEO_EXTENSIONS:
            motion_path = trash_root / f"{still_path.stem}{suffix.upper()}"
            if not motion_path.exists():
                motion_path = trash_root / f"{still_path.stem}{suffix.lower()}"
            if motion_path.exists():
                return self._normalize_path(motion_path)
        return None

    def _read_restore_row_lookup(self, trash_root: Path) -> dict[str, dict[str, Any]]:
        row_lookup: dict[str, dict[str, Any]] = {}
        for row in self.lifecycle_service.read_restore_index_rows(trash_root):
            if not isinstance(row, dict):
                continue
            rel_value = row.get("rel")
            if not isinstance(rel_value, str):
                continue
            candidate_path = (
                self.library_root / rel_value
                if self.library_root
                else Path(rel_value)
            )
            row_lookup[str(self._normalize_path(candidate_path))] = dict(row)
        return row_lookup

    def _read_fallback_restore_rows(
        self,
        paths: Iterable[Path],
        *,
        trash_root: Path,
        row_lookup: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        rels_by_path: dict[str, list[str]] = {}
        rels: list[str] = []
        seen: set[str] = set()
        for path in paths:
            path_key = str(self._normalize_path(path))
            row = row_lookup.get(path_key)
            candidates = self._fallback_original_rels(
                path,
                trash_root=trash_root,
                row=row,
            )
            rels_by_path[path_key] = candidates
            for rel in candidates:
                if rel in seen:
                    continue
                seen.add(rel)
                rels.append(rel)
        rows_by_rel = {
            str(rel): dict(row)
            for rel, row in self.lifecycle_service.read_index_rows_by_rels(rels).items()
            if isinstance(row, dict)
        }
        fallback_rows: dict[str, dict[str, Any]] = {}
        for path_key, candidate_rels in rels_by_path.items():
            for rel in candidate_rels:
                row = rows_by_rel.get(rel)
                if row is None:
                    continue
                fallback = dict(row)
                if not fallback.get("original_rel_path"):
                    fallback["original_rel_path"] = rel
                fallback_rows[path_key] = fallback
                break
        return fallback_rows

    def _fallback_original_rels(
        self,
        path: Path,
        *,
        trash_root: Path,
        row: dict[str, Any] | None,
    ) -> list[str]:
        rels: list[str] = []
        seen: set[str] = set()

        def _append(rel: str | None) -> None:
            if not rel or rel in seen:
                return
            seen.add(rel)
            rels.append(rel)

        if row:
            original_rel = row.get("original_rel_path")
            if isinstance(original_rel, str) and self._is_safe_relative_path(
                original_rel,
            ):
                _append(Path(original_rel).as_posix())
            _append(self._original_rel_from_album_metadata(row))

        _append(self._fallback_original_rel(path, trash_root))
        return rels

    def _determine_restore_destination(
        self,
        *,
        row: dict[str, Any],
        filename: str,
        restore_to_root_prompt: RestoreToRootPrompt | None,
    ) -> Path | None:
        assert self.library_root is not None

        def _offer_restore_to_root(
            skip_reason: str,
            decline_reason: str,
        ) -> Path | None:
            if restore_to_root_prompt is None:
                raise LookupError(skip_reason)
            if restore_to_root_prompt(filename):
                return self.library_root
            raise LookupError(decline_reason)

        original_rel = row.get("original_rel_path")
        if isinstance(original_rel, str) and original_rel:
            candidate_path = self.library_root / original_rel
            try:
                candidate_path.relative_to(self.library_root)
            except ValueError:
                pass
            else:
                parent_dir = candidate_path.parent
                if parent_dir.exists():
                    return parent_dir

        album_id = row.get("original_album_id")
        subpath = row.get("original_album_subpath")
        if (
            isinstance(album_id, str)
            and album_id
            and isinstance(subpath, str)
            and subpath
        ):
            album_root = self._find_album_root_by_uuid(album_id)
            if album_root is not None:
                subpath_obj = Path(subpath)
                if not self._is_safe_relative_parts(subpath_obj):
                    return album_root
                destination_path = album_root / subpath_obj
                try:
                    destination_path.relative_to(album_root)
                except ValueError:
                    return album_root
                return destination_path.parent

            return _offer_restore_to_root(
                skip_reason=(
                    f"Original album for {filename} no longer exists; skipping restore."
                ),
                decline_reason=(
                    f"Restore cancelled for {filename} because its original album is unavailable."
                ),
            )

        if isinstance(original_rel, str) and original_rel:
            return _offer_restore_to_root(
                skip_reason=(
                    f"Original album metadata is unavailable for {filename}; skipping restore."
                ),
                decline_reason=(
                    f"Restore cancelled for {filename} because you opted against placing it in the Basic Library root."
                ),
            )
        return _offer_restore_to_root(
            skip_reason=f"Original location is unknown for {filename}; skipping restore.",
            decline_reason=(
                f"Restore cancelled for {filename} because you opted against placing it in the Basic Library root."
            ),
        )

    def _find_album_root_by_uuid(self, album_id: str) -> Path | None:
        cached_album_root = self._album_uuid_cache.get(album_id)
        if cached_album_root is not None:
            if self._album_root_matches_uuid(cached_album_root, album_id):
                return cached_album_root
            self._album_uuid_cache.pop(album_id, None)
        elif album_id in self._album_uuid_cache:
            self._album_uuid_cache.pop(album_id, None)

        if self.library_root is None:
            return None

        for manifest_name in ALBUM_MANIFEST_NAMES:
            for manifest_path in self.library_root.rglob(manifest_name):
                try:
                    payload = read_json(manifest_path)
                except Exception:
                    continue
                if payload.get("id") != album_id:
                    continue
                manifest_parts = Path(manifest_name).parts
                if (
                    manifest_path.name == Path(manifest_name).name
                    and len(manifest_parts) > 1
                ):
                    album_root = manifest_path.parent.parent
                else:
                    album_root = manifest_path.parent
                self._album_uuid_cache[album_id] = album_root
                return album_root

        return None

    def _album_root_matches_uuid(self, album_root: Path, album_id: str) -> bool:
        try:
            if not album_root.exists() or not album_root.is_dir():
                return False
        except OSError:
            return False

        for manifest_name in ALBUM_MANIFEST_NAMES:
            manifest_path = album_root / manifest_name
            if not manifest_path.exists():
                continue
            try:
                payload = read_json(manifest_path)
            except Exception:
                continue
            if payload.get("id") == album_id:
                return True
        return False

    def _original_rel_from_album_metadata(self, row: dict[str, Any]) -> str | None:
        if self.library_root is None:
            return None
        album_id = row.get("original_album_id")
        subpath = row.get("original_album_subpath")
        if not isinstance(album_id, str) or not album_id:
            return None
        if not isinstance(subpath, str) or not subpath:
            return None

        album_root = self._find_album_root_by_uuid(album_id)
        if album_root is None:
            return None

        subpath_obj = Path(subpath)
        if not self._is_safe_relative_parts(subpath_obj):
            return None

        try:
            relative = (album_root / subpath_obj).relative_to(self.library_root)
        except ValueError:
            return None
        return relative.as_posix()

    def _fallback_original_rel(self, path: Path, trash_root: Path) -> str | None:
        try:
            relative = path.relative_to(trash_root)
        except ValueError:
            return None
        if not self._is_safe_relative_parts(relative):
            return None
        return relative.as_posix()

    def _metadata_for_path(
        self,
        path: Path,
        metadata_lookup: MetadataLookup | None,
    ) -> dict[str, Any] | None:
        if metadata_lookup is None:
            return None
        metadata = metadata_lookup(path)
        return metadata if isinstance(metadata, dict) else None

    def _rejected_move(
        self,
        operation: str,
        *,
        source_root: Path | None = None,
        destination_root: Path | None = None,
        trash_root: Path | None = None,
        errors: list[str] | None = None,
        finished_message: str | None = None,
    ) -> AssetMovePlan:
        return AssetMovePlan(
            operation=operation,
            source_root=source_root,
            destination_root=destination_root,
            library_root=self.library_root,
            trash_root=trash_root,
            asset_lifecycle_service=self.lifecycle_service,
            errors=list(errors or []),
            finished_message=finished_message,
            accepted=False,
        )

    @staticmethod
    def _normalize_path(path: Path) -> Path:
        try:
            return path.resolve()
        except OSError:
            return path

    @classmethod
    def _normalize_optional_path(cls, path: Path | None) -> Path | None:
        return cls._normalize_path(Path(path)) if path is not None else None

    @classmethod
    def _paths_equal(cls, left: Path | None, right: Path | None) -> bool:
        if left is None or right is None:
            return False
        return cls._normalize_path(Path(left)) == cls._normalize_path(Path(right))

    @staticmethod
    def _is_safe_relative_path(value: str) -> bool:
        return LibraryAssetOperationService._is_safe_relative_parts(Path(value))

    @staticmethod
    def _is_safe_relative_parts(path: Path) -> bool:
        return not path.is_absolute() and all(part != ".." for part in path.parts)


__all__ = [
    "AssetMovePlan",
    "AssetRestorePlan",
    "LibraryAssetOperationService",
]
