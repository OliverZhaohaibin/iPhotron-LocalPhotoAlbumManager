"""Library-scoped scan command surface for vNext session entry points."""

from __future__ import annotations

import inspect
import sqlite3
import stat
import time
import uuid
from collections.abc import Callable, Iterable
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
from ..domain.models.scan import ScanStage
from ..domain.models.scan import ScanBatchCommitted
from ..io.scanner_adapter import process_media_paths
from ..media_classifier import ALL_IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from ..path_normalizer import compute_album_path
from ..utils.hashutils import compute_file_id
from ..utils.jsonio import read_json
from ..utils.logging import get_logger
from ..utils.pathutils import ensure_work_dir, resolve_work_dir

if TYPE_CHECKING:  # pragma: no cover
    from ..domain.models.core import LiveGroup

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
        self._scanner = scanner or FilesystemMediaScanner(
            thumbnail_cache_dir=self._thumbnail_cache_dir(),
        )
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
                rows = self.finalize_scan_result(
                    scan_root,
                    result.rows,
                    pair_live=False,
                    preserve_modified_after_ms=result.scan_started_at_ms,
                    current_scan_job_id=result.scan_job_id,
                )
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
        scan_batch_callback: Callable[[ScanBatchCommitted], None] | None = None,
        batch_failed_callback: Callable[[int], None] | None = None,
        chunk_size: int = 500,
        visible_publish_size: int = 100,
        max_chunk_interval_ms: float | None = None,
        persist_chunks: bool = False,
        root_only: bool = False,
    ) -> ScanLibraryResult:
        """Scan *root* using the shared application use case."""

        scan_started_ms = _monotonic_ms()
        scan_started_at_ms = _utc_ms()
        stage_elapsed_ms: dict[str, float] = {}
        scan_root = Path(root)
        resolved_include, resolved_exclude = self.scan_filters(
            scan_root,
            include=include,
            exclude=exclude,
        )
        repository = self._repository()
        scan_job_id = f"scan_{uuid.uuid4().hex}"
        create_scan_job = getattr(repository, "create_scan_job", None)
        update_scan_job_stage = getattr(repository, "update_scan_job_stage", None)
        append_scan_event = getattr(repository, "append_scan_event", None)
        scan_scope = self._scan_scope(scan_root, root_only=root_only)
        if callable(create_scan_job):
            create_scan_job(
                job_id=scan_job_id,
                root=scan_root.as_posix(),
                scope=scan_scope,
                status="running",
                stage=ScanStage.DISCOVER.value,
            )
        if callable(append_scan_event):
            append_scan_event(
                scan_job_id,
                "stage_changed",
                {"stage": ScanStage.DISCOVER.value},
            )
        stage_elapsed_ms[ScanStage.DISCOVER.value] = round(
            _monotonic_ms() - scan_started_ms,
            3,
        )
        stat_cache_started_ms = _monotonic_ms()
        existing_index = load_incremental_index_cache(
            scan_root,
            library_root=self.library_root,
            repository=repository,
            root_only=root_only,
        )
        stage_elapsed_ms[ScanStage.STAT_CACHE.value] = round(
            _monotonic_ms() - stat_cache_started_ms,
            3,
        )
        if callable(update_scan_job_stage):
            update_scan_job_stage(scan_job_id, stage=ScanStage.STAT_CACHE.value)
        if callable(append_scan_event):
            append_scan_event(
                scan_job_id,
                "stage_changed",
                {
                    "stage": ScanStage.STAT_CACHE.value,
                    "elapsed_ms": stage_elapsed_ms[ScanStage.STAT_CACHE.value],
                },
            )

        use_case = ScanLibraryUseCase(
            scanner=self._scanner,
            asset_repository=repository,
        )
        try:
            if callable(update_scan_job_stage):
                update_scan_job_stage(scan_job_id, stage=ScanStage.METADATA.value)
            if callable(append_scan_event):
                append_scan_event(
                    scan_job_id,
                    "stage_changed",
                    {"stage": ScanStage.METADATA.value},
                )
            metadata_started_ms = _monotonic_ms()
            result = use_case.execute(
                ScanLibraryRequest(
                    root=scan_root,
                    include=resolved_include,
                    exclude=resolved_exclude,
                    existing_index=existing_index,
                    progress_callback=progress_callback,
                    is_cancelled=is_cancelled,
                    row_transform=self._library_relative_transform(scan_root),
                    scan_batch_callback=scan_batch_callback,
                    batch_failed_callback=batch_failed_callback,
                    chunk_size=chunk_size,
                    visible_publish_size=visible_publish_size,
                    max_chunk_interval_ms=max_chunk_interval_ms,
                    persist_chunks=persist_chunks,
                    scan_job_id=scan_job_id,
                    scan_started_at_ms=scan_started_at_ms,
                    scan_stage_elapsed_ms=stage_elapsed_ms,
                    recursive=not root_only,
                )
            )
            stage_elapsed_ms[ScanStage.METADATA.value] = round(
                _monotonic_ms() - metadata_started_ms,
                3,
            )
        except Exception:
            if callable(update_scan_job_stage):
                update_scan_job_stage(
                    scan_job_id,
                    status="failed",
                    finished=True,
                )
            raise

        visible_publish_started_ms = _monotonic_ms()
        if callable(update_scan_job_stage):
            update_scan_job_stage(
                scan_job_id,
                stage=ScanStage.VISIBLE_PUBLISH.value,
                status=(
                    "cancelled"
                    if is_cancelled is not None and is_cancelled()
                    else "scanned"
                ),
                processed_count=len(result.rows),
                visible_count=sum(1 for row in result.rows if _row_is_ready_visible(row)),
                failed_count=result.failed_count,
                finished=True,
            )
        stage_elapsed_ms[ScanStage.VISIBLE_PUBLISH.value] = round(
            _monotonic_ms() - visible_publish_started_ms,
            3,
        )
        if callable(append_scan_event):
            append_scan_event(
                scan_job_id,
                "stage_changed",
                {
                    "stage": ScanStage.VISIBLE_PUBLISH.value,
                    "elapsed_ms": stage_elapsed_ms[ScanStage.VISIBLE_PUBLISH.value],
                    "stage_elapsed_ms": stage_elapsed_ms,
                },
            )
        return result

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

        resolved_include, resolved_exclude = self.scan_filters(
            Path(root),
            include=include,
            exclude=exclude,
        )
        result = self.scan_album(
            root,
            include=resolved_include,
            exclude=resolved_exclude,
            progress_callback=progress_callback,
            is_cancelled=is_cancelled,
            persist_chunks=False,
        )
        rows = self.finalize_scan_result(
            root,
            result.rows,
            pair_live=pair_live,
            exclude=resolved_exclude,
            preserve_modified_after_ms=result.scan_started_at_ms,
            current_scan_job_id=result.scan_job_id,
        )
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
        exclude: Iterable[str] | None = None,
        preserve_modified_after_ms: int | None = None,
        current_scan_job_id: str | None = None,
        root_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Persist scan completion side effects for one scope.

        This is the higher-level runtime hook used by GUI adapters after a scan
        completes. It preserves Recently Deleted restore metadata, persists the
        snapshot and derived links, prunes stale rows, and optionally refreshes
        Live Photo pairing state.
        """

        from .library_asset_lifecycle_service import LibraryAssetLifecycleService

        scan_root = Path(root)
        scan_scope = self._scan_scope(scan_root, root_only=root_only)
        resolved_exclude = tuple(
            exclude if exclude is not None else self.scan_filters(scan_root)[1]
        )
        lifecycle_service = LibraryAssetLifecycleService(
            self.library_root,
            scan_service=self,
        )
        materialized_rows = self._existing_scan_rows(scan_root, rows)

        if scan_root.name == RECENTLY_DELETED_DIR_NAME:
            materialized_rows = lifecycle_service.preserve_trash_metadata(
                scan_root,
                materialized_rows,
            )

        try:
            if root_only:
                # A partial snapshot cannot replace the library index or links.
                self._repository().merge_scan_rows(materialized_rows)
            else:
                self.finalize_scan(scan_root, materialized_rows)
            lifecycle_service.reconcile_missing_scan_rows(
                scan_root,
                materialized_rows,
                exclude_globs=resolved_exclude,
                preserve_modified_after_ms=preserve_modified_after_ms,
                current_scan_job_id=current_scan_job_id,
                root_only=root_only,
            )
            if pair_live:
                if root_only:
                    self.pair_album(scan_root, root_only=True)
                else:
                    self.pair_album(scan_root)
        except Exception:
            self._mark_scan_job_failed(current_scan_job_id)
            raise

        self._mark_scan_job_finalized(scan_root, current_scan_job_id, scope=scan_scope)
        return materialized_rows

    def reconcile_missing_album_scope(
        self,
        root: Path,
        *,
        is_cancelled: Callable[[], bool] | None = None,
        relocation_roots: Iterable[Path] = (),
    ) -> bool:
        """Reconcile a missing scope, migrating verified renames before pruning."""

        scan_root = Path(root)
        album_path = self._album_path(scan_root)
        if not album_path or RECENTLY_DELETED_DIR_NAME in Path(album_path).parts:
            return False

        def scope_is_missing() -> bool:
            try:
                info = scan_root.stat()
            except (FileNotFoundError, NotADirectoryError):
                return True
            return not stat.S_ISDIR(info.st_mode)

        if not scope_is_missing():
            return False
        # A disconnected library is not evidence that its albums were deleted.
        if not self.library_root.is_dir():
            raise AlbumNotFoundError(f"Library directory is unavailable: {self.library_root}")
        if is_cancelled is not None and is_cancelled():
            return True
        repository = self._repository()
        rows = [
            {"rel": str(row["rel"]), "id": row.get("id")}
            for row in repository.read_album_assets(
                album_path,
                include_subalbums=True,
                sort_by_date=False,
                filter_hidden=False,
            )
            if row.get("rel")
        ]
        # Newly discovered scopes are only hints: filenames alone cannot prove
        # a rename. Verify content identity before moving any persisted state.
        candidates = [
            Path(candidate)
            for candidate in relocation_roots
            if (relative := self._album_path(Path(candidate)))
            and RECENTLY_DELETED_DIR_NAME not in Path(relative).parts
        ]
        verified_by_candidate: dict[Path, dict[str, str]] = {
            candidate: {} for candidate in candidates
        }
        for row in rows:
            if is_cancelled is not None and is_cancelled():
                return True
            source = str(row["rel"])
            asset_id = str(row.get("id") or "")
            if not asset_id:
                continue
            subpath = Path(source).relative_to(album_path)
            for candidate in candidates:
                target = candidate / subpath
                try:
                    if target.is_file() and f"as_{compute_file_id(target)}" == asset_id:
                        verified_by_candidate[candidate][source] = target.relative_to(
                            self.library_root
                        ).as_posix()
                except (FileNotFoundError, NotADirectoryError):
                    continue
        matches_by_source: dict[str, set[str]] = {}
        for candidate, verified in verified_by_candidate.items():
            # A renamed subtree also publishes its child albums as hints.
            # Ignore a child mapping only when an ancestor explains strictly
            # more of the same source rows. Equal or unrelated matches remain
            # ambiguous rather than transferring choices to a possible copy.
            if any(
                verified.keys() < verified_by_candidate[parent].keys()
                for parent in candidate.parents
                if parent in verified_by_candidate
            ):
                continue
            for source, destination in verified.items():
                matches_by_source.setdefault(source, set()).add(destination)
        destinations: dict[str, str] = {}
        expected_ids: dict[str, str] = {}
        for row in rows:
            source = str(row["rel"])
            matches = matches_by_source.get(source, set())
            if len(matches) > 1:
                raise ValueError(f"Ambiguous relocated asset; retaining source state: {source}")
            if matches:
                destinations[source] = matches.pop()
                expected_ids[source] = str(row["id"])
        # Recheck after the read: a queued notification may outlive a rename or
        # temporary disappearance. A recreated scope must follow a normal scan.
        if not scope_is_missing():
            return False
        if not self.library_root.is_dir():
            raise AlbumNotFoundError(f"Library directory is unavailable: {self.library_root}")
        if is_cancelled is not None and is_cancelled():
            return True
        if destinations:
            repository.relocate_rows(destinations, expected_asset_ids=expected_ids)
        remaining = [str(row["rel"]) for row in rows if row["rel"] not in destinations]
        if remaining:
            repository.remove_rows(remaining, clear_live_partners=True)
        return True

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
        *,
        exclude_globs: Iterable[str] | None = None,
        preserve_modified_after_ms: int | None = None,
        current_scan_job_id: str | None = None,
    ) -> int:
        """Prune stale rows for a completed full scan scope."""

        scan_root = Path(root)
        materialized_rows = [dict(row) for row in rows]
        repository = self._repository()
        resolved_exclude = tuple(
            exclude_globs if exclude_globs is not None else self.scan_filters(scan_root)[1]
        )
        return prune_index_scope(
            scan_root,
            materialized_rows,
            library_root=self.library_root,
            repository=repository,
            exclude_globs=resolved_exclude,
            preserve_modified_after_ms=preserve_modified_after_ms,
            current_scan_job_id=current_scan_job_id,
        )

    def is_scan_scope_complete(self, root: Path) -> bool:
        """Return whether *root* is covered by a completed scan job."""

        scan_root = Path(root)
        repository = self._repository()
        latest_scan_job = getattr(repository, "latest_scan_job", None)
        if not callable(latest_scan_job):
            return False

        exact_scope = "album" if self._album_path(scan_root) else "library"
        exact = latest_scan_job(root=scan_root.as_posix(), scope=exact_scope)
        covering = None
        if not self._paths_equal(scan_root, self.library_root):
            covering = latest_scan_job(
                root=self.library_root.as_posix(),
                scope="library",
            )

        latest = self._newer_scan_job(exact, covering)
        return self._scan_job_completed(latest)

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
            for row in _process_media_paths_with_thumbnail_cache(
                scan_root,
                image_paths,
                video_paths,
                thumbnail_cache_dir=self._thumbnail_cache_dir(),
            )
        ]
        transform = self._library_relative_transform(scan_root)
        transformed = [transform(row) for row in rows]
        if transformed:
            self._repository().merge_scan_rows(transformed)
        return transformed

    def pair_album(self, root: Path, *, root_only: bool = False) -> list[LiveGroup]:
        """Rebuild Live Photo roles and derived links for *root*."""

        scan_root = Path(root)
        album_path = self._album_path(scan_root)
        repository = self._repository()
        if root_only:
            self._scan_scope(scan_root, root_only=True)
            rows = list(
                repository.read_album_assets("", include_subalbums=False, filter_hidden=False)
            )
        elif album_path:
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
            root_only=root_only,
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

    def _thumbnail_cache_dir(self) -> Path:
        return ensure_work_dir(self.library_root) / "cache" / "thumbs"

    def _load_manifest(self, root: Path) -> dict[str, Any]:
        if not root.exists():
            raise AlbumNotFoundError(f"Album directory does not exist: {root}")

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

    def _existing_scan_rows(
        self,
        scan_root: Path,
        rows: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Drop rows whose source file disappeared before scan finalization."""

        materialized: list[dict[str, Any]] = []
        for row in rows:
            row_data = dict(row)
            rel_value = row_data.get("rel")
            if not isinstance(rel_value, str) or not rel_value:
                continue
            absolute = self._absolute_path_for_scan_row(scan_root, rel_value)
            try:
                if not absolute.exists():
                    continue
            except OSError:
                continue
            materialized.append(row_data)
        return materialized

    def _scan_scope(self, root: Path, *, root_only: bool = False) -> str:
        if root_only:
            if not self._paths_equal(root, self.library_root):
                raise ValueError("A root media scan must use the library root")
            return "root_media"
        return "album" if self._album_path(root) else "library"

    def _mark_scan_job_finalized(
        self, scan_root: Path, scan_job_id: str | None, *, scope: str
    ) -> None:
        if not scan_job_id:
            return
        repository = self._repository()
        latest_scan_job = getattr(repository, "latest_scan_job", None)
        if callable(latest_scan_job):
            job = latest_scan_job(root=scan_root.as_posix(), scope=scope)
            if job is not None and job.get("job_id") == scan_job_id:
                status = str(job.get("status") or "")
                if status == "cancelled":
                    return
        update_scan_job_stage = getattr(repository, "update_scan_job_stage", None)
        if callable(update_scan_job_stage):
            update_scan_job_stage(
                scan_job_id,
                stage=ScanStage.DB_COMMIT.value,
                status="completed",
                finished=True,
            )

    def _mark_scan_job_failed(self, scan_job_id: str | None) -> None:
        if not scan_job_id:
            return
        update_scan_job_stage = getattr(self._repository(), "update_scan_job_stage", None)
        if callable(update_scan_job_stage):
            update_scan_job_stage(scan_job_id, status="failed", finished=True)

    def _absolute_path_for_scan_row(self, scan_root: Path, rel: str) -> Path:
        if self._album_path(scan_root):
            return self.library_root / rel
        return scan_root / rel

    @staticmethod
    def _scan_job_completed(job: dict[str, Any] | None) -> bool:
        if not job:
            return False
        return str(job.get("status") or "") == "completed" and job.get("finished_at") is not None

    @staticmethod
    def _newer_scan_job(
        first: dict[str, Any] | None,
        second: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if first is None:
            return second
        if second is None:
            return first
        return first if _scan_job_sort_key(first) >= _scan_job_sort_key(second) else second

    @staticmethod
    def _paths_equal(left: Path, right: Path) -> bool:
        try:
            return Path(left).resolve() == Path(right).resolve()
        except OSError:
            return Path(left) == Path(right)

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


def _row_is_ready_visible(row: dict[str, Any]) -> bool:
    return row.get("thumbnail_state") == "ready" and bool(
        str(row.get("thumb_cache_key") or "").strip()
    )


def _monotonic_ms() -> float:
    return time.perf_counter() * 1000


def _utc_ms() -> int:
    return int(time.time() * 1000)


def _scan_job_sort_key(job: dict[str, Any]) -> tuple[int, int]:
    def _coerce(value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    return (
        max(_coerce(job.get("updated_at")), _coerce(job.get("started_at"))),
        _coerce(job.get("finished_at")),
    )


def _process_media_paths_with_thumbnail_cache(
    root: Path,
    image_paths: list[Path],
    video_paths: list[Path],
    *,
    thumbnail_cache_dir: Path,
) -> Iterable[dict[str, Any]]:
    try:
        signature = inspect.signature(process_media_paths)
    except (TypeError, ValueError):
        signature = None
    if signature is not None and "thumbnail_cache_dir" in signature.parameters:
        return process_media_paths(
            root,
            image_paths,
            video_paths,
            thumbnail_cache_dir=thumbnail_cache_dir,
        )
    return process_media_paths(root, image_paths, video_paths)


__all__ = [
    "AlbumOpenPreparation",
    "AlbumReport",
    "LibraryScanService",
]
