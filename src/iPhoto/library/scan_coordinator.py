"""Scan scheduling, progress tracking, and live scan compatibility reads."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional

from PySide6.QtCore import QMutexLocker, QRunnable

from ..bootstrap.library_scan_service import LibraryScanService
from ..cache.index_store import get_global_repository
from ..utils.logging import get_logger
from .workers.face_scan_worker import FaceScanWorker
from .workers.scanner_worker import ScannerSignals, ScannerWorker

if TYPE_CHECKING:
    pass

LOGGER = get_logger()


class _PairingWorker(QRunnable):
    """Run live-photo pairing off the main thread after a scan completes."""

    def __init__(
        self,
        scan_root: Path,
        library_root: Optional[Path],
        scan_service: LibraryScanService | None = None,
    ) -> None:
        super().__init__()
        self._scan_root = scan_root
        self._library_root = library_root
        self._scan_service = scan_service

    def run(self) -> None:
        try:
            scan_service = self._scan_service or LibraryScanService(
                self._library_root or self._scan_root
            )
            scan_service.pair_album(self._scan_root)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "Failed to persist live photo pairings after scan of %s "
                "(re-scanning the library will retry pairing): %s",
                self._scan_root,
                exc,
            )


class ScanCoordinatorMixin:
    """Mixin providing scan scheduling and progress for LibraryManager."""

    def start_scanning(self, root: Path, include: Iterable[str], exclude: Iterable[str]) -> None:
        """Start a background scan for the given root directory.
        
        All scanned assets are written to the global database at the library root.
        """
        # Prepare signals outside the lock
        signals = ScannerSignals()
        signals.progressUpdated.connect(self.scanProgress)
        signals.chunkReady.connect(self._on_scan_chunk)
        signals.finished.connect(self._on_scan_finished)
        signals.error.connect(self._on_scan_error)
        signals.batchFailed.connect(self._on_scan_batch_failed)

        # Check if already scanning the same root (thread-safe)
        locker = QMutexLocker(self._scan_buffer_lock)
        if self._current_scanner_worker is not None:
            if self._live_scan_root and self._paths_equal(self._live_scan_root, root):
                return
            # Cancel the old scan before starting new one (inline to avoid deadlock)
            self._current_scanner_worker.cancel()
            self._current_scanner_worker = None
            self._live_scan_root = None
        if self._current_face_scanner is not None:
            self._current_face_scanner.cancel()
            self._current_face_scanner = None

        self._live_scan_root = root
        self._live_scan_buffer.clear()

        # Pass library root to scanner so all assets go to global database
        worker = ScannerWorker(
            root,
            include,
            exclude,
            signals,
            library_root=self._root,
            scan_service=getattr(self, "_scan_service", None),
        )
        self._current_scanner_worker = worker
        self._face_scan_status_message = None
        self.faceScanStatusChanged.emit("")
        face_library_root = self._root if self._root is not None else root
        face_worker = FaceScanWorker(
            face_library_root,
            self,
            people_service=getattr(self, "_people_service", None),
        )
        face_worker.statusChanged.connect(self._on_face_scan_status_changed)
        face_worker.finished.connect(self._on_face_scan_finished)
        self._current_face_scanner = face_worker
        # Release lock before starting the worker
        del locker

        face_worker.start()
        self._scan_thread_pool.start(worker)

    def stop_scanning(self) -> None:
        """Cancel the currently running scan, if any."""
        locker = QMutexLocker(self._scan_buffer_lock)
        if self._current_scanner_worker:
            self._current_scanner_worker.cancel()
            self._current_scanner_worker = None
            # We don't clear the buffer immediately on stop, as the UI might still need it
            # until a new scan starts or the app closes. Setting root to None invalidates it contextually.
            self._live_scan_root = None
        if self._current_face_scanner is not None:
            self._current_face_scanner.cancel()
            self._current_face_scanner = None

    def is_scanning_path(self, path: Path) -> bool:
        """Return True if the given path is covered by the active scan."""
        locker = QMutexLocker(self._scan_buffer_lock)
        if not self._live_scan_root:
            return False

        try:
            target = path.resolve()
            scan_root = self._live_scan_root.resolve()
            if target == scan_root:
                return True
            # Check if target is a subdirectory of scan_root
            return scan_root in target.parents
        except (OSError, ValueError):
            return False

    def get_live_scan_results(self, relative_to: Optional[Path] = None) -> List[Dict]:
        """Return a best-effort snapshot of live scan results.

        Args:
            relative_to: If provided, only returns items that are descendants of
                this path.

        Notes:
            The scan coordinator no longer treats the in-memory list as the
            authoritative browsing cache. When available, results are now read
            back from the on-disk database snapshot. The old in-memory list is
            only used as a compatibility fallback for tests or callers that
            manually inject rows.
        """
        locker = QMutexLocker(self._scan_buffer_lock)
        scan_root = self._live_scan_root
        buffer_snapshot = list(self._live_scan_buffer)
        library_root = self._root
        # Release the lock before performing any potentially slow I/O.
        del locker

        if scan_root is None:
            return []

        if buffer_snapshot:
            return self._remap_live_rows(buffer_snapshot, scan_root, relative_to)

        if library_root is None:
            return []

        base_root = scan_root if relative_to is None else relative_to
        query_root = self._resolve_live_query_root(scan_root, base_root)
        if query_root is None:
            return []

        db_rows = self._read_live_rows_from_store(query_root, library_root)
        if not db_rows:
            return []
        return self._rewrite_rows_relative_to(db_rows, query_root, base_root)

    def _resolve_live_query_root(
        self,
        scan_root: Path,
        relative_to: Optional[Path],
    ) -> Optional[Path]:
        if relative_to is None:
            return scan_root
        try:
            rel_root_res = relative_to.resolve()
            scan_root_res = scan_root.resolve()
        except OSError:
            return None

        if scan_root_res == rel_root_res:
            return scan_root
        if rel_root_res in scan_root_res.parents:
            return scan_root
        if scan_root_res in rel_root_res.parents:
            return relative_to
        return None

    def _read_live_rows_from_store(
        self,
        query_root: Path,
        library_root: Path,
    ) -> List[Dict]:
        try:
            store = get_global_repository(library_root)
            try:
                album_path = query_root.resolve().relative_to(library_root.resolve()).as_posix()
            except (OSError, ValueError):
                try:
                    album_path = query_root.relative_to(library_root).as_posix()
                except ValueError:
                    album_path = None

            if album_path in (None, "", "."):
                rows = store.read_all(sort_by_date=True, filter_hidden=True)
            else:
                rows = store.read_album_assets(
                    album_path,
                    include_subalbums=True,
                    sort_by_date=True,
                    filter_hidden=True,
                )
            return [dict(row) for row in rows]
        except Exception:
            LOGGER.debug("Failed to read live scan rows from store", exc_info=True)
            return []

    def _rewrite_rows_relative_to(
        self,
        rows: List[Dict],
        query_root: Path,
        relative_to: Optional[Path],
    ) -> List[Dict]:
        if self._root is None:
            return []
        target_root = relative_to or query_root
        rewritten: List[Dict] = []
        for row in rows:
            rel_value = row.get("rel")
            if not isinstance(rel_value, str) or not rel_value:
                continue
            try:
                abs_path = (self._root / rel_value).resolve()
                rel_path = abs_path.relative_to(target_root.resolve()).as_posix()
            except (OSError, ValueError):
                continue
            updated = dict(row)
            updated["rel"] = rel_path
            rewritten.append(updated)
        return rewritten

    def _remap_live_rows(
        self,
        rows: List[Dict],
        scan_root: Path,
        relative_to: Optional[Path],
    ) -> List[Dict]:
        if relative_to is None:
            return list(rows)

        try:
            scan_root_res = scan_root.resolve()
            rel_root_res = relative_to.resolve()
        except OSError:
            return []

        if scan_root_res == rel_root_res:
            return list(rows)

        filtered: List[Dict] = []
        if rel_root_res in scan_root_res.parents:
            prefix = scan_root_res.relative_to(rel_root_res).as_posix()
            for item in rows:
                item_rel = item.get("rel")
                if not isinstance(item_rel, str) or not item_rel:
                    continue
                new_item = item.copy()
                new_item["rel"] = f"{prefix}/{item_rel}"
                filtered.append(new_item)
            return filtered

        if scan_root_res in rel_root_res.parents:
            prefix = rel_root_res.relative_to(scan_root_res).as_posix()
            prefix_slash = f"{prefix}/"
            for item in rows:
                item_rel = item.get("rel")
                if not isinstance(item_rel, str):
                    continue
                if item_rel == prefix or item_rel.startswith(prefix_slash):
                    new_item = item.copy()
                    new_item["rel"] = item_rel[len(prefix_slash):] if item_rel != prefix else ""
                    if new_item["rel"]:
                        filtered.append(new_item)
            return filtered

        return []

    def _on_scan_chunk(self, root: Path, chunk: List[dict]) -> None:
        """Handle incoming scan chunks after persistence.

        The scan worker already persists each chunk to disk before this signal is
        observed. The browsing layer now treats the database as the source of
        truth, so we only relay the invalidation event here.
        """

        if not chunk:
            return

        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None
        if self._current_face_scanner is not None:
            self._current_face_scanner.enqueue_rows(chunk)
        self.scanChunkReady.emit(root, chunk)

    def _on_scan_finished(self, root: Path, rows: List[dict]) -> None:
        self._geotagged_assets_cache = None
        self._geotagged_assets_cache_root = None

        # Clear worker reference before downstream listeners react so a completed
        # scan does not still appear in-flight while final post-processing runs.
        locker = QMutexLocker(self._scan_buffer_lock)
        worker = self._current_scanner_worker
        self._current_scanner_worker = None
        face_scanner = self._current_face_scanner
        del locker

        if worker is None:
            if self._live_scan_root is None:
                self.scanFinished.emit(root, True)
            return
        if face_scanner is not None:
            face_scanner.finish_input()

        if worker.cancelled:
            self.scanFinished.emit(root, True)
            return
        if worker.failed:
            self.scanFinished.emit(root, False)
            return

        # Persist Live Photo pairings once a scan completes so the database and
        # links.json reflect the latest scan results.
        scan_service = worker.scan_service
        try:
            scan_service.finalize_scan(root, rows)
        except Exception as exc:
            LOGGER.warning("Failed to persist live photo pairings after scan: %s", exc)

        # Emit immediately so the UI (status bar, map refresh) can react without
        # waiting for the potentially slow live-photo pairing step.
        self.scanFinished.emit(root, True)

        # Persist live-photo pairings in the background to avoid blocking the
        # main thread while downstream listeners start refreshing.
        self._scan_thread_pool.start(_PairingWorker(root, self._root, scan_service))

    def _on_scan_error(self, root: Path, message: str) -> None:
        locker = QMutexLocker(self._scan_buffer_lock)
        self._current_scanner_worker = None
        face_scanner = self._current_face_scanner
        del locker
        if face_scanner is not None:
            face_scanner.finish_input()
        self.errorRaised.emit(message)
        self.scanFinished.emit(root, False)

    def _on_scan_batch_failed(self, root: Path, count: int) -> None:
        """Propagate partial failure notifications to the UI."""
        self.scanBatchFailed.emit(root, count)

    def _paths_equal(self, p1: Path, p2: Path) -> bool:
        try:
            return p1.resolve() == p2.resolve()
        except OSError:
            return p1 == p2

    def _on_face_scan_status_changed(self, message: str) -> None:
        self._face_scan_status_message = message or None
        self.faceScanStatusChanged.emit(message)

    def _on_face_scan_finished(self) -> None:
        self._current_face_scanner = None
