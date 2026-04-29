from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for rescan worker tests",
    exc_type=ImportError,
)

from iPhoto.library.workers.rescan_worker import RescanSignals, RescanWorker


class FakeScanService:
    def __init__(self) -> None:
        self.scanned: list[tuple[Path, bool]] = []
        self.finalized: list[tuple[Path, list[dict]]] = []

    def scan_album(self, root: Path, *, progress_callback=None, persist_chunks: bool):
        self.scanned.append((root, persist_chunks))
        if progress_callback is not None:
            progress_callback(1, 2)
        return SimpleNamespace(rows=[{"rel": "a.jpg"}])

    def finalize_scan(self, root: Path, rows: list[dict]) -> None:
        self.finalized.append((root, rows))


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

    worker = RescanWorker(album_root, signals, scan_service=scan_service)
    worker.run()

    assert scan_service.scanned == [(album_root, False)]
    assert scan_service.finalized == [(album_root, [{"rel": "a.jpg"}])]
    assert progress == [(album_root, 1, 2)]
    assert finished == [(album_root, True)]
