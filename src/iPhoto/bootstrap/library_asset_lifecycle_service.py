"""Library-scoped asset lifecycle commands for move/delete/restore flows."""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..application.ports import AssetRepositoryPort
from ..cache.index_store import get_global_repository
from ..cache.lock import FileLock
from ..config import ALBUM_MANIFEST_NAMES, RECENTLY_DELETED_DIR_NAME
from ..errors import IPhotoError
from ..index_sync_service import prune_index_scope
from ..io.scanner_adapter import process_media_paths
from ..media_classifier import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from ..schemas import validate_album
from ..utils.jsonio import read_json, write_json
from ..utils.logging import get_logger
from ..utils.pathutils import ensure_work_dir, resolve_work_dir
from .library_scan_service import LibraryScanService

LOGGER = get_logger()


@dataclass(frozen=True)
class AssetLifecycleResult:
    """Outcome of applying index/link side effects after file moves."""

    source_index_ok: bool = True
    destination_index_ok: bool = True
    errors: list[str] = field(default_factory=list)


class LibraryAssetLifecycleService:
    """Own move/delete/restore index updates for one active library session."""

    def __init__(
        self,
        library_root: Path | None,
        *,
        scan_service: LibraryScanService | None = None,
        repository_factory: Callable[[Path], AssetRepositoryPort] | None = None,
        media_processor: Callable[
            [Path, list[Path], list[Path]],
            Iterable[dict[str, Any]],
        ]
        | None = None,
    ) -> None:
        self.library_root = Path(library_root) if library_root is not None else None
        self._scan_service = scan_service
        self._repository_factory = repository_factory or get_global_repository
        self._media_processor = media_processor or process_media_paths
        self._album_root_cache: dict[str, Path | None] = {}

    def apply_move(
        self,
        *,
        moved: Iterable[tuple[Path, Path]],
        source_root: Path,
        destination_root: Path,
        trash_root: Path | None = None,
        is_restore: bool = False,
    ) -> AssetLifecycleResult:
        """Apply repository and Live Photo side effects for completed file moves."""

        moved_pairs = [(Path(source), Path(target)) for source, target in moved]
        if not moved_pairs:
            return AssetLifecycleResult()

        errors: list[str] = []
        source_index_ok = True
        destination_index_ok = True
        cached_source_rows: dict[str, dict[str, Any]] = {}

        try:
            cached_source_rows = self._remove_source_rows(
                moved_pairs,
                source_root=Path(source_root),
            )
        except (IPhotoError, sqlite3.Error, OSError) as exc:
            source_index_ok = False
            errors.append(str(exc))

        try:
            self._append_destination_rows(
                moved_pairs,
                destination_root=Path(destination_root),
                trash_root=Path(trash_root) if trash_root is not None else None,
                is_restore=is_restore,
                cached_source_rows=cached_source_rows,
            )
        except (IPhotoError, sqlite3.Error, OSError) as exc:
            destination_index_ok = False
            errors.append(str(exc))

        try:
            self._pair_after_move(Path(source_root))
        except IPhotoError as exc:
            errors.append(f"Failed to pair Live Photos: {exc}")

        return AssetLifecycleResult(
            source_index_ok=source_index_ok,
            destination_index_ok=destination_index_ok,
            errors=errors,
        )

    def read_restore_index_rows(self, trash_root: Path) -> list[dict[str, Any]]:
        """Read indexed rows for Recently Deleted without exposing the repository."""

        root = self._repository_root_for_read(Path(trash_root))
        repository = self._repository(root)
        album_path = self._album_path(Path(trash_root))
        if album_path:
            rows = repository.read_album_assets(
                album_path,
                include_subalbums=True,
                filter_hidden=False,
            )
        elif self.library_root is None:
            rows = repository.read_all(filter_hidden=False)
        else:
            rows = ()
        return [dict(row) for row in rows if isinstance(row, dict)]

    def read_index_rows_by_rels(
        self,
        rels: Iterable[str],
    ) -> dict[str, dict[str, Any]]:
        """Read indexed rows by library-relative paths."""

        if self.library_root is None:
            return {}
        repository = self._repository(self.library_root)
        return {
            str(rel): dict(row)
            for rel, row in repository.get_rows_by_rels(rels).items()
            if isinstance(row, dict)
        }

    def preserve_trash_metadata(
        self,
        trash_root: Path,
        rows: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge restore metadata from the existing trash index into fresh rows."""

        materialized = [dict(row) for row in rows]
        preserved_fields = (
            "original_rel_path",
            "original_album_id",
            "original_album_subpath",
        )
        try:
            preserved_rows = {
                str(row["rel"]): row
                for row in self.read_restore_index_rows(trash_root)
                if row.get("rel") is not None
                and any(row.get(field) is not None for field in preserved_fields)
            }
        except (IPhotoError, sqlite3.Error):
            return materialized

        if not preserved_rows:
            return materialized

        for row in materialized:
            rel_value = row.get("rel")
            if rel_value is None:
                continue
            cached_row = preserved_rows.get(str(rel_value))
            if cached_row is None:
                continue
            for preserved_field in preserved_fields:
                if cached_row.get(preserved_field) is not None:
                    row[preserved_field] = cached_row[preserved_field]
        return materialized

    def cleanup_deleted_index(self, trash_root: Path) -> int:
        """Drop stale Recently Deleted rows for this library session."""

        try:
            root = self._repository_root_for_read(Path(trash_root))
            repository = self._repository(root)
            album_path = self._album_path(Path(trash_root))
            if album_path:
                rows = repository.read_album_assets(
                    album_path,
                    include_subalbums=True,
                    filter_hidden=False,
                )
                process_root = root
            elif self.library_root is None:
                rows = repository.read_all(filter_hidden=False)
                process_root = Path(trash_root)
            else:
                return 0

            try:
                has_files = next(Path(trash_root).iterdir(), None) is not None
            except OSError:
                has_files = False

            missing_rels = [
                row_rel
                for row_rel in (row.get("rel") for row in rows)
                if isinstance(row_rel, str)
                and (not has_files or not (process_root / row_rel).exists())
            ]
            if missing_rels:
                repository.remove_rows(missing_rels)
            return len(missing_rels)
        except (IPhotoError, sqlite3.Error, OSError) as exc:
            LOGGER.debug("Recently Deleted cleanup skipped: %s", exc)
            return 0

    def reconcile_missing_scan_rows(
        self,
        root: Path,
        materialised_rows: Iterable[dict[str, Any]],
    ) -> int:
        """Explicitly prune stale scan rows for a completed scan scope."""

        scan_root = Path(root)
        repository_root = self._repository_root_for_read(scan_root)
        repository = self._repository(repository_root)
        return prune_index_scope(
            scan_root,
            materialised_rows,
            library_root=self.library_root,
            repository=repository,
        )

    def _remove_source_rows(
        self,
        moved: list[tuple[Path, Path]],
        *,
        source_root: Path,
    ) -> dict[str, dict[str, Any]]:
        index_root = self._repository_root_for_source(source_root)
        repository = self._repository(index_root)
        rels: list[str] = []
        rel_to_original: dict[str, str] = {}

        for original, _ in moved:
            rel = self._relative_for_index(original, base=source_root)
            if rel is None:
                continue
            rels.append(rel)
            rel_to_original[rel] = str(original)

        cached_source_rows: dict[str, dict[str, Any]] = {}
        if rels:
            for rel, row_data in repository.get_rows_by_rels(rels).items():
                original_str = rel_to_original.get(rel)
                if original_str:
                    cached_source_rows[original_str] = dict(row_data)
            repository.remove_rows(rels)
        return cached_source_rows

    def _append_destination_rows(
        self,
        moved: list[tuple[Path, Path]],
        *,
        destination_root: Path,
        trash_root: Path | None,
        is_restore: bool,
        cached_source_rows: dict[str, dict[str, Any]],
    ) -> None:
        index_root = self._repository_root_for_destination(destination_root)
        repository = self._repository(index_root)
        process_root = self._process_root(destination_root)
        is_trash_destination = self._paths_equal(destination_root, trash_root)

        if is_trash_destination:
            self._cleanup_stale_trash_rows(repository, process_root)

        reused_rows: list[dict[str, Any]] = []
        uncached_images: list[Path] = []
        uncached_videos: list[Path] = []

        for original, target in moved:
            cached = cached_source_rows.get(str(original))
            if cached:
                row = dict(cached)
                row["rel"] = self._target_rel(target, process_root)
                row["parent_album_path"] = self._parent_album_path(row["rel"])
                if is_restore:
                    row.pop("original_rel_path", None)
                    row.pop("original_album_id", None)
                    row.pop("original_album_subpath", None)
                reused_rows.append(row)
                continue

            suffix = target.suffix.lower()
            if suffix in IMAGE_EXTENSIONS:
                uncached_images.append(target)
            elif suffix in VIDEO_EXTENSIONS:
                uncached_videos.append(target)
            else:
                uncached_images.append(target)

        freshly_scanned: list[dict[str, Any]] = []
        if uncached_images or uncached_videos:
            freshly_scanned = [
                dict(row)
                for row in self._media_processor(
                    process_root,
                    uncached_images,
                    uncached_videos,
                )
            ]

        new_rows = reused_rows + freshly_scanned
        if is_trash_destination and not is_restore:
            new_rows = self._annotate_trash_rows(new_rows, moved, process_root)

        if new_rows:
            repository.append_rows(new_rows)

    def _cleanup_stale_trash_rows(
        self,
        repository: AssetRepositoryPort,
        process_root: Path,
    ) -> None:
        try:
            existing_trash_rows = list(
                repository.read_album_assets(
                    RECENTLY_DELETED_DIR_NAME,
                    include_subalbums=True,
                    filter_hidden=False,
                )
            )
            missing_rels = [
                row_rel
                for row_rel in (row.get("rel") for row in existing_trash_rows)
                if isinstance(row_rel, str)
                and not (process_root / row_rel).exists()
            ]
            if missing_rels:
                repository.remove_rows(missing_rels)
        except (IPhotoError, sqlite3.Error, OSError) as exc:
            LOGGER.debug("Trash cleanup during move skipped: %s", exc)

    def _annotate_trash_rows(
        self,
        rows: list[dict[str, Any]],
        moved: list[tuple[Path, Path]],
        process_root: Path,
    ) -> list[dict[str, Any]]:
        if self.library_root is None:
            raise IPhotoError("Library root is required to annotate trash index entries.")

        source_lookup: dict[str, Path] = {}
        for original, target in moved:
            target_key = self._normalised_string(target)
            if target_key:
                source_lookup[target_key] = original

        annotated_rows: list[dict[str, Any]] = []
        library_root_key = self._normalised_string(self.library_root)
        album_uuid_cache: dict[str, str | None] = {}
        for row in rows:
            rel_value = row.get("rel")
            if not isinstance(rel_value, str):
                annotated_rows.append(row)
                continue

            absolute_target = process_root / rel_value
            target_key = self._normalised_string(absolute_target)
            original_path = source_lookup.get(target_key) if target_key else None
            if original_path is None:
                annotated_rows.append(row)
                continue

            original_relative = self._library_relative(original_path)
            original_album_id: str | None = None
            original_album_subpath: str | None = None

            album_root = self._discover_album_root(original_path.parent, library_root_key)
            if album_root is not None:
                album_key = self._normalised_string(album_root)
                if album_key is not None:
                    if album_key not in album_uuid_cache:
                        album_uuid_cache[album_key] = self._read_album_uuid(album_root)
                    original_album_id = album_uuid_cache.get(album_key)

                relative_to_album = self._relative_to(original_path, album_root)
                if relative_to_album is not None:
                    original_album_subpath = relative_to_album.as_posix()

            enriched = dict(row)
            if original_relative is not None:
                enriched["original_rel_path"] = original_relative
            enriched["original_album_id"] = original_album_id
            enriched["original_album_subpath"] = original_album_subpath
            annotated_rows.append(enriched)
        return annotated_rows

    def _pair_after_move(self, source_root: Path) -> None:
        pair_root = self.library_root or source_root
        self._active_scan_service(pair_root).pair_album(pair_root)

    def _active_scan_service(self, root: Path) -> LibraryScanService:
        if self._scan_service is None:
            self._scan_service = LibraryScanService(root)
        return self._scan_service

    def _repository(self, root: Path) -> AssetRepositoryPort:
        return self._repository_factory(Path(root))

    def _repository_root_for_source(self, source_root: Path) -> Path:
        return self.library_root or Path(source_root)

    def _repository_root_for_destination(self, destination_root: Path) -> Path:
        return self.library_root or Path(destination_root)

    def _repository_root_for_read(self, root: Path) -> Path:
        return self.library_root or Path(root)

    def _process_root(self, destination_root: Path) -> Path:
        return self.library_root or Path(destination_root)

    def _relative_for_index(self, path: Path, *, base: Path) -> str | None:
        root = self.library_root or base
        relative = self._relative_to(path, root)
        return relative.as_posix() if relative is not None else None

    def _target_rel(self, target: Path, process_root: Path) -> str:
        relative = self._relative_to(target, process_root)
        return relative.as_posix() if relative is not None else target.name

    @staticmethod
    def _parent_album_path(rel: str) -> str:
        parent = Path(rel).parent
        return parent.as_posix() if parent != Path(".") else ""

    def _album_path(self, root: Path) -> str | None:
        if self.library_root is None:
            return None
        relative = self._relative_to(root, self.library_root)
        if relative is None or relative == Path("."):
            return None
        return relative.as_posix()

    def _library_relative(self, original_path: Path) -> str | None:
        if self.library_root is None:
            return None
        relative = self._relative_to(original_path, self.library_root)
        if relative is not None:
            return relative.as_posix()
        try:
            relative_str = os.path.relpath(original_path, self.library_root)
        except (OSError, ValueError):
            return None
        if relative_str.startswith(".."):
            return None
        try:
            candidate = (self.library_root / relative_str).resolve()
            root_resolved = self.library_root.resolve()
            candidate.relative_to(root_resolved)
        except (OSError, ValueError):
            return None
        return Path(relative_str).as_posix()

    def _discover_album_root(
        self,
        start: Path,
        library_root_key: str | None,
    ) -> Path | None:
        key = self._normalised_string(start)
        if key is None:
            return None
        cached = self._album_root_cache.get(key, ...)
        if cached is not ...:
            return cached

        try:
            current = start.resolve()
        except OSError:
            current = start

        visited: list[Path] = []
        while True:
            visited.append(current)
            if resolve_work_dir(current) is not None:
                album_root: Path | None = current
                break
            parent = current.parent
            if parent == current:
                album_root = None
                break
            if (
                library_root_key is not None
                and self._normalised_string(parent) == library_root_key
            ):
                album_root = parent if resolve_work_dir(parent) is not None else None
                visited.append(parent)
                break
            current = parent

        for candidate in visited:
            candidate_key = self._normalised_string(candidate)
            if candidate_key is not None:
                self._album_root_cache[candidate_key] = album_root
        return album_root

    def _read_album_uuid(self, album_root: Path) -> str | None:
        if not album_root.exists():
            return None

        manifest_path: Path | None = None
        for manifest_name in ALBUM_MANIFEST_NAMES:
            candidate = album_root / manifest_name
            if candidate.exists():
                manifest_path = candidate
                break

        changed = False

        def _default_manifest() -> dict[str, Any]:
            return {
                "schema": "iPhoto/album@1",
                "id": str(uuid.uuid4()),
                "title": album_root.name,
                "filters": {},
            }

        if manifest_path is None:
            manifest_path = album_root / ALBUM_MANIFEST_NAMES[0]
            manifest = _default_manifest()
            changed = True
        else:
            try:
                manifest = read_json(manifest_path)
            except IPhotoError:
                manifest = _default_manifest()
                changed = True
            if not isinstance(manifest, dict):
                manifest = _default_manifest()
                changed = True

        if manifest.get("schema") != "iPhoto/album@1":
            manifest["schema"] = "iPhoto/album@1"
            changed = True

        title = manifest.get("title")
        if not isinstance(title, str) or not title or title != album_root.name:
            manifest["title"] = album_root.name
            changed = True

        manifest_id = manifest.get("id")
        if not isinstance(manifest_id, str) or not manifest_id:
            manifest_id = str(uuid.uuid4())
            manifest["id"] = manifest_id
            changed = True

        filters = manifest.get("filters")
        if filters is None or not isinstance(filters, dict):
            manifest["filters"] = {}
            changed = True

        try:
            validate_album(manifest)
        except IPhotoError:
            manifest = _default_manifest()
            manifest_id = manifest["id"]
            changed = True
            validate_album(manifest)

        if changed:
            try:
                backup_dir = ensure_work_dir(album_root) / "manifest.bak"
                with FileLock(album_root, "manifest"):
                    write_json(manifest_path, manifest, backup_dir=backup_dir)
            except (IPhotoError, OSError) as exc:
                LOGGER.warning(
                    "Failed to persist manifest updates for %s: %s",
                    album_root,
                    exc,
                )

        return manifest_id

    @staticmethod
    def _relative_to(path: Path, root: Path) -> Path | None:
        try:
            return path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            try:
                return path.relative_to(root)
            except ValueError:
                return None

    @staticmethod
    def _normalised_string(path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return str(path.resolve())
        except OSError:
            return str(path)

    def _paths_equal(self, left: Path | None, right: Path | None) -> bool:
        if left is None or right is None:
            return False
        if left == right:
            return True
        return self._normalised_string(left) == self._normalised_string(right)


__all__ = ["AssetLifecycleResult", "LibraryAssetLifecycleService"]
