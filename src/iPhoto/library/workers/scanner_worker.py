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
    """Scan album files in a worker thread and emit progress updates.
    
    All scanned assets are written to a single global database at the library root.
    When scanning a subfolder, the assets are stored with their library-relative paths.
    """

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
        library_root: Optional[Path] = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._root = root
        self._include = list(include)
        self._exclude = list(exclude)
        self._signals = signals
        # Use library_root for database if provided, otherwise use root
        self._library_root = library_root if library_root else root
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
    def library_root(self) -> Path:
        """Return the database root used by this worker."""

        return self._library_root

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

    def run(self) -> None:  # pragma: no cover - executed on worker thread
        """Perform the scan and emit progress as files are processed."""

        rows: List[dict] = []
        scanner: Optional[Iterator[dict]] = None
        store: Optional[IndexStore] = None

        try:
            ensure_work_dir(self._root, WORK_DIR_NAME)

            # Emit an initial indeterminate update
            self._signals.progressUpdated.emit(self._root, 0, -1)

            # Initialize IndexStore at library root (single global database)
            try:
                store = IndexStore(self._library_root)
            except Exception as e:
                LOGGER.error(f"Failed to initialize IndexStore for {self._library_root}: {e}")
                raise

            def progress_callback(processed: int, total: int) -> None:
                if not self._is_cancelled:
                    self._signals.progressUpdated.emit(self._root, processed, total)

            chunk: List[dict] = []

            # Load existing index for incremental scanning
            existing_index = load_incremental_index_cache(self._library_root)

            # The new scan_album implementation handles parallel discovery and processing.
            # We initialize the generator but execution (and thread starting) happens on iteration.
            scanner = scan_album(
                self._root,
                self._include,
                self._exclude,
                existing_index=existing_index,
                progress_callback=progress_callback
            )

            # Compute prefix for library-relative paths (once, before the loop)
            scan_prefix = ""
            scan_prefix_with_slash = ""
            try:
                if self._library_root.resolve() != self._root.resolve():
                    scan_prefix = self._root.relative_to(self._library_root).as_posix()
                    scan_prefix_with_slash = scan_prefix + "/"
            except ValueError:
                pass  # root is not under library_root, keep empty prefix

            for row in scanner:
                if self._is_cancelled:
                    break

                # Adjust rel path to be library-relative if scanning a subfolder
                if scan_prefix and "rel" in row:
                    # Use string concatenation for efficiency (avoiding Path object creation per row)
                    row["rel"] = scan_prefix_with_slash + row["rel"]

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
            if scanner is not None:
                scanner.close()

            # Clean up the IndexStore if it has a persistent connection
            if store is not None and store._conn is not None:
                try:
                    store._conn.close()
                    store._conn = None
                except Exception as e:
                    LOGGER.warning(f"Failed to close IndexStore connection: {e}")

            if not self._is_cancelled and not self._had_error:
                # Consumers should use `chunkReady` for progressive UI updates.
                # The `finished` signal provides the complete dataset for
                # authoritative operations (e.g. writing the index file).
                self._signals.finished.emit(self._root, rows)
            else:
                self._signals.finished.emit(self._root, [])

    def _process_chunk(self, store: IndexStore, chunk: List[dict]) -> None:
        """
        Attempt to persist a chunk of items to the store and emit readiness signals.

        This method tries to append the given chunk to the provided IndexStore. If
        persistence fails, it logs the error, increments the failed count, and emits
        the `batchFailed` signal with the number of items in the failed chunk.

        Regardless of whether persistence succeeds or fails, the `chunkReady` signal
        is always emitted with the chunk. This ensures that downstream consumers are
        notified of all processed chunks, even if some were not successfully stored.
        """
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
