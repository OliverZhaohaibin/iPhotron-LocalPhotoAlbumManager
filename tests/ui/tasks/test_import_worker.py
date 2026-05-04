"""Tests for :mod:`iPhoto.gui.ui.tasks.import_worker`."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for import worker tests",
    exc_type=ImportError,
)
pytest.importorskip(
    "PySide6.QtWidgets",
    reason="Qt widgets are required for import worker tests",
    exc_type=ImportError,
)

from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.tasks.import_worker import ImportWorker, ImportSignals


class FakeScanService:
    def __init__(self, *, fail_incremental: bool = False, fail_pair: bool = False) -> None:
        self.fail_incremental = fail_incremental
        self.fail_pair = fail_pair
        self.specific: list[tuple[Path, list[Path]]] = []
        self.paired: list[Path] = []
        self.scanned: list[tuple[Path, bool]] = []
        self.finalized: list[tuple[Path, list[dict]]] = []

    def scan_specific_files(self, root: Path, files: list[Path]) -> None:
        self.specific.append((root, list(files)))
        if self.fail_incremental:
            raise RuntimeError("chunk failed")

    def pair_album(self, root: Path) -> None:
        self.paired.append(root)
        if self.fail_pair:
            raise RuntimeError("pair failed")

    def scan_album(self, root: Path, *, persist_chunks: bool):
        self.scanned.append((root, persist_chunks))
        return SimpleNamespace(rows=[{"rel": "photo.jpg"}])

    def finalize_scan(self, root: Path, rows: list[dict]) -> None:
        self.finalized.append((root, rows))


class FakeLifecycleService:
    def __init__(self) -> None:
        self.reconciled: list[tuple[Path, list[dict]]] = []

    def reconcile_missing_scan_rows(self, root: Path, rows: list[dict]) -> int:
        self.reconciled.append((root, rows))
        return 0


@pytest.fixture()
def qapp() -> QApplication:
    """Ensure a QApplication exists for QObject-based signals."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_import_worker_prefers_pair_over_rescan(qapp: QApplication, tmp_path: Path) -> None:
    """Incremental imports should use pairing instead of a full rescan when chunks succeed."""

    destination = tmp_path / "Album"
    destination.mkdir()
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"data")

    signals = ImportSignals()
    finished: list[tuple[Path, list[Path], bool]] = []
    signals.finished.connect(
        lambda root, imported, success: finished.append((root, imported, success))
    )

    scan_service = FakeScanService()
    lifecycle_service = FakeLifecycleService()

    def copier(src: Path, dst: Path) -> Path:
        target = dst / src.name
        target.write_bytes(src.read_bytes())
        return target

    worker = ImportWorker(
        [source],
        destination,
        copier,
        signals,
        scan_service=scan_service,
        asset_lifecycle_service=lifecycle_service,
    )
    worker.run()

    assert scan_service.specific == [(destination, [destination / source.name])]
    assert scan_service.paired == [destination]
    assert scan_service.scanned == []
    assert lifecycle_service.reconciled == []

    assert finished
    root, imported, success = finished[-1]
    assert root == destination
    assert imported == [destination / source.name]
    assert success is True


def test_import_worker_falls_back_to_full_rescan_after_incremental_failure(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "Album"
    destination.mkdir()
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"data")

    signals = ImportSignals()
    errors: list[str] = []
    finished: list[tuple[Path, list[Path], bool]] = []
    signals.error.connect(errors.append)
    signals.finished.connect(
        lambda root, imported, success: finished.append((root, imported, success))
    )

    scan_service = FakeScanService(fail_incremental=True)
    lifecycle_service = FakeLifecycleService()

    def copier(src: Path, dst: Path) -> Path:
        target = dst / src.name
        target.write_bytes(src.read_bytes())
        return target

    worker = ImportWorker(
        [source],
        destination,
        copier,
        signals,
        scan_service=scan_service,
        asset_lifecycle_service=lifecycle_service,
    )
    worker.run()

    assert errors == ["Incremental scan failed: chunk failed"]
    assert scan_service.paired == []
    assert scan_service.scanned == [(destination, False)]
    assert scan_service.finalized == [(destination, [{"rel": "photo.jpg"}])]
    assert lifecycle_service.reconciled == [(destination, [{"rel": "photo.jpg"}])]
    assert finished[-1] == (destination, [destination / source.name], True)
