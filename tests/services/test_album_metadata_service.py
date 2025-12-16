"""Unit tests for :mod:`iPhoto.gui.services.album_metadata_service`."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

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

from src.iPhoto.errors import IPhotoError
from src.iPhoto.gui.services.album_metadata_service import AlbumMetadataService


@pytest.fixture()
def qapp() -> QApplication:
    """Ensure a QApplication instance exists for QObject-based services."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class DummyAlbum:
    """Simple stand-in for :class:`Album` capturing manifest mutations."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest: dict[str, list[str]] = {"featured": []}
        self.cover: str | None = None
        self.saved = 0

    def set_cover(self, rel: str) -> None:
        """Record the last requested cover path."""

        self.cover = rel

    def add_featured(self, ref: str) -> None:
        """Append a featured entry if it is not already present."""

        if ref not in self.manifest["featured"]:
            self.manifest["featured"].append(ref)

    def remove_featured(self, ref: str) -> None:
        """Remove a featured entry when it exists."""

        if ref in self.manifest["featured"]:
            self.manifest["featured"].remove(ref)

    def save(self) -> None:
        """Simulate a successful manifest write."""

        self.saved += 1


def _build_service(
    *,
    asset_model,
    current_album: Callable[[], DummyAlbum | None],
    library_manager_getter: Callable[[], object | None],
    refresh,
) -> AlbumMetadataService:
    """Create a service instance with timer scheduling patched for tests."""

    service = AlbumMetadataService(
        asset_list_model_provider=lambda: asset_model,
        current_album_getter=current_album,
        library_manager_getter=library_manager_getter,
        refresh_view=refresh,
    )
    return service


def test_toggle_featured_updates_current_and_library_album(
    monkeypatch: pytest.MonkeyPatch,
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Toggling an asset should update both the active album and the library root."""

    album_root = tmp_path / "Albums" / "Trip"
    library_root = tmp_path / "Albums"
    album_root.mkdir(parents=True)
    dummy_album = DummyAlbum(album_root)
    root_album = DummyAlbum(library_root)

    # Patch ``Album.open`` to return the dummy root album when invoked.
    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.Album.open",
        lambda path: root_album if Path(path) == library_root else dummy_album,
    )
    # Avoid waiting for Qt timers in tests by executing callbacks immediately.
    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.QTimer.singleShot",
        lambda _delay, callback: callback(),
    )
    
    # Mock IndexStore to avoid DB operations in tests
    index_store_mock = mocker.MagicMock()
    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.IndexStore",
        lambda root: index_store_mock,
    )

    manager = mocker.MagicMock()
    manager.root.return_value = library_root
    asset_model = mocker.MagicMock()
    refresh = mocker.MagicMock()

    service = _build_service(
        asset_model=asset_model,
        current_album=lambda: dummy_album,
        library_manager_getter=lambda: manager,
        refresh=refresh,
    )

    result = service.toggle_featured(dummy_album, "photo.jpg")

    # The current album must mark the asset as featured and persist the change.
    assert result is True
    assert dummy_album.manifest["featured"] == ["photo.jpg"]
    assert dummy_album.saved == 1
    asset_model.update_featured_status.assert_called_once_with("photo.jpg", True)

    # The library root receives the fully qualified path for shared favorites.
    assert root_album.manifest["featured"] == ["Trip/photo.jpg"]
    assert root_album.saved == 1
    refresh.assert_not_called()


def test_toggle_featured_rolls_back_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Errors when saving should keep the manifest and model unchanged."""

    album_root = tmp_path / "Album"
    album_root.mkdir()
    dummy_album = DummyAlbum(album_root)
    dummy_album.manifest["featured"] = ["photo.jpg"]

    def failing_save() -> None:
        raise IPhotoError("boom")

    dummy_album.save = failing_save  # type: ignore[assignment]

    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.QTimer.singleShot",
        lambda _delay, callback: callback(),
    )

    asset_model = mocker.MagicMock()
    refresh = mocker.MagicMock()
    errors: list[str] = []

    service = _build_service(
        asset_model=asset_model,
        current_album=lambda: dummy_album,
        library_manager_getter=lambda: None,
        refresh=refresh,
    )
    service.errorRaised.connect(errors.append)

    result = service.toggle_featured(dummy_album, "photo.jpg")

    # The method should report the original state and avoid touching the model.
    assert result is True
    assert dummy_album.manifest["featured"] == ["photo.jpg"]
    asset_model.update_featured_status.assert_not_called()
    assert errors == ["boom"]
    refresh.assert_not_called()


def test_toggle_featured_from_library_root_updates_sub_album(
    monkeypatch: pytest.MonkeyPatch,
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Toggling from Library Root should propagate to the physical sub-album."""

    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    
    # Create manifest file so the service can identify it as a physical album
    (album_root / ".iphoto.album.json").touch()

    # Create a dummy physical file to make path resolution work
    photo_path = album_root / "photo.jpg"
    photo_path.touch()
    
    root_album = DummyAlbum(library_root)
    sub_album = DummyAlbum(album_root)

    # Patch ``Album.open`` to return the appropriate album based on path
    def album_opener(path):
        path = Path(path)
        if path == library_root:
            return root_album
        elif path == album_root:
            return sub_album
        return None

    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.Album.open",
        album_opener,
    )
    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.QTimer.singleShot",
        lambda _delay, callback: callback(),
    )
    
    # Mock IndexStore to avoid DB operations in tests
    index_store_mock = mocker.MagicMock()
    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.IndexStore",
        lambda root: index_store_mock,
    )

    manager = mocker.MagicMock()
    manager.root.return_value = library_root
    asset_model = mocker.MagicMock()
    refresh = mocker.MagicMock()

    service = _build_service(
        asset_model=asset_model,
        current_album=lambda: root_album,
        library_manager_getter=lambda: manager,
        refresh=refresh,
    )

    # Toggle favorite from the Library Root perspective
    result = service.toggle_featured(root_album, "Trip/photo.jpg")

    # The library root should mark the asset as featured
    assert result is True
    assert root_album.manifest["featured"] == ["Trip/photo.jpg"]
    assert root_album.saved == 1

    # The physical sub-album should also be updated
    assert sub_album.manifest["featured"] == ["photo.jpg"]
    assert sub_album.saved == 1
    
    # IndexStore should be called twice (once for root, once for sub-album)
    assert index_store_mock.set_favorite_status.call_count == 2
    
    asset_model.update_featured_status.assert_called_once_with("Trip/photo.jpg", True)
    refresh.assert_not_called()


def test_toggle_featured_from_library_root_for_root_asset(
    monkeypatch: pytest.MonkeyPatch,
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Toggling an asset directly in Library Root (not in sub-album) should update root only."""

    library_root = tmp_path / "Library"
    library_root.mkdir(parents=True)
    
    # Create manifest file so the service can identify it as a physical album
    (library_root / ".iphoto.album.json").touch()

    # Create a file directly in library root
    photo_path = library_root / "photo.jpg"
    photo_path.touch()
    
    root_album = DummyAlbum(library_root)

    open_count = 0
    def album_opener(path):
        nonlocal open_count
        open_count += 1
        if Path(path) == library_root:
            return root_album
        return None

    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.Album.open",
        album_opener,
    )
    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.QTimer.singleShot",
        lambda _delay, callback: callback(),
    )
    
    # Mock IndexStore to avoid DB operations in tests
    index_store_mock = mocker.MagicMock()
    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.IndexStore",
        lambda root: index_store_mock,
    )

    manager = mocker.MagicMock()
    manager.root.return_value = library_root
    asset_model = mocker.MagicMock()
    refresh = mocker.MagicMock()

    service = _build_service(
        asset_model=asset_model,
        current_album=lambda: root_album,
        library_manager_getter=lambda: manager,
        refresh=refresh,
    )

    # Toggle favorite for an asset directly in the library root
    result = service.toggle_featured(root_album, "photo.jpg")

    # The library root should be updated
    assert result is True
    assert root_album.manifest["featured"] == ["photo.jpg"]
    # Root album should be saved once (as primary album). Redundant update to itself is skipped.
    assert root_album.saved == 1
    
    # Album.open should not be called as we skip reopening the library root
    assert open_count == 0
    
    # IndexStore should be called once (for root as primary)
    assert index_store_mock.set_favorite_status.call_count == 1
    
    asset_model.update_featured_status.assert_called_once_with("photo.jpg", True)
    refresh.assert_not_called()


def test_toggle_featured_uses_abs_path_for_library_view(
    monkeypatch: pytest.MonkeyPatch,
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """
    Verifies that toggling a photo as featured via the library view with an absolute path
    updates both the library root and the physical album manifests and sets the favorite
    status in both IndexStores, even when the rel lacks subfolder context.
    """

    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    (album_root / ".iphoto.album.json").touch()

    photo_path = album_root / "photo.jpg"
    photo_path.touch()

    root_album = DummyAlbum(library_root)
    sub_album = DummyAlbum(album_root)

    def album_opener(path):
        path = Path(path)
        if path == library_root:
            return root_album
        if path == album_root:
            return sub_album
        return None

    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.Album.open",
        album_opener,
    )
    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.QTimer.singleShot",
        lambda _delay, callback: callback(),
    )

    store_map: dict[Path, object] = {}

    def store_factory(root: Path):
        store = mocker.MagicMock()
        store_map[Path(root)] = store
        return store

    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.IndexStore",
        store_factory,
    )

    manager = mocker.MagicMock()
    manager.root.return_value = library_root
    asset_model = mocker.MagicMock()
    refresh = mocker.MagicMock()

    service = _build_service(
        asset_model=asset_model,
        current_album=lambda: root_album,
        library_manager_getter=lambda: manager,
        refresh=refresh,
    )

    result = service.toggle_featured(root_album, "photo.jpg", abs_path=photo_path)

    assert result is True
    assert root_album.manifest["featured"] == ["Trip/photo.jpg"]
    assert sub_album.manifest["featured"] == ["photo.jpg"]
    asset_model.update_featured_status.assert_called_once_with("photo.jpg", True)

    assert album_root in store_map
    assert library_root in store_map
    store_map[album_root].set_favorite_status.assert_called_once_with("photo.jpg", True)
    store_map[library_root].set_favorite_status.assert_called_once_with("Trip/photo.jpg", True)


def test_ensure_featured_entries_updates_album(
    monkeypatch: pytest.MonkeyPatch,
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Recently imported files should be promoted to featured assets when requested."""

    library_root = tmp_path / "Library"
    album_root = library_root / "Hike"
    album_root.mkdir(parents=True)
    imported = [album_root / "a.jpg", album_root / "b.jpg", library_root / "skip.txt"]

    current_album: DummyAlbum | None = None
    dummy_album = DummyAlbum(album_root)

    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.Album.open",
        lambda path: dummy_album if Path(path) == album_root else None,
    )
    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.QTimer.singleShot",
        lambda _delay, callback: callback(),
    )

    asset_model = mocker.MagicMock()
    refresh = mocker.MagicMock()

    service = _build_service(
        asset_model=asset_model,
        current_album=lambda: current_album,
        library_manager_getter=lambda: None,
        refresh=refresh,
    )

    service.ensure_featured_entries(album_root, imported)

    # Only paths located inside the album should be recorded.
    assert sorted(dummy_album.manifest["featured"]) == ["a.jpg", "b.jpg"]
    assert dummy_album.saved == 1
    refresh.assert_not_called()
    asset_model.update_featured_status.assert_not_called()


def test_toggle_featured_identifies_correct_physical_root_nested(
    monkeypatch: pytest.MonkeyPatch,
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """
    Test that toggling a file in a subfolder of a physical album correctly identifies
    the physical album root, instead of treating the subfolder as the album.

    Structure:
    Library/
      album.json (Library Root)
      Events/
        album.json (Physical Album)
        2023/
          photo.jpg (Asset)

    Action: Toggle "featured" on "Events/2023/photo.jpg" from Library Root.

    Expected:
      - Library Root manifest updated with "Events/2023/photo.jpg"
      - Events album manifest updated with "2023/photo.jpg"
      - NO ghost album created in Events/2023/
    """

    # 1. Setup filesystem structure
    library_root = tmp_path / "Library"
    events_root = library_root / "Events"
    sub_dir = events_root / "2023"

    sub_dir.mkdir(parents=True)

    # Create manifest files so `exists()` checks pass
    (library_root / ".iphoto.album.json").touch()
    (events_root / ".iphoto.album.json").touch()

    # Asset file
    photo_path = sub_dir / "photo.jpg"
    photo_path.touch()

    # 2. Setup Mock Albums
    root_album = DummyAlbum(library_root)
    events_album = DummyAlbum(events_root)
    ghost_album = DummyAlbum(sub_dir)  # Should NOT be touched

    # Map paths to albums for Album.open
    albums_by_path = {
        library_root: root_album,
        events_root: events_album,
        sub_dir: ghost_album,
    }

    def album_opener(path):
        return albums_by_path.get(Path(path))

    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.Album.open",
        album_opener,
    )

    # Mock other dependencies
    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.QTimer.singleShot",
        lambda _delay, callback: callback(),
    )

    index_store_mock = mocker.MagicMock()
    monkeypatch.setattr(
        "src.iPhoto.gui.services.album_metadata_service.IndexStore",
        lambda root: index_store_mock,
    )

    manager = mocker.MagicMock()
    manager.root.return_value = library_root
    asset_model = mocker.MagicMock()
    refresh = mocker.MagicMock()

    service = _build_service(
        asset_model=asset_model,
        current_album=lambda: root_album,  # We are in Library Root view
        library_manager_getter=lambda: manager,
        refresh=refresh,
    )

    # 3. Perform Action
    # Ref is relative to library root
    ref = "Events/2023/photo.jpg"

    result = service.toggle_featured(root_album, ref)

    assert result is True

    # 4. Assertions

    # Library root should be updated (unchanged behavior)
    assert root_album.manifest["featured"] == [ref]

    # Events album SHOULD be updated (Desired Behavior)
    # Ref in events album should be relative to events root: "2023/photo.jpg"
    assert events_album.manifest["featured"] == ["2023/photo.jpg"]

    # Ghost album should NOT be updated
    assert ghost_album.manifest["featured"] == []
