"""Library-scoped scan command surface for vNext session entry points."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..application.ports import AssetRepositoryPort, MediaScannerPort
from ..application.use_cases.scan_library import (
    ScanLibraryRequest,
    ScanLibraryResult,
    ScanLibraryUseCase,
)
from ..cache.index_store import get_global_repository
from ..config import (
    ALBUM_MANIFEST_NAMES,
    DEFAULT_EXCLUDE,
    DEFAULT_INCLUDE,
    RECENTLY_DELETED_DIR_NAME,
)
from ..errors import (
    AlbumNotFoundError,
    IndexCorruptedError,
    IPhotoError,
    ManifestInvalidError,
)
from ..index_sync_service import (
    ensure_links,
    load_incremental_index_cache,
    prune_index_scope,
    update_index_snapshot,
)
from ..infrastructure.services.filesystem_media_scanner import FilesystemMediaScanner
from ..io.scanner_adapter import process_media_paths
from ..media_classifier import ALL_IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from ..path_normalizer import compute_album_path
from ..utils.jsonio import read_json
from ..utils.logging import get_logger
from ..utils.pathutils import ensure_work_dir, resolve_work_dir

if TYPE_CHECKING:  # pragma: no cover
    from ..models.types import LiveGroup

LOGGER = get_logger()


@dataclass(frozen=True)
class AlbumReport:
    """Small CLI/report DTO for one album or library scope."""

    title: str | None
    asset_count: int
    live_pair_count: int


@dataclass(frozen=True)
class AlbumOpenPreparation:
    """Result of preparing an album for presentation after it is opened."""

    asset_count: int
    rows: list[dict[str, Any]] | None = None
    scanned: bool = False


class _EmptyScanner(MediaScannerPort):
    """Scanner placeholder used when only chunk merging is needed."""

    def scan(
        self,
        _root: Path,
        _include: Iterable[str],
        _exclude: Iterable[str],
        *,
        existing_index: dict[str, dict[str, Any]] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        return iter(())


def merge_scan_chunk_with_repository(
    repository: AssetRepositoryPort,
    *,
    root: Path,
    include: Iterable[str],
    exclude: Iterable[str],
    chunk: list[dict[str, Any]],
    chunk_callback: Callable[[list[dict[str, Any]]], None] | None = None,
    batch_failed_callback: Callable[[int], None] | None = None,
) -> int:
    """Persist one scan chunk through the application scan merge policy."""

    use_case = ScanLibraryUseCase(
        scanner=_EmptyScanner(),
        asset_repository=repository,
    )
    return use_case.merge_chunk(
        chunk,
        ScanLibraryRequest(
            root=root,
            include=include,
            exclude=exclude,
            chunk_callback=chunk_callback,
            batch_failed_callback=batch_failed_callback,
        ),
    )


class LibraryScanService:
    """Coordinate scans through the active library session.

    This is a migration adapter: it centralizes concrete index-store and scanner
    wiring at the session/runtime boundary while the repository consolidation
    work remains open.
    """

    def __init__(
        self,
        library_root: Path,
        *,
        scanner: MediaScannerPort | None = None,
        repository_factory: Callable[[Path], AssetRepositoryPort] | None = None,
    ) -> None:
        self.library_root = Path(library_root)
        self._scanner = scanner or FilesystemMediaScanner()
        self._repository_factory = repository_factory or get_global_repository

    def prepare_album_open(
        self,
        root: Path,
        *,
        autoscan: bool = True,
        hydrate_index: bool = True,
        sync_manifest_favorites: bool = False,
    ) -> AlbumOpenPreparation:
        """Prepare index/link state for an opened album.

        ``hydrate_index=False`` keeps startup and GUI navigation lazy by using a
        scoped count instead of loading all rows. If the scope is empty and
        ``autoscan`` is enabled, the shared scan use case is used and finalized.
        """

        scan_root = Path(root)
        rows: list[dict[str, Any]] | None = None
        scanned = False

        if hydrate_index:
            rows = self.read_scoped_assets(
                scan_root,
                filter_hidden=self._album_path(scan_root) is not None,
            )
            asset_count = len(rows)
        else:
            try:
                asset_count = self.count_assets(scan_root, filter_hidden=True)
            except Exception as exc:
                if not _is_recoverable_index_error(exc):
                    raise
                asset_count = 0

            if asset_count == 0 and autoscan:
                result = self.scan_album(scan_root, persist_chunks=False)
                self.finalize_scan(scan_root, result.rows)
                self.reconcile_missing_scan_rows(scan_root, result.rows)
                rows = result.rows
                asset_count = len(rows)
                scanned = True
            elif asset_count == 0:
                # Preserve legacy open behavior: an empty lazy open still
                # materializes empty link state for the opened album.
                rows = []

        if rows is not None and not scanned:
            self.ensure_links_for_rows(scan_root, rows)

        if sync_manifest_favorites:
            self.sync_manifest_favorites(scan_root, suppress_recoverable=True)

        return AlbumOpenPreparation(
            asset_count=asset_count,
            rows=rows,
            scanned=scanned,
        )

    def scan_album(
        self,
        root: Path,
        *,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        chunk_callback: Callable[[list[dict[str, Any]]], None] | None = None,
        batch_failed_callback: Callable[[int], None] | None = None,
        chunk_size: int = 50,
        persist_chunks: bool = False,
    ) -> ScanLibraryResult:
        """Scan *root* using the shared application use case."""

        scan_root = Path(root)
        resolved_include, resolved_exclude = self.scan_filters(
            scan_root,
            include=include,
            exclude=exclude,
        )
        repository = self._repository()
        existing_index = load_incremental_index_cache(
            scan_root,
            library_root=self.library_root,
            repository=repository,
        )

        use_case = ScanLibraryUseCase(
            scanner=self._scanner,
            asset_repository=repository,
        )
        return use_case.execute(
            ScanLibraryRequest(
                root=scan_root,
                include=resolved_include,
                exclude=resolved_exclude,
                existing_index=existing_index,
                progress_callback=progress_callback,
                is_cancelled=is_cancelled,
                row_transform=self._library_relative_transform(scan_root),
                chunk_callback=chunk_callback,
                batch_failed_callback=batch_failed_callback,
                chunk_size=chunk_size,
                persist_chunks=persist_chunks,
            )
        )

    def rescan_album(
        self,
        root: Path,
        *,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        sync_manifest_favorites: bool = False,
        pair_live: bool = True,
    ) -> list[dict[str, Any]]:
        """Synchronously rebuild one scan scope and persist follow-up state."""

        result = self.scan_album(
            root,
            include=include,
            exclude=exclude,
            progress_callback=progress_callback,
            is_cancelled=is_cancelled,
            persist_chunks=False,
        )
        rows = self.finalize_scan_result(root, result.rows, pair_live=pair_live)
        if sync_manifest_favorites:
            self.sync_manifest_favorites(Path(root))
        return rows

    def finalize_scan(self, root: Path, rows: Iterable[dict[str, Any]]) -> None:
        """Persist additive scan side effects after a successful scan."""

        scan_root = Path(root)
        materialized_rows = [dict(row) for row in rows]
        repository = self._repository()
        update_index_snapshot(
            scan_root,
            materialized_rows,
            library_root=self.library_root,
            repository=repository,
        )
        self.ensure_links_for_rows(scan_root, materialized_rows, repository=repository)

    def finalize_scan_result(
        self,
        root: Path,
        rows: Iterable[dict[str, Any]],
        *,
        pair_live: bool = True,
    ) -> list[dict[str, Any]]:
        """Persist scan completion side effects for one scope.

        This is the higher-level runtime hook used by GUI adapters after a scan
        completes. It preserves Recently Deleted restore metadata, persists the
        snapshot and derived links, prunes stale rows, and optionally refreshes
        Live Photo pairing state.
        """

        from .library_asset_lifecycle_service import LibraryAssetLifecycleService

        scan_root = Path(root)
        lifecycle_service = LibraryAssetLifecycleService(
            self.library_root,
            scan_service=self,
        )
        materialized_rows = [dict(row) for row in rows]

        if scan_root.name == RECENTLY_DELETED_DIR_NAME:
            materialized_rows = lifecycle_service.preserve_trash_metadata(
                scan_root,
                materialized_rows,
            )

        self.finalize_scan(scan_root, materialized_rows)
        lifecycle_service.reconcile_missing_scan_rows(scan_root, materialized_rows)
        if pair_live:
            self.pair_album(scan_root)
        return materialized_rows

    def refresh_restored_album(
        self,
        root: Path,
        *,
        progress_callback: Callable[[int, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        pair_live: bool = True,
    ) -> list[dict[str, Any]]:
        """Refresh one restored album so gallery state and links stay current."""

        return self.rescan_album(
            root,
            progress_callback=progress_callback,
            is_cancelled=is_cancelled,
            pair_live=pair_live,
        )

    def reconcile_missing_scan_rows(
        self,
        root: Path,
        rows: Iterable[dict[str, Any]],
    ) -> int:
        """Prune stale rows for a completed full scan scope."""

        scan_root = Path(root)
        materialized_rows = [dict(row) for row in rows]
        repository = self._repository()
        return prune_index_scope(
            scan_root,
            materialized_rows,
            library_root=self.library_root,
            repository=repository,
        )

    def scan_specific_files(
        self,
        root: Path,
        files: Iterable[Path],
    ) -> list[dict[str, Any]]:
        """Scan and merge a known set of files under *root*."""

        scan_root = Path(root)
        image_paths: list[Path] = []
        video_paths: list[Path] = []
        for raw_path in files:
            candidate = Path(raw_path)
            suffix = candidate.suffix.lower()
            if suffix in ALL_IMAGE_EXTENSIONS:
                image_paths.append(candidate)
            elif suffix in VIDEO_EXTENSIONS:
                video_paths.append(candidate)

        rows = [
            dict(row)
            for row in process_media_paths(scan_root, image_paths, video_paths)
        ]
        transform = self._library_relative_transform(scan_root)
        transformed = [transform(row) for row in rows]
        if transformed:
            self._repository().merge_scan_rows(transformed)
        return transformed

    def pair_album(self, root: Path) -> "list[LiveGroup]":
        """Rebuild Live Photo roles and derived links for *root*."""

        scan_root = Path(root)
        album_path = self._album_path(scan_root)
        repository = self._repository()
        if album_path:
            rows = list(
                repository.read_album_assets(
                    album_path,
                    include_subalbums=True,
                    filter_hidden=False,
                )
            )
            rows = self._album_relative_rows(rows, album_path)
        else:
            rows = list(repository.read_all(filter_hidden=False))
        return ensure_links(
            scan_root,
            rows,
            library_root=self.library_root,
            repository=repository,
        )

    def report_album(self, root: Path) -> AlbumReport:
        """Return the asset and Live Photo counts for *root*."""

        scan_root = Path(root)
        manifest = self._load_manifest(scan_root)
        album_path = self._album_path(scan_root)
        repository = self._repository()
        if album_path:
            asset_count = repository.count(
                filter_hidden=False,
                album_path=album_path,
                include_subalbums=True,
            )
        else:
            asset_count = repository.count(filter_hidden=False)

        live_pair_count = self._read_live_pair_count(scan_root)
        if live_pair_count is None:
            live_pair_count = len(self.pair_album(scan_root))

        title = manifest.get("title")
        return AlbumReport(
            title=title if isinstance(title, str) else None,
            asset_count=asset_count,
            live_pair_count=live_pair_count,
        )

    def count_assets(self, root: Path, *, filter_hidden: bool = True) -> int:
        """Return the number of indexed assets in the scope rooted at *root*."""

        scan_root = Path(root)
        album_path = self._album_path(scan_root)
        repository = self._repository()
        if album_path:
            return repository.count(
                filter_hidden=filter_hidden,
                album_path=album_path,
                include_subalbums=True,
            )
        return repository.count(filter_hidden=filter_hidden)

    def read_scoped_assets(
        self,
        root: Path,
        *,
        filter_hidden: bool = True,
    ) -> list[dict[str, Any]]:
        """Read indexed rows scoped to *root* without exposing the repository."""

        scan_root = Path(root)
        album_path = self._album_path(scan_root)
        repository = self._repository()
        if album_path:
            rows = repository.read_album_assets(
                album_path,
                include_subalbums=True,
                filter_hidden=filter_hidden,
            )
        else:
            rows = repository.read_all(filter_hidden=filter_hidden)
        return [dict(row) for row in rows]

    def ensure_links_for_rows(
        self,
        root: Path,
        rows: Iterable[dict[str, Any]],
        *,
        repository: AssetRepositoryPort | None = None,
    ) -> "list[LiveGroup]":
        """Rebuild links using already materialized rows for *root*."""

        scan_root = Path(root)
        materialized = [dict(row) for row in rows]
        active_repository = repository or self._repository()
        album_path = self._album_path(scan_root)
        if album_path:
            materialized = self._album_relative_rows(materialized, album_path)
        return ensure_links(
            scan_root,
            materialized,
            library_root=self.library_root,
            repository=active_repository,
        )

    def sync_manifest_favorites(
        self,
        root: Path,
        *,
        suppress_recoverable: bool = False,
    ) -> None:
        """Compatibility sync from album manifest favorites to the index."""

        manifest = self._load_manifest(Path(root))
        sync_favorites = getattr(self._repository(), "sync_favorites", None)
        if not callable(sync_favorites):
            return
        try:
            sync_favorites(manifest.get("featured", []))
        except Exception as exc:
            if not suppress_recoverable or not _is_recoverable_index_error(exc):
                raise
            LOGGER.warning(
                "sync_favorites failed for %s [%s]: %s",
                root,
                type(exc).__name__,
                exc,
            )

    def scan_filters(
        self,
        root: Path,
        *,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Resolve manifest filters, falling back to project defaults."""

        manifest = self._load_manifest(root)
        filters = manifest.get("filters", {})
        if not isinstance(filters, dict):
            filters = {}
        resolved_include = include if include is not None else filters.get("include")
        resolved_exclude = exclude if exclude is not None else filters.get("exclude")
        if not isinstance(resolved_include, Iterable) or isinstance(
            resolved_include,
            (str, bytes),
        ):
            resolved_include = DEFAULT_INCLUDE
        if not isinstance(resolved_exclude, Iterable) or isinstance(
            resolved_exclude,
            (str, bytes),
        ):
            resolved_exclude = DEFAULT_EXCLUDE
        return list(resolved_include), list(resolved_exclude)

    def _repository(self) -> AssetRepositoryPort:
        return self._repository_factory(self.library_root)

    def _load_manifest(self, root: Path) -> dict[str, Any]:
        if not root.exists():
            raise AlbumNotFoundError(f"Album directory does not exist: {root}")

        ensure_work_dir(root)
        for name in ALBUM_MANIFEST_NAMES:
            candidate = root / name
            if not candidate.exists():
                continue
            try:
                manifest = read_json(candidate)
            except IPhotoError:
                break
            if isinstance(manifest, dict):
                return manifest

        return {
            "schema": "iPhoto/album@1",
            "title": root.name,
            "filters": {},
            "featured": [],
        }

    def _album_path(self, root: Path) -> str | None:
        return compute_album_path(root, self.library_root)

    def _library_relative_transform(
        self,
        root: Path,
    ) -> Callable[[dict[str, Any]], dict[str, Any]]:
        album_path = self._album_path(root)

        def transform(row: dict[str, Any]) -> dict[str, Any]:
            if album_path and "rel" in row:
                row["rel"] = f"{album_path}/{row['rel']}"
            return row

        return transform

    def _album_relative_rows(
        self,
        rows: Iterable[dict[str, Any]],
        album_path: str,
    ) -> list[dict[str, Any]]:
        prefix = album_path + "/"
        album_rows: list[dict[str, Any]] = []
        for row in rows:
            rel = row.get("rel", "")
            if isinstance(rel, str) and rel.startswith(prefix):
                adjusted = dict(row)
                adjusted["rel"] = rel[len(prefix):]
                album_rows.append(adjusted)
            elif isinstance(rel, str) and "/" not in rel:
                album_rows.append(dict(row))
        return album_rows

    def _read_live_pair_count(self, root: Path) -> int | None:
        work_dir = resolve_work_dir(root)
        links_path = work_dir / "links.json" if work_dir is not None else None
        if links_path is None or not links_path.exists():
            return None
        try:
            payload = read_json(links_path)
        except IPhotoError:
            return None
        groups = payload.get("live_groups") if isinstance(payload, dict) else None
        if isinstance(groups, list):
            return len(groups)
        return None


def _is_recoverable_index_error(exc: Exception) -> bool:
    return isinstance(exc, (sqlite3.Error, IndexCorruptedError, ManifestInvalidError))


__all__ = [
    "AlbumOpenPreparation",
    "AlbumReport",
    "LibraryScanService",
    "merge_scan_chunk_with_repository",
]
