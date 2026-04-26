"""Tests for :mod:`iPhoto.gui.ui.tasks.map_extension_download_worker`."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for worker tests",
    exc_type=ImportError,
)
pytest.importorskip(
    "PySide6.QtWidgets",
    reason="Qt widgets are required for worker tests",
    exc_type=ImportError,
)

from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.tasks.map_extension_download_worker import (
    MapExtensionDownloadRequest,
    MapExtensionDownloadWorker,
    _DOWNLOAD_TIMEOUT_SECONDS,
)


@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_download_archive_uses_timeout_and_reports_stall(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    del qapp

    worker = MapExtensionDownloadWorker(
        MapExtensionDownloadRequest(
            package_root=tmp_path / "maps",
            platform="linux",
        )
    )
    archive_path = tmp_path / "extension.tar.xz"
    captured: dict[str, object] = {}

    class _TimedOutResponse:
        headers = {"Content-Length": "10"}

        def __enter__(self) -> _TimedOutResponse:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self, _size: int) -> bytes:
            raise socket.timeout("stalled")

    def _fake_urlopen(url: str, *, timeout: int) -> _TimedOutResponse:
        captured["url"] = url
        captured["timeout"] = timeout
        return _TimedOutResponse()

    monkeypatch.setattr(
        "iPhoto.gui.ui.tasks.map_extension_download_worker.request.urlopen",
        _fake_urlopen,
    )

    with pytest.raises(RuntimeError, match="timed out"):
        worker._download_archive("https://example.invalid/extension.tar.xz", archive_path)

    assert captured == {
        "url": "https://example.invalid/extension.tar.xz",
        "timeout": _DOWNLOAD_TIMEOUT_SECONDS,
    }


def test_install_and_verify_pending_root_raises_when_install_not_verified(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    del qapp

    worker = MapExtensionDownloadWorker(
        MapExtensionDownloadRequest(
            package_root=tmp_path / "maps",
            platform="linux",
        )
    )

    monkeypatch.setattr(
        "iPhoto.gui.ui.tasks.map_extension_download_worker.apply_pending_osmand_extension_install",
        lambda _root: True,
    )
    monkeypatch.setattr(
        "iPhoto.gui.ui.tasks.map_extension_download_worker.verify_osmand_extension_install",
        lambda _root, platform=None: False,
    )

    with pytest.raises(RuntimeError, match="stuck as '.pending'"):
        worker._install_and_verify_pending_root()
