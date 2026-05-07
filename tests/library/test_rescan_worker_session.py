from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for rescan worker tests",
    exc_type=ImportError,
)

from iPhoto.application.use_cases.scan_models import (
    ScanCompletion,
    ScanMode,
    ScanPressureLevel,
    ScanProgressPhase,
)
from iPhoto.library.workers.rescan_worker import RescanSignals, RescanWorker


class FakeScanService:
    def __init__(self, completion: ScanCompletion | None = None) -> None:
        self.refreshed: list[tuple[Path, bool]] = []
        self._completion = completion

    def refresh_restored_album(self, root: Path, *, progress_callback=None, pair_live: bool):
        self.refreshed.append((root, pair_live))
        if progress_callback is not None:
            progress_callback(1, 2)
        if self._completion is not None:
            return self._completion
        return ScanCompletion(
            root=Path(root),
            scan_id="scan-1",
            mode=ScanMode.BACKGROUND,
            processed_count=1,
            pressure_level=ScanPressureLevel.NORMAL,
            failed_count=0,
            success=True,
            cancelled=False,
            phase=ScanProgressPhase.COMPLETED,
        )


def test_rescan_worker_uses_session_scan_service(tmp_path: Path) -> None:
    album_root = tmp_path / "album"
    album_root.mkdir()
    scan_service = FakeScanService()
    signals = RescanSignals()
    progress: list[tuple[Path, int, int]] = []
    finished: list[tuple[Path, bool]] = []
    signals.progressUpdated.connect(
        lambda root, done, total: progress.append((root, done, total))
    )
    signals.finished.connect(lambda root, success: finished.append((root, success)))

    worker = RescanWorker(
        album_root,
        signals,
        scan_service=scan_service,
    )
    worker.run()

    assert scan_service.refreshed == [(album_root, True)]
    assert progress == [(album_root, 1, 2)]
    assert finished == [(album_root, True)]


def test_rescan_worker_marks_failed_completion_unsuccessful(tmp_path: Path) -> None:
    album_root = tmp_path / "album"
    album_root.mkdir()
    completion = ScanCompletion(
        root=album_root,
        scan_id="scan-1",
        mode=ScanMode.BACKGROUND,
        processed_count=1,
        pressure_level=ScanPressureLevel.CONSTRAINED,
        failed_count=1,
        success=False,
        cancelled=False,
        phase=ScanProgressPhase.FAILED,
    )
    scan_service = FakeScanService(completion=completion)
    signals = RescanSignals()
    finished: list[tuple[Path, bool]] = []
    signals.finished.connect(lambda root, success: finished.append((root, success)))

    worker = RescanWorker(
        album_root,
        signals,
        scan_service=scan_service,
    )
    worker.run()

    assert finished == [(album_root, False)]
