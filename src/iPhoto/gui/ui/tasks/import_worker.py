"""Background worker that copies dropped media into an album asynchronously."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, List
import time

from PySide6.QtCore import QObject, QRunnable, Signal

from .... import app as backend

# Max updates per second for progress signal
MAX_UPDATES_PER_SEC = 10
# Number of files to process before triggering an incremental index update
CHUNK_SIZE = 20


class ImportSignals(QObject):
    """Qt signal container used by :class:`ImportWorker` to report progress."""

    started = Signal(Path)
    progress = Signal(Path, int, int)
    finished = Signal(Path, list, bool)
    error = Signal(str)


class ImportWorker(QRunnable):
    """Copy media files on a worker thread and rebuild the album index."""

    def __init__(
        self,
        sources: Iterable[Path],
        destination: Path,
        copier: Callable[[Path, Path], Path],
        signals: ImportSignals,
        *,
        library_root: Path | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._sources = [Path(path) for path in sources]
        self._destination = Path(destination)
        self._copier = copier
        self._signals = signals
        self._is_cancelled = False
        self._library_root = library_root
        self._had_incremental_error = False

    @property
    def signals(self) -> ImportSignals:
        """Return the signal bundle associated with the worker."""

        return self._signals

    def cancel(self) -> None:
        """Request cancellation of the in-flight import operation."""

        self._is_cancelled = True

    def run(self) -> None:  # pragma: no cover - executed on a worker thread
        """Copy files and rebuild the index while emitting progress updates."""

        total = len(self._sources)
        self._signals.started.emit(self._destination)
        if total == 0:
            self._signals.finished.emit(self._destination, [], False)
            return

        imported: List[Path] = []
        pending_batch: List[Path] = []
        last_emit_time = 0.0
        min_interval = 1.0 / MAX_UPDATES_PER_SEC

        for index, source in enumerate(self._sources, start=1):
            if self._is_cancelled:
                break
            try:
                copied = self._copier(source, self._destination)
            except OSError as exc:
                # Propagate filesystem issues (permissions, disk space, …) to the UI.
                self._signals.error.emit(f"Could not import '{source}': {exc}")
            except Exception as exc:  # pragma: no cover - defensive fallback
                self._signals.error.emit(str(exc))
            else:
                imported.append(copied)
                pending_batch.append(copied)
            finally:
                # Throttled progress emission
                now = time.monotonic()
                if now - last_emit_time >= min_interval or index == total:
                    self._signals.progress.emit(self._destination, index, total)
                    last_emit_time = now

            # Process chunk if ready
            if len(pending_batch) >= CHUNK_SIZE:
                self._process_chunk(pending_batch)
                pending_batch.clear()

        # Process any remaining files in the final chunk
        if pending_batch:
            self._process_chunk(pending_batch)
            pending_batch.clear()

        # Final full rescan/link check if not cancelled
        rescan_success = False

        if imported and not self._is_cancelled:
            try:
                if self._had_incremental_error:
                    backend.rescan(self._destination, library_root=self._library_root)
                else:
                    backend.pair(self._destination, library_root=self._library_root)
            except Exception as exc:  # pragma: no cover - defensive fallback
                self._signals.error.emit(str(exc))
                try:
                    backend.rescan(self._destination, library_root=self._library_root)
                except Exception as fallback_exc:  # pragma: no cover - defensive fallback
                    self._signals.error.emit(str(fallback_exc))
                else:
                    rescan_success = True
            else:
                rescan_success = True

        self._signals.finished.emit(self._destination, imported, rescan_success)

    def _process_chunk(self, batch: List[Path]) -> None:
        """Update the index for a batch of imported files."""
        if not batch or self._is_cancelled:
            return
        try:
            backend.scan_specific_files(
                self._destination, batch, library_root=self._library_root
            )
        except Exception as exc:
            # Log error but don't fail the whole import; final rescan might fix it
            self._had_incremental_error = True
            self._signals.error.emit(f"Incremental scan failed: {exc}")
