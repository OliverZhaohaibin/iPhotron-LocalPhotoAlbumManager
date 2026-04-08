"""Scan scheduling, progress tracking, and live scan compatibility reads."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional

from PySide6.QtCore import QMutexLocker

from ..utils.logging import get_logger
from .workers.scanner_worker import ScannerSignals, ScannerWorker

if TYPE_CHECKING:
    pass

LOGGER = get_logger()


class ScanCoordinatorMixin:
    """Thin Qt mixin that delegates scan logic to ``LibraryScanService``.

    This mixin is intentionally kept minimal: it wires Qt signals, manages the
    ``QThreadPool`` worker lifetime, and forwards all business-level decisions
    to ``self._scan_service`` (a :class:`~iPhoto.application.services.library_scan_service.LibraryScanService`
    instance that must be initialised by :class:`~iPhoto.library.manager.LibraryManager`).
    """

    def start_scanning(self, root: Path, include: Iterable[str], exclude: Iterable[str]) -> None:
        """Start a background scan for the given root directory."""

        # Skip if already scanning the same root (delegate to service).
        if self._scan_service.should_skip_start(root):
            return

        # Prepare signals before acquiring the lock.
        signals = ScannerSignals()
        signals.progressUpdated.connect(self.scanProgress)
        signals.chunkReady.connect(self._on_scan_chunk)
        signals.finished.connect(self._on_scan_finished)
        signals.error.connect(self._on_scan_error)
        signals.batchFailed.connect(self._on_scan_batch_failed)

        locker = QMutexLocker(self._scan_buffer_lock)
        if self._current_scanner_worker is not None:
            self._current_scanner_worker.cancel()
            self._current_scanner_worker = None
            self._live_scan_root = None

        self._live_scan_root = root
        self._live_scan_buffer.clear()

        worker = ScannerWorker(root, include, exclude, signals, library_root=self._root)
        self._current_scanner_worker = worker
        del locker

        # Notify service that a scan has started.
        self._scan_service.mark_started(root)
        self._scan_thread_pool.start(worker)

    def stop_scanning(self) -> None:
        """Cancel the currently running scan, if any."""
        locker = QMutexLocker(self._scan_buffer_lock)
        if self._current_scanner_worker:
            self._current_scanner_worker.cancel()
            self._current_scanner_worker = None
            self._live_scan_root = None
        self._scan_service.mark_stopped()

    def is_scanning_path(self, path: Path) -> bool:
        """Return True if the given path is covered by the active scan."""
        return self._scan_service.is_scanning_path(path)

    def get_live_scan_results(self, relative_to: Optional[Path] = None) -> List[Dict]:
        """Return a best-effort snapshot of live scan results."""
        locker = QMutexLocker(self._scan_buffer_lock)
        scan_root = self._live_scan_root
        buffer_snapshot = list(self._live_scan_buffer)
        library_root = self._root
        del locker

        if scan_root is None:
            return []

        if buffer_snapshot:
            return self._remap_live_rows(buffer_snapshot, scan_root, relative_to)

        if library_root is None:
            return []

        base_root = scan_root if relative_to is None else relative_to
        query_root = self._scan_service.resolve_live_query_root(scan_root, base_root)
        if query_root is None:
            return []

        db_rows = self._scan_service.read_live_rows_from_store(query_root, library_root)
        if not db_rows:
            return []
        return self._rewrite_rows_relative_to(db_rows, query_root, base_root)

    def _on_scan_chunk(self, root: Path, chunk: List[dict]) -> None:
        """Forward scan chunk signal."""
        if not chunk:
            return
        self.scanChunkReady.emit(root, chunk)

    def _on_scan_finished(self, root: Path, rows: List[dict]) -> None:  # noqa: ARG002
        """Handle scan completion: delegate bookkeeping, emit signal."""
        self.scanFinished.emit(root, True)

        locker = QMutexLocker(self._scan_buffer_lock)
        self._current_scanner_worker = None
        del locker

        def _pair(r: Path, lib_root: Optional[Path]) -> None:
            from ..application.use_cases.scan.pair_live_photos_use_case_v2 import (
                PairLivePhotosUseCaseV2,
            )
            PairLivePhotosUseCaseV2(library_root_getter=lambda: lib_root).execute(r)

        self._scan_service.on_scan_finished(root, self._root, pair_callback=_pair)

    def _on_scan_error(self, root: Path, message: str) -> None:
        """Handle scan error: delegate bookkeeping, emit signals."""
        locker = QMutexLocker(self._scan_buffer_lock)
        self._current_scanner_worker = None
        del locker

        self._scan_service.on_scan_error(root)
        self.errorRaised.emit(message)
        self.scanFinished.emit(root, False)

    def _on_scan_batch_failed(self, root: Path, count: int) -> None:
        """Propagate partial failure notifications to the UI."""
        self.scanBatchFailed.emit(root, count)

    # ------------------------------------------------------------------
    # Row remapping helpers (read-only, no business logic)
    # ------------------------------------------------------------------

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
