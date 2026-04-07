"""Unit tests for the three presentation sub-facades.

These tests verify that AlbumFacade, AssetFacade and LibraryFacade correctly
delegate to their injected collaborators without requiring Qt or PySide6.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iPhoto.presentation.qt.facade.album_facade import AlbumFacade
from iPhoto.presentation.qt.facade.asset_facade import AssetFacade
from iPhoto.presentation.qt.facade.library_facade import LibraryFacade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_album(root: Path = Path("/tmp/album")) -> MagicMock:
    album = MagicMock()
    album.root = root
    album.manifest = {}
    return album


# ---------------------------------------------------------------------------
# AlbumFacade
# ---------------------------------------------------------------------------


class TestAlbumFacade:
    def _make_facade(self, *, album=None, current_album_holder=None) -> tuple[AlbumFacade, dict]:
        mocks: dict = {
            "backend": MagicMock(),
            "metadata_service": MagicMock(),
            "library_update_service": MagicMock(),
            "library_manager": None,
            "error": MagicMock(),
            "album_opened": MagicMock(),
            "load_started": MagicMock(),
            "load_finished": MagicMock(),
            "rescan_trigger": MagicMock(),
        }
        holder: dict[str, object] = {"album": album}
        if current_album_holder is not None:
            holder = current_album_holder

        facade = AlbumFacade(
            backend_bridge=mocks["backend"],
            metadata_service=mocks["metadata_service"],
            library_update_service=mocks["library_update_service"],
            current_album_getter=lambda: holder["album"],
            current_album_setter=lambda a: holder.update({"album": a}),
            library_manager_getter=lambda: mocks["library_manager"],
            error_emitter=mocks["error"],
            album_opened_emitter=mocks["album_opened"],
            load_started_emitter=mocks["load_started"],
            load_finished_emitter=mocks["load_finished"],
            rescan_trigger=mocks["rescan_trigger"],
        )
        return facade, mocks

    def test_set_cover_delegates_to_metadata_service(self):
        mock_album = _make_mock_album()
        facade, mocks = self._make_facade(album=mock_album)

        mocks["metadata_service"].set_album_cover.return_value = True
        result = facade.set_cover("cover.jpg")

        assert result is True
        mocks["metadata_service"].set_album_cover.assert_called_once_with(mock_album, "cover.jpg")

    def test_set_cover_no_album_emits_error(self):
        facade, mocks = self._make_facade(album=None)

        result = facade.set_cover("cover.jpg")

        assert result is False
        mocks["error"].assert_called_once()
        mocks["metadata_service"].set_album_cover.assert_not_called()

    def test_toggle_featured_delegates_to_metadata_service(self):
        mock_album = _make_mock_album()
        facade, mocks = self._make_facade(album=mock_album)

        mocks["metadata_service"].toggle_featured.return_value = True
        result = facade.toggle_featured("photo.jpg")

        assert result is True
        mocks["metadata_service"].toggle_featured.assert_called_once_with(mock_album, "photo.jpg")

    def test_pair_live_current_delegates_to_library_update_service(self):
        mock_album = _make_mock_album()
        facade, mocks = self._make_facade(album=mock_album)

        expected = [{"live_group": "x"}]
        mocks["library_update_service"].pair_live.return_value = expected
        result = facade.pair_live_current()

        assert result == expected
        mocks["library_update_service"].pair_live.assert_called_once_with(mock_album)

    def test_pair_live_no_album_returns_empty_and_emits_error(self):
        facade, mocks = self._make_facade(album=None)

        result = facade.pair_live_current()

        assert result == []
        mocks["error"].assert_called_once()

    def test_open_album_sets_current_album_and_emits_signals(self, tmp_path):
        root = tmp_path / "myalbum"
        root.mkdir()
        mock_album = _make_mock_album(root)
        holder: dict = {"album": None}
        facade, mocks = self._make_facade(current_album_holder=holder)

        mocks["backend"].open_album.return_value = mock_album

        # Simulate non-empty index so no rescan is triggered
        mock_store = MagicMock()
        mock_store.read_all.return_value = iter([{"id": "asset1"}])

        import iPhoto.presentation.qt.facade.album_facade as _mod

        orig_get_global_repository = None
        try:
            import importlib
            import iPhoto.cache.index_store as _index_mod

            orig_get_global_repository = _index_mod.get_global_repository
            _index_mod.get_global_repository = lambda *args, **kwargs: mock_store

            result = facade.open_album(root)
        finally:
            if orig_get_global_repository is not None:
                _index_mod.get_global_repository = orig_get_global_repository

        assert result is mock_album
        assert holder["album"] is mock_album
        mocks["album_opened"].assert_called_once_with(root)
        mocks["load_started"].assert_called_once_with(root)
        mocks["load_finished"].assert_called_once_with(root, True)


# ---------------------------------------------------------------------------
# AssetFacade
# ---------------------------------------------------------------------------


class TestAssetFacade:
    def _make_facade(self) -> tuple[AssetFacade, dict]:
        mocks = {
            "import_service": MagicMock(),
            "move_service": MagicMock(),
            "deletion_service": MagicMock(),
            "restoration_service": MagicMock(),
        }
        facade = AssetFacade(
            import_service=mocks["import_service"],
            move_service=mocks["move_service"],
            deletion_service=mocks["deletion_service"],
            restoration_service=mocks["restoration_service"],
        )
        return facade, mocks

    def test_import_files_delegates_to_import_service(self):
        facade, mocks = self._make_facade()
        sources = [Path("/src/a.jpg"), Path("/src/b.jpg")]
        dest = Path("/dest")

        facade.import_files(sources, destination=dest, mark_featured=True)

        mocks["import_service"].import_files.assert_called_once_with(
            sources, destination=dest, mark_featured=True
        )

    def test_move_assets_delegates_to_move_service(self):
        facade, mocks = self._make_facade()
        sources = [Path("/a.jpg")]
        dest = Path("/dest")

        facade.move_assets(sources, dest)

        mocks["move_service"].move_assets.assert_called_once_with(sources, dest)

    def test_delete_assets_delegates_to_deletion_service(self):
        facade, mocks = self._make_facade()
        sources = [Path("/a.jpg")]

        facade.delete_assets(sources)

        mocks["deletion_service"].delete_assets.assert_called_once_with(sources)

    def test_restore_assets_delegates_to_restoration_service(self):
        facade, mocks = self._make_facade()
        sources = [Path("/trash/a.jpg")]
        mocks["restoration_service"].restore_assets.return_value = True

        result = facade.restore_assets(sources)

        assert result is True
        mocks["restoration_service"].restore_assets.assert_called_once_with(sources)


# ---------------------------------------------------------------------------
# LibraryFacade
# ---------------------------------------------------------------------------


class TestLibraryFacade:
    def _make_facade(self, *, album=None, library_manager=None) -> tuple[LibraryFacade, dict]:
        mocks = {
            "library_update_service": MagicMock(),
            "task_manager": MagicMock(),
            "error": MagicMock(),
        }
        facade = LibraryFacade(
            library_update_service=mocks["library_update_service"],
            task_manager=mocks["task_manager"],
            current_album_getter=lambda: album,
            library_manager_getter=lambda: library_manager,
            error_emitter=mocks["error"],
        )
        return facade, mocks

    def test_rescan_current_delegates_to_update_service(self):
        mock_album = _make_mock_album()
        facade, mocks = self._make_facade(album=mock_album)
        mocks["library_update_service"].rescan_album.return_value = [{"row": 1}]

        result = facade.rescan_current()

        assert result == [{"row": 1}]
        mocks["library_update_service"].rescan_album.assert_called_once_with(mock_album)

    def test_rescan_current_no_album_emits_error_and_returns_empty(self):
        facade, mocks = self._make_facade(album=None)

        result = facade.rescan_current()

        assert result == []
        mocks["error"].assert_called_once()
        mocks["library_update_service"].rescan_album.assert_not_called()

    def test_rescan_current_async_uses_library_manager_when_present(self):
        mock_album = _make_mock_album()
        mock_album.manifest = {"filters": {"include": ["*.jpg"], "exclude": []}}
        mock_manager = MagicMock()
        facade, mocks = self._make_facade(album=mock_album, library_manager=mock_manager)

        facade.rescan_current_async()

        mock_manager.start_scanning.assert_called_once()

    def test_rescan_current_async_falls_back_to_update_service(self):
        mock_album = _make_mock_album()
        facade, mocks = self._make_facade(album=mock_album, library_manager=None)

        facade.rescan_current_async()

        mocks["library_update_service"].rescan_album_async.assert_called_once_with(mock_album)

    def test_cancel_active_scans_delegates_to_manager_and_service(self):
        mock_manager = MagicMock()
        facade, mocks = self._make_facade(library_manager=mock_manager)

        facade.cancel_active_scans()

        mock_manager.stop_scanning.assert_called_once()
        mock_manager.pause_watcher.assert_called_once()
        mocks["library_update_service"].cancel_active_scan.assert_called_once()

    def test_announce_album_refresh_delegates(self):
        root = Path("/album")
        facade, mocks = self._make_facade()

        facade.announce_album_refresh(root, request_reload=True, force_reload=False)

        mocks["library_update_service"].announce_album_refresh.assert_called_once_with(
            root, request_reload=True, force_reload=False, announce_index=False
        )
