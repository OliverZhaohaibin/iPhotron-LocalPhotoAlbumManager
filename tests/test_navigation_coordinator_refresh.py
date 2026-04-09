"""Tests for NavigationCoordinator refresh and open-path behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator


def _make_coordinator(*, current_album_root: Path | None = None, gallery_active: bool = True) -> NavigationCoordinator:
    """Build a NavigationCoordinator with lightweight mocks."""

    sidebar = MagicMock()
    router = MagicMock()
    router.is_gallery_view_active.return_value = gallery_active

    facade = MagicMock()
    if current_album_root is not None:
        facade.current_album.root.resolve.return_value = current_album_root.resolve()
    else:
        facade.current_album = None

    context = MagicMock()
    asset_vm = MagicMock()

    coord = NavigationCoordinator(
        sidebar=sidebar,
        router=router,
        asset_vm=asset_vm,
        context=context,
        facade=facade,
    )
    return coord


class TestShouldTreatAsRefresh:
    """Verify that _should_treat_as_refresh correctly gates redundant opens."""

    def test_same_album_in_gallery_is_refresh(self, tmp_path: Path) -> None:
        album = tmp_path / "Paris"
        album.mkdir()
        coord = _make_coordinator(current_album_root=album, gallery_active=True)
        coord._static_selection = None

        assert coord._should_treat_as_refresh(album) is True

    def test_different_album_is_not_refresh(self, tmp_path: Path) -> None:
        paris = tmp_path / "Paris"
        paris.mkdir()
        tokyo = tmp_path / "Tokyo"
        tokyo.mkdir()
        coord = _make_coordinator(current_album_root=paris, gallery_active=True)
        coord._static_selection = None

        assert coord._should_treat_as_refresh(tokyo) is False

    def test_same_album_after_static_selection_is_not_refresh(self, tmp_path: Path) -> None:
        """Core regression test: switching Paris -> All Photos -> Paris must navigate."""
        album = tmp_path / "Paris"
        album.mkdir()
        coord = _make_coordinator(current_album_root=album, gallery_active=True)
        # Simulate having navigated to a static section (e.g. All Photos)
        coord._static_selection = "All Photos"

        assert coord._should_treat_as_refresh(album) is False

    def test_no_current_album_is_not_refresh(self, tmp_path: Path) -> None:
        album = tmp_path / "Paris"
        album.mkdir()
        coord = _make_coordinator(current_album_root=None, gallery_active=True)

        assert coord._should_treat_as_refresh(album) is False

    def test_same_album_not_gallery_is_not_refresh(self, tmp_path: Path) -> None:
        album = tmp_path / "Paris"
        album.mkdir()
        coord = _make_coordinator(current_album_root=album, gallery_active=False)
        coord._static_selection = None

        assert coord._should_treat_as_refresh(album) is False


def test_open_album_uses_single_facade_path(tmp_path: Path) -> None:
    album = tmp_path / "Paris"
    album.mkdir()
    coord = _make_coordinator(current_album_root=None, gallery_active=True)
    coord._album_path_for_query = MagicMock(return_value="Paris")

    opened_album = MagicMock()
    opened_album.root = album
    coord._facade.open_album.return_value = opened_album

    coord.open_album(album)

    coord._facade.open_album.assert_called_once_with(album)
    coord._context.remember_album.assert_called_once_with(album)
    coord._asset_vm.load_query.assert_called_once()
