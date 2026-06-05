"""Background worker that scans albums while reporting progress."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional

from PySide6.QtCore import QObject, QRunnable, Signal

from ...bootstrap.library_scan_service import LibraryScanService
from ...utils.pathutils import ensure_work_dir
from ...utils.logging import get_logger

LOGGER = get_logger()


class ScannerSignals(QObject):
    """Signals emitted by :class:`ScannerWorker` while scanning."""

    progressUpdated = Signal(Path, int, int)
    batchCommitted = Signal(object)
    finished = Signal(Path, list)
    error = Signal(Path, str)
    batchFailed = Signal(Path, int)


class ScannerWorker(QRunnable):
    """Scan album files in a worker thread and emit progress updates.
    
    All scanned assets are written to a single global database at the library root.
    When scanning a subfolder, the assets are stored with their library-relative paths.
    """

    # GUI scans trade smaller commits for timely DB-backed gallery refreshes.
    SCAN_CHUNK_SIZE = 100
    SCAN_CHUNK_MAX_INTERVAL_MS = 250

    def __init__(
        self,
        root: Path,
        include: Iterable[str],
        exclude: Iterable[str],
        signals: ScannerSignals,
        library_root: Optional[Path] = None,
        scan_service: Optional[LibraryScanService] = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._root = root
        self._include = list(include)
        self._exclude = list(exclude)
        self._signals = signals
        # Use library_root for database if provided, otherwise use root
        self._library_root = library_root if library_root else root
        self._scan_service = scan_service
        self._is_cancelled = False
        self._had_error = False
        self._failed_count = 0
        self._scan_job_id: str | None = None
        self._scan_started_at_ms: int | None = None

    @property
    def root(self) -> Path:
        """Album directory being scanned."""

        return self._root

    @property
    def signals(self) -> ScannerSignals:
        """Signal container used by this worker."""

        return self._signals

    @property
    def library_root(self) -> Path:
        """Return the database root used by this worker."""

        return self._library_root

    @property
    def scan_service(self) -> LibraryScanService:
        """Return the session scan service used by this worker."""

        if self._scan_service is None:
            self._scan_service = LibraryScanService(self._library_root)
        return self._scan_service

    @property
    def cancelled(self) -> bool:
        """Return ``True`` if the scan has been cancelled."""

        return self._is_cancelled

    @property
    def failed(self) -> bool:
        """Return ``True`` if the scan terminated due to an error."""

        return self._had_error

    @property
    def failed_count(self) -> int:
        """Return the number of items that failed to persist during the scan."""

        return self._failed_count

    @property
    def scan_job_id(self) -> str | None:
        """Return the durable scan job id for the most recent run."""

        return self._scan_job_id

    @property
    def scan_started_at_ms(self) -> int | None:
        """Return the wall-clock start time for the most recent scan."""

        return self._scan_started_at_ms

    def run(self) -> None:  # pragma: no cover - executed on worker thread
        """Perform the scan and emit progress as files are processed."""

        rows: List[dict] = []
        try:
            ensure_work_dir(self._root)

            # Emit an initial indeterminate update
            self._signals.progressUpdated.emit(self._root, 0, -1)

            def progress_callback(processed: int, total: int) -> None:
                if not self._is_cancelled:
                    self._signals.progressUpdated.emit(self._root, processed, total)

            result = self.scan_service.scan_album(
                self._root,
                include=self._include,
                exclude=self._exclude,
                progress_callback=progress_callback,
                is_cancelled=lambda: self._is_cancelled,
                scan_batch_callback=self._emit_batch_if_active,
                batch_failed_callback=lambda count: self._signals.batchFailed.emit(
                    self._root,
                    count,
                ),
                chunk_size=self.SCAN_CHUNK_SIZE,
                max_chunk_interval_ms=self.SCAN_CHUNK_MAX_INTERVAL_MS,
                persist_chunks=True,
            )
            rows = result.rows
            self._failed_count += result.failed_count
            self._scan_job_id = result.scan_job_id
            self._scan_started_at_ms = result.scan_started_at_ms

        except Exception as exc:  # pragma: no cover - best-effort error propagation
            if not self._is_cancelled:
                self._had_error = True
                self._signals.error.emit(self._root, str(exc))
        finally:
            if not self._is_cancelled and not self._had_error:
                # Consumers should use `batchCommitted` for progressive UI updates.
                # The `finished` signal provides the complete dataset for
                # authoritative operations (e.g. writing the index file).
                self._signals.finished.emit(self._root, rows)
            else:
                self._signals.finished.emit(self._root, [])

    def _emit_batch_if_active(self, batch: object) -> None:
        if self._is_cancelled:
            return
        self._signals.batchCommitted.emit(batch)

    def cancel(self) -> None:
        """Request cancellation of the in-progress scan."""

        self._is_cancelled = True
