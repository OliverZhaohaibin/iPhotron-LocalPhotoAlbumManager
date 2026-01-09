"""Tests for :mod:`iPhoto.gui.ui.tasks.import_worker`."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

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

from src.iPhoto.gui.ui.tasks.import_worker import ImportWorker, ImportSignals


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

    with patch(
        "src.iPhoto.gui.ui.tasks.import_worker.backend.scan_specific_files"
    ) as scan_specific_files, patch(
        "src.iPhoto.gui.ui.tasks.import_worker.backend.pair"
    ) as pair, patch(
        "src.iPhoto.gui.ui.tasks.import_worker.backend.rescan"
    ) as rescan:
        pair.return_value = None
        rescan.return_value = None

        def copier(src: Path, dst: Path) -> Path:
            target = dst / src.name
            target.write_bytes(src.read_bytes())
            return target

        worker = ImportWorker([source], destination, copier, signals)
        worker.run()

        scan_specific_files.assert_called()
        pair.assert_called_once_with(destination, library_root=None)
        rescan.assert_not_called()

    assert finished
    root, imported, success = finished[-1]
    assert root == destination
    assert imported == [destination / source.name]
    assert success is True
