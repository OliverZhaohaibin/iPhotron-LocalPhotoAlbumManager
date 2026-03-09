"""Tests for NavigationCoordinator cluster gallery mode cleanup."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator


def _make_coordinator(
    *,
    current_album_root: Path | None = None,
    gallery_active: bool = True,
    library_root: Path | None = None,
) -> NavigationCoordinator:
    """Build a NavigationCoordinator with lightweight mocks."""

    sidebar = MagicMock()
    router = MagicMock()
    router.is_gallery_view_active.return_value = gallery_active

    # gallery_page mock returns an object with set_cluster_gallery_mode
    gallery_page = MagicMock()
    router.gallery_page.return_value = gallery_page

    facade = MagicMock()
    if current_album_root is not None:
        facade.current_album.root.resolve.return_value = current_album_root.resolve()
    else:
        facade.current_album = None

    context = MagicMock()
    context.library.root.return_value = library_root

    album_service = MagicMock()
    asset_vm = MagicMock()
    event_bus = MagicMock()

    coord = NavigationCoordinator(
        sidebar=sidebar,
        router=router,
        album_service=album_service,
        asset_vm=asset_vm,
        event_bus=event_bus,
        context=context,
        facade=facade,
    )
    return coord


class TestClearClusterGalleryMode:
    """Verify that _clear_cluster_gallery_mode resets state and hides the back button."""

    def test_clears_flag_and_calls_gallery_page(self) -> None:
        coord = _make_coordinator()
        coord._in_cluster_gallery = True

        coord._clear_cluster_gallery_mode()

        assert coord._in_cluster_gallery is False
        coord._router.gallery_page().set_cluster_gallery_mode.assert_called_once_with(False)

    def test_noop_when_not_in_cluster_gallery(self) -> None:
        coord = _make_coordinator()
        coord._in_cluster_gallery = False

        coord._clear_cluster_gallery_mode()

        assert coord._in_cluster_gallery is False
        coord._router.gallery_page().set_cluster_gallery_mode.assert_not_called()


class TestNavigationClearsClusterGallery:
    """Ensure navigation methods reset cluster gallery mode."""

    def test_open_all_photos_clears_cluster_gallery(self) -> None:
        coord = _make_coordinator()
        coord._in_cluster_gallery = True

        coord.open_all_photos()

        assert coord._in_cluster_gallery is False
        coord._router.gallery_page().set_cluster_gallery_mode.assert_called_with(False)

    def test_open_album_clears_cluster_gallery(self, tmp_path: Path) -> None:
        album = tmp_path / "TestAlbum"
        album.mkdir()
        coord = _make_coordinator(library_root=tmp_path)
        coord._in_cluster_gallery = True

        coord.open_album(album)

        assert coord._in_cluster_gallery is False
        coord._router.gallery_page().set_cluster_gallery_mode.assert_called_with(False)

    def test_open_location_view_clears_cluster_gallery(self, tmp_path: Path) -> None:
        coord = _make_coordinator(library_root=tmp_path)
        coord._in_cluster_gallery = True

        coord.open_location_view()

        assert coord._in_cluster_gallery is False
        coord._router.gallery_page().set_cluster_gallery_mode.assert_called_with(False)
