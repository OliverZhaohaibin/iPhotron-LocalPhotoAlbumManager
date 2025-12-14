"""Background worker that scans albums while reporting progress."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator, List, Optional

from PySide6.QtCore import QObject, QRunnable, Signal

from ...app import load_incremental_index_cache
from ...cache.index_store import IndexStore
from ...config import WORK_DIR_NAME
from ...io.scanner import scan_album
from ...utils.pathutils import ensure_work_dir
from ...utils.logging import get_logger

LOGGER = get_logger()


class ScannerSignals(QObject):
    """Signals emitted by :class:`ScannerWorker` while scanning."""

    progressUpdated = Signal(Path, int, int)
    chunkReady = Signal(Path, list)
    finished = Signal(Path, list)
    error = Signal(Path, str)
    batchFailed = Signal(Path, int)


class ScannerWorker(QRunnable):
    """Scan album files in a worker thread and emit progress updates."""

    # Number of items to process before emitting a progressive update signal.
    # A smaller chunk size makes the UI feel more responsive during the initial
    # load, while a larger one reduces the overhead of signal emission.
    SCAN_CHUNK_SIZE = 10

    def __init__(
        self,
        root: Path,
        include: Iterable[str],
        exclude: Iterable[str],
        signals: ScannerSignals,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._root = root
        self._include = list(include)
        self._exclude = list(exclude)
        self._signals = signals
        self._is_cancelled = False
        self._had_error = False
        self._failed_count = 0

    @property
    def root(self) -> Path:
        """Album directory being scanned."""

        return self._root

    @property
    def signals(self) -> ScannerSignals:
        """Signal container used by this worker."""

        return self._signals

    @property
    def cancelled(self) -> bool:
        """Return ``True`` if the scan has been cancelled."""

        return self._is_cancelled

    @property
    def failed(self) -> bool:
        """Return ``True`` if the scan terminated due to an error."""

        return self._had_error

    def run(self) -> None:  # pragma: no cover - executed on worker thread
        """Perform the scan and emit progress as files are processed."""

        rows: List[dict] = []
        scanner: Optional[Iterator[dict]] = None
        store: Optional[IndexStore] = None

        try:
            ensure_work_dir(self._root, WORK_DIR_NAME)

            # Emit an initial indeterminate update
            self._signals.progressUpdated.emit(self._root, 0, -1)

            # Initialize IndexStore once
            try:
                store = IndexStore(self._root)
            except Exception as e:
                LOGGER.error(f"Failed to initialize IndexStore for {self._root}: {e}")
                raise

            def progress_callback(processed: int, total: int) -> None:
                if not self._is_cancelled:
                    self._signals.progressUpdated.emit(self._root, processed, total)

            chunk: List[dict] = []

            # Load existing index for incremental scanning
            existing_index = load_incremental_index_cache(self._root)

            # The new scan_album implementation handles parallel discovery and processing.
            # We initialize the generator but execution (and thread starting) happens on iteration.
            scanner = scan_album(
                self._root,
                self._include,
                self._exclude,
                existing_index=existing_index,
                progress_callback=progress_callback
            )

            for row in scanner:
                if self._is_cancelled:
                    break

                rows.append(row)
                chunk.append(row)

                if len(chunk) >= self.SCAN_CHUNK_SIZE:
                    self._process_chunk(store, chunk)
                    chunk = []

            if chunk and not self._is_cancelled:
                self._process_chunk(store, chunk)

        except Exception as exc:  # pragma: no cover - best-effort error propagation
            if not self._is_cancelled:
                self._had_error = True
                self._signals.error.emit(self._root, str(exc))
        finally:
            # Ensure the scanner generator is closed to trigger its cleanup logic (stopping threads)
            if scanner is not None and hasattr(scanner, "close"):
                scanner.close()

            if not self._is_cancelled and not self._had_error:
                # Consumers should use `chunkReady` for progressive UI updates.
                # The `finished` signal provides the complete dataset for
                # authoritative operations (e.g. writing the index file).
                self._signals.finished.emit(self._root, rows)
            else:
                self._signals.finished.emit(self._root, [])

    def _process_chunk(self, store: IndexStore, chunk: List[dict]) -> None:
        """Persist a chunk to the store and emit readiness signal."""
        try:
            store.append_rows(chunk)
        except Exception as e:
            LOGGER.error(f"Failed to persist chunk of {len(chunk)} items: {e}")
            self._failed_count += len(chunk)
            self._signals.batchFailed.emit(self._root, len(chunk))
            # We continue even if DB write fails, though these items won't be persisted

        self._signals.chunkReady.emit(self._root, chunk)

    def cancel(self) -> None:
        """Request cancellation of the in-progress scan."""

        self._is_cancelled = True
