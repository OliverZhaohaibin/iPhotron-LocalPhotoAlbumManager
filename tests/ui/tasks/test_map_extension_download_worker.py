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
    MapExtensionDownloadResult,
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


def test_cleanup_failure_does_not_invalidate_verified_install(
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
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    progress_messages: list[str] = []
    worker.signals.progress.connect(lambda _current, _total, message: progress_messages.append(message))

    def _raise_cleanup_error(_path: Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(
        "iPhoto.gui.ui.tasks.map_extension_download_worker.shutil.rmtree",
        _raise_cleanup_error,
    )

    worker._cleanup_temporary_directory(tmp_dir)

    assert progress_messages == ["Finalizing map extension install..."]


def test_download_and_stage_returns_success_when_verified_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    del qapp

    package_root = tmp_path / "maps"
    worker = MapExtensionDownloadWorker(
        MapExtensionDownloadRequest(
            package_root=package_root,
            platform="linux",
        )
    )

    monkeypatch.setattr(
        "iPhoto.gui.ui.tasks.map_extension_download_worker.supports_map_extension_download",
        lambda _platform: True,
    )
    monkeypatch.setattr(
        "iPhoto.gui.ui.tasks.map_extension_download_worker.default_osmand_download_url",
        lambda _platform: "https://example.invalid/extension.tar.xz",
    )
    monkeypatch.setattr(
        worker,
        "_download_archive",
        lambda _url, archive_path: archive_path.write_bytes(b"archive"),
    )

    def _extract_archive(_archive_path: Path, extracted_root: Path) -> None:
        extension_root = extracted_root / "extension"
        (extension_root / "rendering_styles").mkdir(parents=True)
        (extension_root / "rendering_styles" / "snowmobile.render.xml").write_text(
            "<renderingStyle />",
            encoding="utf-8",
        )
        (extension_root / "search").mkdir()
        (extension_root / "search" / "geonames.sqlite3").write_bytes(b"sqlite")
        (extension_root / "bin").mkdir()
        (extension_root / "bin" / "osmand_render_helper").write_bytes(b"helper")
        (extension_root / "World_basemap_2.obf").write_bytes(b"obf")

    monkeypatch.setattr(worker, "_extract_archive", _extract_archive)
    monkeypatch.setattr(
        "iPhoto.gui.ui.tasks.map_extension_download_worker.verify_osmand_extension_install",
        lambda _root, platform=None: True,
    )
    monkeypatch.setattr(
        "iPhoto.gui.ui.tasks.map_extension_download_worker.shutil.rmtree",
        lambda _path: (_ for _ in ()).throw(PermissionError("locked")),
    )

    result = worker._download_and_stage()

    assert isinstance(result, MapExtensionDownloadResult)
    assert result.pending_root.name == "extension.pending"
    assert result.extension_root.name == "extension"
