"""Scan scheduling, progress tracking, and live scan buffer management."""

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
    """Mixin providing scan scheduling and progress for LibraryManager."""

    _MAX_LIVE_BUFFER_SIZE = 5000

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

        self._live_scan_root = root
        self._live_scan_buffer.clear()

        # Pass library root to scanner so all assets go to global database
        worker = ScannerWorker(root, include, exclude, signals, library_root=self._root)
        self._current_scanner_worker = worker
        # Release lock before starting the worker
        del locker

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
        """Return a snapshot of valid items currently in the scan buffer.

        Args:
            relative_to: If provided, only returns items that are descendants of this path.
        """
        locker = QMutexLocker(self._scan_buffer_lock)
        if not self._live_scan_buffer:
            return []

        if relative_to is None:
            return list(self._live_scan_buffer)

        # Capture root inside lock to prevent race with stop_scanning
        scan_root = self._live_scan_root
        if not scan_root:
            return []

        # Optimization: Resolve paths once outside the loop to avoid I/O blocking per item.
        try:
            scan_root_res = scan_root.resolve()
            rel_root_res = relative_to.resolve()
        except OSError:
            return []

        # Determine the relationship between scan root and view root.
        # Case A: Same path. No path adjustment needed.
        if scan_root_res == rel_root_res:
            return list(self._live_scan_buffer)

        filtered = []
        # Case B: Scanning a child, viewing a parent (e.g., scan Vacation, view Photos).
        # We need to prepend the relative difference to the item paths.
        if rel_root_res in scan_root_res.parents:
            prefix = scan_root_res.relative_to(rel_root_res).as_posix()
            for item in self._live_scan_buffer:
                item_rel = item.get("rel")
                if not isinstance(item_rel, str) or not item_rel:
                    continue
                new_item = item.copy()
                new_item["rel"] = f"{prefix}/{item_rel}"
                filtered.append(new_item)

        # Case C: Scanning a parent, viewing a child (e.g., scan Photos, view Vacation).
        # We need to filter items that belong to the child and strip the prefix.
        elif scan_root_res in rel_root_res.parents:
            prefix = rel_root_res.relative_to(scan_root_res).as_posix()
            # We add a slash to ensure we match directory boundaries (e.g. "Vacation/" vs "VacationTrip")
            prefix_slash = f"{prefix}/"
            for item in self._live_scan_buffer:
                item_rel = item.get("rel")
                if not isinstance(item_rel, str):
                    continue
                # Check if the item is inside the viewing directory
                if item_rel == prefix or item_rel.startswith(prefix_slash):
                    new_item = item.copy()
                    # Strip the prefix to make it relative to the viewing directory
                    # e.g. "Vacation/img.jpg" -> "img.jpg"
                    new_item["rel"] = item_rel[len(prefix_slash):] if item_rel != prefix else ""
                    if not new_item["rel"]:
                        continue # Should not happen for files, but safeguard
                    filtered.append(new_item)

        # Case D: Disjoint paths (e.g. scan Photos/A, view Photos/B).
        # Return empty list.

        return filtered

    def _on_scan_chunk(self, root: Path, chunk: List[dict]) -> None:
        """Handle incoming scan chunks: update buffer only."""

        if not chunk:
            return

        # 1. Update In-Memory Buffer
        locker = QMutexLocker(self._scan_buffer_lock)
        # Check buffer limit
        if len(self._live_scan_buffer) < self._MAX_LIVE_BUFFER_SIZE:
            self._live_scan_buffer.extend(chunk)
        else:
            # If buffer is full, we rely on disk.
            # We can optionally rotate, but simply stopping accumulation is safer for memory.
            # The consuming models should have already pulled earlier data.
            LOGGER.warning(
                f"Live scan buffer for {root} reached its limit of {self._MAX_LIVE_BUFFER_SIZE} items. "
                f"{len(chunk)} new items were not added to the in-memory buffer; relying on disk persistence."
            )

        # 2. Forward signal
        # The persistence is now handled by the ScannerWorker in the background thread.
        self.scanChunkReady.emit(root, chunk)

    def _on_scan_finished(self, root: Path, rows: List[dict]) -> None:
        # Emit scanFinished for downstream handling (e.g., updating links or finalizing scan).
        self.scanFinished.emit(root, True)
        # Clear worker reference after emitting signal to prevent race conditions
        locker = QMutexLocker(self._scan_buffer_lock)
        self._current_scanner_worker = None

        # Persist Live Photo pairings once a scan completes so the database and
        # links.json reflect the latest scan results.
        try:
            from .. import app as backend
            backend.pair(root, library_root=self._root)
        except Exception as exc:
            LOGGER.warning("Failed to persist live photo pairings after scan: %s", exc)

    def _on_scan_error(self, root: Path, message: str) -> None:
        locker = QMutexLocker(self._scan_buffer_lock)
        self._current_scanner_worker = None
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
