"""Background worker that scans albums while reporting progress."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, Signal

from ...application.use_cases.scan_models import (
    ScanCompletion,
    ScanMode,
    ScanPlan,
    ScanProgressPhase,
    ScanStatusUpdate,
)
from ...bootstrap.library_scan_service import (
    LibraryScanService,
    merge_scan_chunk_with_repository,
)
from ...infrastructure.services.memory_monitor import MemoryMonitor
from ...utils.pathutils import ensure_work_dir
from ...utils.logging import get_logger

LOGGER = get_logger()

if TYPE_CHECKING:
    from ...cache.index_store.repository import AssetRepository

class ScannerSignals(QObject):
    """Signals emitted by :class:`ScannerWorker` while scanning."""

    progressUpdated = Signal(Path, int, int)
    chunkReady = Signal(Path, list)
    statusChanged = Signal(object)
    finished = Signal(object)
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
        scan_service: Optional[LibraryScanService] = None,
        scan_plan: ScanPlan | None = None,
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
        self._scan_plan = scan_plan
        self._is_cancelled = False
        self._had_error = False
        self._failed_count = 0
        self._micro_thumbnails_enabled = True
        self._memory_paused = False

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

    def run(self) -> None:  # pragma: no cover - executed on worker thread
        """Perform the scan and emit progress as files are processed."""

        completion = ScanCompletion(
            root=self._root,
            scan_id="",
            mode=ScanMode.BACKGROUND,
            processed_count=0,
            failed_count=0,
            success=True,
            cancelled=False,
        )
        try:
            ensure_work_dir(self._root)
            plan = self._scan_plan
            if plan is None:
                if hasattr(self.scan_service, "plan_scan"):
                    plan = self.scan_service.plan_scan(
                        self._root,
                        include=self._include,
                        exclude=self._exclude,
                        mode=ScanMode.BACKGROUND,
                    )
                else:
                    plan = ScanPlan(
                        root=self._root,
                        include=tuple(self._include),
                        exclude=tuple(self._exclude),
                        mode=ScanMode.BACKGROUND,
                        scan_id="",
                        persist_chunks=True,
                        collect_rows=False,
                        safe_mode=False,
                        generate_micro_thumbnails=True,
                        allow_face_scan=True,
                        defer_live_pairing=False,
                    )
            self._scan_plan = plan

            memory_monitor = MemoryMonitor()
            memory_monitor.add_warning_callback(self._on_memory_warning)
            memory_monitor.add_critical_callback(self._on_memory_critical)

            # Emit an initial indeterminate update
            self._signals.progressUpdated.emit(self._root, 0, -1)
            self._emit_status(
                ScanProgressPhase.DISCOVERING,
                message="Discovering files…",
            )

            def progress_callback(processed: int, total: int) -> None:
                if not self._is_cancelled:
                    memory_monitor.check()
                    self._signals.progressUpdated.emit(self._root, processed, total)
                    phase = (
                        ScanProgressPhase.DISCOVERING
                        if total <= 0
                        else ScanProgressPhase.INDEXING
                    )
                    self._emit_status(
                        phase,
                        processed=processed,
                        total=total if total >= 0 else None,
                        message=(
                            "Scanning… (counting files)"
                            if total <= 0
                            else "Indexing discovered media…"
                        ),
                    )

            if hasattr(self.scan_service, "start_scan"):
                result = self.scan_service.start_scan(
                    plan,
                    progress_callback=progress_callback,
                    is_cancelled=lambda: self._is_cancelled,
                    chunk_callback=lambda chunk: self._signals.chunkReady.emit(
                        self._root,
                        chunk,
                    ),
                    batch_failed_callback=lambda count: self._signals.batchFailed.emit(
                        self._root,
                        count,
                    ),
                    chunk_size=self.SCAN_CHUNK_SIZE,
                    status_callback=self._signals.statusChanged.emit,
                    generate_micro_thumbnails=lambda: self._micro_thumbnails_enabled
                    and plan.generate_micro_thumbnails,
                )
            else:
                result = self.scan_service.scan_album(
                    self._root,
                    include=self._include,
                    exclude=self._exclude,
                    progress_callback=progress_callback,
                    is_cancelled=lambda: self._is_cancelled,
                    chunk_callback=lambda chunk: self._signals.chunkReady.emit(
                        self._root,
                        chunk,
                    ),
                    batch_failed_callback=lambda count: self._signals.batchFailed.emit(
                        self._root,
                        count,
                    ),
                    chunk_size=self.SCAN_CHUNK_SIZE,
                    persist_chunks=True,
                )
            self._failed_count += result.failed_count
            scan_succeeded = not self._had_error and result.failed_count == 0
            phase = (
                ScanProgressPhase.PAUSED_FOR_MEMORY
                if self._memory_paused
                else (
                    ScanProgressPhase.COMPLETED
                    if scan_succeeded
                    else ScanProgressPhase.FAILED
                )
            )
            completion = ScanCompletion(
                root=self._root,
                scan_id=plan.scan_id,
                mode=plan.mode,
                processed_count=result.processed_count,
                failed_count=result.failed_count,
                success=scan_succeeded,
                cancelled=bool(result.cancelled or self._is_cancelled),
                safe_mode=plan.safe_mode,
                defer_live_pairing=plan.defer_live_pairing,
                allow_face_scan=plan.allow_face_scan,
                phase=phase,
            )

        except Exception as exc:  # pragma: no cover - best-effort error propagation
            if not self._is_cancelled:
                self._had_error = True
                self._signals.error.emit(self._root, str(exc))
                completion = ScanCompletion(
                    root=self._root,
                    scan_id=self._scan_plan.scan_id if self._scan_plan is not None else "",
                    mode=self._scan_plan.mode if self._scan_plan is not None else ScanMode.BACKGROUND,
                    processed_count=0,
                    failed_count=self._failed_count,
                    success=False,
                    cancelled=False,
                    safe_mode=bool(self._scan_plan and self._scan_plan.safe_mode),
                    defer_live_pairing=bool(self._scan_plan and self._scan_plan.defer_live_pairing),
                    allow_face_scan=bool(self._scan_plan.allow_face_scan) if self._scan_plan is not None else True,
                    phase=ScanProgressPhase.FAILED,
                )
        finally:
            self._signals.finished.emit(completion)

    def _process_chunk(self, store: "AssetRepository", chunk: List[dict]) -> None:
        """Compatibility wrapper for tests and legacy worker internals."""

        self._failed_count += merge_scan_chunk_with_repository(
            store,
            root=self._root,
            include=self._include,
            exclude=self._exclude,
            chunk=chunk,
            chunk_callback=lambda emitted: self._signals.chunkReady.emit(
                self._root,
                emitted,
            ),
            batch_failed_callback=lambda count: self._signals.batchFailed.emit(
                self._root,
                count,
            ),
        )

    def cancel(self) -> None:
        """Request cancellation of the in-progress scan."""

        self._is_cancelled = True

    def _emit_status(
        self,
        phase: ScanProgressPhase,
        *,
        processed: int = 0,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        plan = self._scan_plan
        if plan is None:
            return
        self._signals.statusChanged.emit(
            ScanStatusUpdate(
                root=self._root,
                scan_id=plan.scan_id,
                mode=plan.mode,
                phase=phase,
                processed=processed,
                total=total,
                failed_count=self._failed_count,
                message=message,
            )
        )

    def _on_memory_warning(self, _snapshot) -> None:
        if not self._micro_thumbnails_enabled:
            return
        self._micro_thumbnails_enabled = False
        if self._scan_plan is not None:
            self._scan_plan = ScanPlan(
                **{
                    **self._scan_plan.__dict__,
                    "generate_micro_thumbnails": False,
                }
            )
        self._emit_status(
            ScanProgressPhase.INDEXING,
            message="Memory high: disabling new micro thumbnails.",
        )

    def _on_memory_critical(self, _snapshot) -> None:
        self._memory_paused = True
        self._is_cancelled = True
        self._emit_status(
            ScanProgressPhase.PAUSED_FOR_MEMORY,
            message="Memory critical: pausing scan after current batch.",
        )
