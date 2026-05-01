"""Qt task transport for library update scans and restore refreshes."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from ...bootstrap.library_scan_service import LibraryScanService
from ..background_task_manager import BackgroundTaskManager
from ...library.workers.rescan_worker import RescanSignals, RescanWorker
from ...library.workers.scanner_worker import ScannerSignals, ScannerWorker


@dataclass(frozen=True)
class ScanTaskCompletion:
    """Normalized scan completion payload for GUI presentation adapters."""

    root: Path
    rows: list[dict]
    scan_service: LibraryScanService
    library_root: Path | None
    restart_requested: bool = False


class LibraryUpdateTaskRunner:
    """Own worker lifecycle and task-manager wiring for library updates."""

    def __init__(self, *, task_manager: BackgroundTaskManager) -> None:
        self._task_manager = task_manager
        self._scanner_worker: ScannerWorker | None = None
        self._scan_pending = False

    def start_scan(
        self,
        *,
        root: Path,
        include: Iterable[str],
        exclude: Iterable[str],
        library_root: Path | None,
        scan_service: LibraryScanService | None,
        on_progress: Callable[[Path, int, int], None],
        on_chunk: Callable[[Path, list[dict]], None],
        on_cancelled: Callable[[Path, bool], None],
        on_completed: Callable[[ScanTaskCompletion], None],
        on_error: Callable[[Path, str, bool], None],
    ) -> None:
        """Submit an asynchronous scan task or request a restart."""

        if self._scanner_worker is not None:
            self._scanner_worker.cancel()
            self._scan_pending = True
            return

        signals = ScannerSignals()
        signals.progressUpdated.connect(on_progress)
        signals.chunkReady.connect(on_chunk)

        worker = ScannerWorker(
            root,
            include,
            exclude,
            signals,
            library_root=library_root,
            scan_service=scan_service,
        )
        self._scanner_worker = worker
        self._scan_pending = False

        self._task_manager.submit_task(
            task_id=f"scan:{root}",
            worker=worker,
            progress=signals.progressUpdated,
            finished=signals.finished,
            error=signals.error,
            pause_watcher=False,
            on_finished=lambda emitted_root, rows, captured_library_root=library_root: self._handle_scan_finished(
                worker,
                emitted_root,
                rows,
                library_root=captured_library_root,
                on_cancelled=on_cancelled,
                on_completed=on_completed,
            ),
            on_error=lambda emitted_root, message: self._handle_scan_error(
                worker,
                emitted_root,
                message,
                on_error=on_error,
            ),
            result_payload=lambda _root, rows: rows,
        )

    def cancel_active_scan(self) -> None:
        """Cancel the active scan without scheduling a retry."""

        if self._scanner_worker is None:
            return
        self._scanner_worker.cancel()
        self._scan_pending = False

    def start_restore_refresh(
        self,
        *,
        root: Path,
        task_id: str,
        library_root: Path | None,
        scan_service: LibraryScanService | None,
        on_finished: Callable[[Path, bool], None],
        on_error: Callable[[Path, str], None],
    ) -> None:
        """Submit a restored-album refresh task."""

        signals = RescanSignals()
        worker = RescanWorker(
            root,
            signals,
            library_root=library_root,
            scan_service=scan_service,
        )
        self._task_manager.submit_task(
            task_id=task_id,
            worker=worker,
            progress=signals.progressUpdated,
            finished=signals.finished,
            error=signals.error,
            pause_watcher=False,
            on_finished=on_finished,
            on_error=on_error,
            result_payload=lambda path, ok: (path, ok),
        )

    def _handle_scan_finished(
        self,
        worker: ScannerWorker,
        root: Path,
        rows: list[dict],
        *,
        library_root: Path | None,
        on_cancelled: Callable[[Path, bool], None],
        on_completed: Callable[[ScanTaskCompletion], None],
    ) -> None:
        if self._scanner_worker is not worker:
            return

        restart_requested = self._scan_pending
        if worker.cancelled:
            self._cleanup_scan_worker()
            on_cancelled(root, restart_requested)
            return

        if worker.failed:
            self._cleanup_scan_worker()
            return

        completion = ScanTaskCompletion(
            root=Path(root),
            rows=[dict(row) for row in rows],
            scan_service=worker.scan_service,
            library_root=library_root,
            restart_requested=restart_requested,
        )
        self._cleanup_scan_worker()
        on_completed(completion)

    def _handle_scan_error(
        self,
        worker: ScannerWorker,
        root: Path,
        message: str,
        *,
        on_error: Callable[[Path, str, bool], None],
    ) -> None:
        if self._scanner_worker is not worker:
            return

        restart_requested = self._scan_pending
        self._cleanup_scan_worker()
        on_error(root, message, restart_requested)

    def _cleanup_scan_worker(self) -> None:
        self._scanner_worker = None
        self._scan_pending = False

