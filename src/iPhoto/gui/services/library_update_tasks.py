"""Qt task transport for library update scans and restore refreshes."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from ...application.use_cases.scan_models import ScanCompletion, ScanMode, ScanPlan
from ...bootstrap.library_scan_service import LibraryScanService
from ..background_task_manager import BackgroundTaskManager
from ...library.workers.rescan_worker import RescanSignals, RescanWorker
from ...library.workers.scanner_worker import ScannerSignals, ScannerWorker


@dataclass(frozen=True)
class ScanTaskCompletion:
    """Normalized scan completion payload for GUI presentation adapters."""

    completion: ScanCompletion
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
        scan_mode: ScanMode = ScanMode.BACKGROUND,
        on_progress: Callable[[Path, int, int], None],
        on_status: Callable[[object], None],
        on_chunk: Callable[[Path, list[dict]], None],
        on_batch_failed: Callable[[Path, int], None],
        on_cancelled: Callable[[Path, bool], None],
        on_completed: Callable[[ScanTaskCompletion], None],
        on_error: Callable[[Path, str, bool], None],
    ) -> None:
        """Submit an asynchronous scan task or request a restart."""

        if self._scanner_worker is not None:
            if self._paths_equal(self._scanner_worker.root, root):
                return
            self._scanner_worker.cancel()
            self._scan_pending = True
            return

        signals = ScannerSignals()
        signals.progressUpdated.connect(on_progress)
        signals.statusChanged.connect(on_status)
        signals.chunkReady.connect(on_chunk)
        signals.batchFailed.connect(on_batch_failed)
        if scan_service is not None and hasattr(scan_service, "resume_scan"):
            scan_plan = (
                scan_service.resume_scan(root, include=include, exclude=exclude)
                if scan_mode == ScanMode.INITIAL_SAFE
                else scan_service.plan_scan(root, include=include, exclude=exclude, mode=scan_mode)
            )
        else:
            scan_plan = ScanPlan(
                root=Path(root),
                include=tuple(include),
                exclude=tuple(exclude),
                mode=scan_mode,
                scan_id="",
                persist_chunks=True,
                collect_rows=False,
                safe_mode=scan_mode == ScanMode.INITIAL_SAFE,
                generate_micro_thumbnails=True,
                allow_face_scan=scan_mode != ScanMode.INITIAL_SAFE,
                defer_live_pairing=scan_mode == ScanMode.INITIAL_SAFE,
            )

        worker = ScannerWorker(
            root,
            include,
            exclude,
            signals,
            library_root=library_root,
            scan_service=scan_service,
            scan_plan=scan_plan,
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
            on_finished=lambda completion, captured_library_root=library_root: self._handle_scan_finished(
                worker,
                completion,
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
            result_payload=lambda completion: completion,
        )

    def cancel_active_scan(self) -> None:
        """Cancel the active scan without scheduling a retry."""

        if self._scanner_worker is None:
            return
        self._scanner_worker.cancel()
        self._scan_pending = False

    def active_scan_root(self) -> Path | None:
        """Return the currently scanned root when a scan is active."""

        if self._scanner_worker is None:
            return None
        return Path(self._scanner_worker.root)

    def is_scanning_path(self, path: Path) -> bool:
        """Return ``True`` when *path* is covered by the active scan worker."""

        active_root = self.active_scan_root()
        if active_root is None:
            return False

        try:
            target = Path(path).resolve()
            scan_root = active_root.resolve()
            if target == scan_root:
                return True
            return scan_root in target.parents
        except (OSError, ValueError):
            return False

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
        completion: ScanCompletion,
        *,
        library_root: Path | None,
        on_cancelled: Callable[[Path, bool], None],
        on_completed: Callable[[ScanTaskCompletion], None],
    ) -> None:
        if self._scanner_worker is not worker:
            return

        restart_requested = self._scan_pending
        if worker.cancelled or completion.cancelled:
            self._cleanup_scan_worker()
            on_cancelled(completion.root, restart_requested)
            return

        task_completion = ScanTaskCompletion(
            completion=completion,
            scan_service=worker.scan_service,
            library_root=library_root,
            restart_requested=restart_requested,
        )
        self._cleanup_scan_worker()
        on_completed(task_completion)

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

    @staticmethod
    def _paths_equal(left: Path, right: Path) -> bool:
        try:
            return Path(left).resolve() == Path(right).resolve()
        except OSError:
            return Path(left) == Path(right)
