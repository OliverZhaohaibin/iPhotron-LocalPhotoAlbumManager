"""Scan scheduling, progress tracking, and live scan compatibility reads."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional

from PySide6.QtCore import QMutexLocker, QRunnable

from ..utils.logging import get_logger

if TYPE_CHECKING:
    from ..bootstrap.library_scan_service import LibraryScanService

LOGGER = get_logger()


class _PairingWorker(QRunnable):
    """Run live-photo pairing off the main thread after a scan completes."""

    def __init__(
        self,
        scan_root: Path,
        scan_service: LibraryScanService | None = None,
    ) -> None:
        super().__init__()
        self._scan_root = scan_root
        self._scan_service = scan_service

    def run(self) -> None:
        try:
            scan_service = self._scan_service
            if scan_service is None:
                LOGGER.warning(
                    "Skipping live photo pairing for %s because no bound scan service is available",
                    self._scan_root,
                )
                return
            scan_service.pair_album(self._scan_root)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "Failed to persist live photo pairings after scan of %s "
                "(re-scanning the library will retry pairing): %s",
                self._scan_root,
                exc,
            )


class ScanCoordinatorMixin:
    """Mixin providing scan scheduling and progress for LibraryRuntimeController."""

    def start_session_scan(
        self,
        root: Path,
        *,
        include: Iterable[str],
        exclude: Iterable[str],
    ) -> None:
        """Start a scan through the session-facing runtime controller surface."""

        self.start_scanning(root, include, exclude)

    def start_scanning(self, root: Path, include: Iterable[str], exclude: Iterable[str]) -> None:
        """Start a background scan for the given root directory.
        
        All scanned assets are written to the global database at the library root.
        """
        # Scanner and face-recognition workers bring Pillow/NumPy and optional
        # AI runtimes into the process. Import them only when a scan actually
        # starts, never while constructing the first window frame.
        from .workers.face_scan_worker import FaceScanWorker
        from .workers.scanner_worker import ScannerSignals, ScannerWorker

        signals = ScannerSignals()

        # Check if already scanning the same root (thread-safe)
        locker = QMutexLocker(self._scan_buffer_lock)
        if self._current_scanner_worker is not None:
            if self._live_scan_root and self._paths_equal(self._live_scan_root, root):
                return
            self._queue_deferred_scan(root, include, exclude)
            return
        if self._current_face_scanner is not None:
            is_running = getattr(self._current_face_scanner, "isRunning", None)
            if callable(is_running) and is_running():
                self._queue_deferred_scan(root, include, exclude)
                return
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
        signals.progressUpdated.connect(self.scanProgress)
        signals.batchCommitted.connect(self._on_scan_batch_committed)
        signals.finished.connect(
            lambda emitted_root, rows, scan_worker=worker: self._on_scan_finished(
                scan_worker,
                emitted_root,
                rows,
            )
        )
        signals.error.connect(
            lambda emitted_root, message, scan_worker=worker: self._on_scan_error(
                scan_worker,
                emitted_root,
                message,
            )
        )
        signals.batchFailed.connect(self._on_scan_batch_failed)
        self._face_scan_status_message = None
        self.faceScanStatusChanged.emit("")
        face_library_root = self._root if self._root is not None else root
        face_worker = FaceScanWorker(
            face_library_root,
            self,
            people_service=getattr(self, "_people_service", None),
        )
        face_worker.statusChanged.connect(self._on_face_scan_status_changed)
        face_worker.finished.connect(
            lambda face_worker=face_worker: self._on_face_scan_finished(face_worker)
        )
        self._current_face_scanner = face_worker
        # Release lock before starting the worker
        del locker

        face_worker.start()
        self._scan_thread_pool.start(worker)

    def stop_scanning(self, *, wait: bool = False, timeout_ms: int = 2000) -> None:
        """Cancel the currently running scan, if any."""
        locker = QMutexLocker(self._scan_buffer_lock)
        scanner_worker = self._current_scanner_worker
        face_scanner = self._current_face_scanner
        if self._current_scanner_worker:
            worker = self._current_scanner_worker
            worker.cancel()
            cancelled_workers = getattr(self, "_cancelled_scanner_workers", None)
            if cancelled_workers is not None:
                cancelled_workers.add(id(worker))
            self._current_scanner_worker = None
            # We don't clear the buffer immediately on stop, as the UI might still need it
            # until a new scan starts or the app closes. Setting root to None invalidates it contextually.
            self._live_scan_root = None
        deferred_queue = getattr(self, "_deferred_scan_queue", None)
        if deferred_queue is not None:
            deferred_queue.clear()
        if self._current_face_scanner is not None:
            self._current_face_scanner.cancel()
            self._current_face_scanner = None
        del locker

        if wait:
            self._wait_for_scan_workers(
                scanner_worker=scanner_worker,
                face_scanner=face_scanner,
                timeout_ms=timeout_ms,
            )

    def _wait_for_scan_workers(
        self,
        *,
        scanner_worker: ScannerWorker | None,
        face_scanner: FaceScanWorker | None,
        timeout_ms: int,
    ) -> None:
        """Best-effort bounded wait for cancelled scan workers during teardown."""

        if face_scanner is not None:
            wait = getattr(face_scanner, "wait", None)
            if callable(wait):
                try:
                    wait(timeout_ms)
                except RuntimeError:
                    LOGGER.debug("Face scanner wait failed during teardown", exc_info=True)
            is_running = getattr(face_scanner, "isRunning", None)
            if callable(is_running):
                try:
                    if is_running():
                        LOGGER.warning(
                            "Face scan worker did not exit within %d ms after cancel(); "
                            "detaching without terminate() to avoid DB corruption.",
                            timeout_ms,
                        )
                except RuntimeError:
                    LOGGER.debug(
                        "Face scanner state check failed during teardown",
                        exc_info=True,
                    )

        if scanner_worker is None:
            return
        wait_for_done = getattr(self._scan_thread_pool, "waitForDone", None)
        if not callable(wait_for_done):
            return
        try:
            completed = wait_for_done(timeout_ms)
        except RuntimeError:
            LOGGER.debug("Scanner thread-pool wait failed during teardown", exc_info=True)
            return
        if completed is False:
            LOGGER.warning(
                "Scanner thread pool did not quiesce within %d ms after cancel(); "
                "continuing teardown.",
                timeout_ms,
            )

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

        db_rows = self._read_live_rows_from_query_service(query_root, library_root)
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

    def _read_live_rows_from_query_service(
        self,
        query_root: Path,
        library_root: Path,
    ) -> List[Dict]:
        try:
            query_service = getattr(self, "asset_query_service", None)
            if query_service is None:
                return []
            rows = query_service.read_library_relative_asset_rows(
                query_root,
                sort_by_date=True,
                filter_hidden=True,
            )
            return [dict(row) for row in rows]
        except Exception:
            LOGGER.debug("Failed to read live scan rows from query service", exc_info=True)
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

    def _on_scan_batch_committed(self, batch: object) -> None:
        """Handle explicit ready-only scan batches after persistence."""

        rows = getattr(batch, "rows", None)
        if rows:
            self.invalidate_geotagged_assets_cache()
            if self._current_face_scanner is not None:
                self._current_face_scanner.enqueue_rows(rows)
        self.scanBatchCommitted.emit(batch)

    def _on_scan_chunk(self, _root: Path, rows: List[dict]) -> None:
        """Compatibility hook for callers that still relay raw scan chunks."""

        if not rows:
            return
        self.invalidate_geotagged_assets_cache()
        if self._current_face_scanner is not None:
            self._current_face_scanner.enqueue_rows(rows)

    def _on_scan_finished(
        self,
        worker: ScannerWorker,
        root: Path,
        rows: List[dict],
    ) -> None:
        # Clear worker reference before downstream listeners react so a completed
        # scan does not still appear in-flight while final post-processing runs.
        locker = QMutexLocker(self._scan_buffer_lock)
        if self._current_scanner_worker is not worker:
            cancelled_workers = getattr(self, "_cancelled_scanner_workers", set())
            if getattr(worker, "cancelled", False) and id(worker) in cancelled_workers:
                cancelled_workers.discard(id(worker))
                del locker
                self.invalidate_geotagged_assets_cache()
                self.scanFinished.emit(root, False)
                return
            return
        self._current_scanner_worker = None
        face_scanner = self._current_face_scanner
        self._live_scan_root = None
        del locker
        self.invalidate_geotagged_assets_cache()

        if face_scanner is not None:
            face_scanner.finish_input()

        if worker.cancelled:
            self.scanFinished.emit(root, False)
            self._start_next_deferred_scan()
            return
        if worker.failed:
            self.scanFinished.emit(root, False)
            self._start_next_deferred_scan()
            return

        # Persist Live Photo pairings once a scan completes so the database and
        # links.json reflect the latest scan results.
        scan_service = worker.scan_service
        try:
            scan_service.finalize_scan_result(
                root,
                rows,
                pair_live=False,
                preserve_modified_after_ms=worker.scan_started_at_ms,
                current_scan_job_id=worker.scan_job_id,
            )
        except Exception as exc:
            LOGGER.warning("Failed to persist scan finalization for %s: %s", root, exc)
            self.scanFinished.emit(root, False)
            self._start_next_deferred_scan()
            return

        # Emit immediately so the UI (status bar, map refresh) can react without
        # waiting for the potentially slow live-photo pairing step.
        self.scanFinished.emit(root, True)
        self._start_next_deferred_scan()

        # Persist live-photo pairings in the background to avoid blocking the
        # main thread while downstream listeners start refreshing.
        self._scan_thread_pool.start(_PairingWorker(root, scan_service))

    def _on_scan_error(self, worker: ScannerWorker, root: Path, message: str) -> None:
        locker = QMutexLocker(self._scan_buffer_lock)
        if self._current_scanner_worker is not worker:
            return
        self._current_scanner_worker = None
        face_scanner = self._current_face_scanner
        self._live_scan_root = None
        del locker
        if face_scanner is not None:
            face_scanner.finish_input()
        self.errorRaised.emit(message)
        self.scanFinished.emit(root, False)
        self._start_next_deferred_scan()

    def _on_scan_batch_failed(self, root: Path, count: int) -> None:
        """Propagate partial failure notifications to the UI."""
        self.scanBatchFailed.emit(root, count)

    def _paths_equal(self, p1: Path, p2: Path) -> bool:
        try:
            return p1.resolve() == p2.resolve()
        except OSError:
            return p1 == p2

    def _queue_deferred_scan(
        self,
        root: Path,
        include: Iterable[str],
        exclude: Iterable[str],
    ) -> None:
        queue = getattr(self, "_deferred_scan_queue", None)
        if queue is None:
            queue = []
            setattr(self, "_deferred_scan_queue", queue)
        for queued_root, _queued_include, _queued_exclude in queue:
            if self._paths_equal(queued_root, root):
                return
        queue.append((Path(root), list(include), list(exclude)))

    def _start_next_deferred_scan(self) -> None:
        queue = getattr(self, "_deferred_scan_queue", None)
        if not queue:
            return
        if getattr(self, "_current_scanner_worker", None) is not None:
            return
        face_scanner = getattr(self, "_current_face_scanner", None)
        if face_scanner is not None:
            is_running = getattr(face_scanner, "isRunning", None)
            if not callable(is_running) or is_running():
                return
            self._current_face_scanner = None
        root, include, exclude = queue.pop(0)
        self.start_scanning(root, include, exclude)

    def _on_face_scan_status_changed(self, message: str) -> None:
        self._face_scan_status_message = message or None
        self.faceScanStatusChanged.emit(message)

    def _on_face_scan_finished(self, worker: FaceScanWorker | None = None) -> None:
        if worker is None or self._current_face_scanner is worker:
            self._current_face_scanner = None
            self._start_next_deferred_scan()
