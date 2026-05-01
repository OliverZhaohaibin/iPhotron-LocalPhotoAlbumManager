"""Unit tests for :mod:`iPhoto.gui.services.album_metadata_service`."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for album metadata service tests",
    exc_type=ImportError,
)
pytest.importorskip(
    "PySide6.QtWidgets",
    reason="Qt widgets are required for album metadata service tests",
    exc_type=ImportError,
)

from PySide6.QtWidgets import QApplication

from iPhoto.errors import IPhotoError
from iPhoto.gui.services.album_metadata_service import AlbumMetadataService


@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class DummyAlbum:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest: dict[str, list[str]] = {"featured": []}
        self.cover: str | None = None

    def set_cover(self, rel: str) -> None:
        self.cover = rel

    def add_featured(self, ref: str) -> None:
        if ref not in self.manifest["featured"]:
            self.manifest["featured"].append(ref)

    def remove_featured(self, ref: str) -> None:
        if ref in self.manifest["featured"]:
            self.manifest["featured"].remove(ref)


def test_set_album_cover_delegates_to_bound_service_and_refreshes_view(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    album = DummyAlbum(tmp_path / "Album")
    session_service = SimpleNamespace(
        set_cover=MagicMock(),
        toggle_featured=MagicMock(),
        ensure_featured_entries=MagicMock(),
    )
    manager = SimpleNamespace(
        album_metadata_service=session_service,
        root=lambda: album.root,
        state_repository=None,
        pause_watcher=MagicMock(),
        resume_watcher=MagicMock(),
    )
    refresh = MagicMock()
    monkeypatch.setattr(
        "iPhoto.gui.services.album_metadata_service.QTimer.singleShot",
        lambda _delay, callback: callback(),
    )

    service = AlbumMetadataService(
        current_album_getter=lambda: album,
        library_manager_getter=lambda: manager,
        refresh_view=refresh,
    )

    assert service.set_album_cover(album, "cover.jpg") is True
    session_service.set_cover.assert_called_once_with(album.root, "cover.jpg")
    manager.pause_watcher.assert_called_once()
    manager.resume_watcher.assert_called_once()
    refresh.assert_called_once_with(album.root)
    assert album.cover == "cover.jpg"


def test_toggle_featured_delegates_to_bound_service_and_updates_album_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    album = DummyAlbum(tmp_path / "Album")
    session_service = SimpleNamespace(
        set_cover=MagicMock(),
        toggle_featured=MagicMock(
            return_value=SimpleNamespace(is_featured=True, errors=[]),
        ),
        ensure_featured_entries=MagicMock(),
    )
    manager = SimpleNamespace(
        album_metadata_service=session_service,
        root=lambda: album.root,
        state_repository=None,
        pause_watcher=MagicMock(),
        resume_watcher=MagicMock(),
    )
    monkeypatch.setattr(
        "iPhoto.gui.services.album_metadata_service.QTimer.singleShot",
        lambda _delay, callback: callback(),
    )

    service = AlbumMetadataService(
        current_album_getter=lambda: album,
        library_manager_getter=lambda: manager,
        refresh_view=MagicMock(),
    )

    assert service.toggle_featured(album, "photo.jpg") is True
    session_service.toggle_featured.assert_called_once_with(album.root, "photo.jpg")
    assert album.manifest["featured"] == ["photo.jpg"]
    manager.pause_watcher.assert_called_once()
    manager.resume_watcher.assert_called_once()


def test_toggle_featured_emits_error_and_preserves_original_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    album = DummyAlbum(tmp_path / "Album")
    album.manifest["featured"] = ["photo.jpg"]
    session_service = SimpleNamespace(
        set_cover=MagicMock(),
        toggle_featured=MagicMock(side_effect=IPhotoError("boom")),
        ensure_featured_entries=MagicMock(),
    )
    manager = SimpleNamespace(
        album_metadata_service=session_service,
        root=lambda: album.root,
        state_repository=None,
        pause_watcher=MagicMock(),
        resume_watcher=MagicMock(),
    )
    errors: list[str] = []
    monkeypatch.setattr(
        "iPhoto.gui.services.album_metadata_service.QTimer.singleShot",
        lambda _delay, callback: callback(),
    )

    service = AlbumMetadataService(
        current_album_getter=lambda: album,
        library_manager_getter=lambda: manager,
        refresh_view=MagicMock(),
    )
    service.errorRaised.connect(errors.append)

    assert service.toggle_featured(album, "photo.jpg") is True
    assert album.manifest["featured"] == ["photo.jpg"]
    assert errors == ["boom"]


def test_ensure_featured_entries_updates_current_album_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    album_root = tmp_path / "Album"
    album = DummyAlbum(album_root)
    imported = [album_root / "a.jpg", album_root / "nested" / "b.jpg"]
    session_service = SimpleNamespace(
        set_cover=MagicMock(),
        toggle_featured=MagicMock(),
        ensure_featured_entries=MagicMock(),
    )
    manager = SimpleNamespace(
        album_metadata_service=session_service,
        root=lambda: album_root,
        state_repository=None,
        pause_watcher=MagicMock(),
        resume_watcher=MagicMock(),
    )
    monkeypatch.setattr(
        "iPhoto.gui.services.album_metadata_service.QTimer.singleShot",
        lambda _delay, callback: callback(),
    )

    service = AlbumMetadataService(
        current_album_getter=lambda: album,
        library_manager_getter=lambda: manager,
        refresh_view=MagicMock(),
    )

    service.ensure_featured_entries(album_root, imported)

    session_service.ensure_featured_entries.assert_called_once_with(album_root, imported)
    assert sorted(album.manifest["featured"]) == ["a.jpg", "nested/b.jpg"]
