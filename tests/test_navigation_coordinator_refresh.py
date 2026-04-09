"""Tests for NavigationCoordinator binder behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator


def _make_coordinator(*, current_album_root: Path | None = None, gallery_active: bool = True) -> NavigationCoordinator:
    sidebar = MagicMock()
    router = MagicMock()
    router.is_gallery_view_active.return_value = gallery_active
    facade = MagicMock()
    if current_album_root is not None:
        facade.current_album.root.resolve.return_value = current_album_root.resolve()
    else:
        facade.current_album = None
    context = MagicMock()
    gallery_vm = MagicMock()
    gallery_vm.static_selection.value = None
    gallery_vm.bind_library_requested = MagicMock()
    gallery_vm.route_requested = MagicMock()
    gallery_vm.detail_requested = MagicMock()
    gallery_vm.map_assets_changed = MagicMock()
    gallery_vm.cluster_gallery_mode_changed = MagicMock()
    gallery_vm.sidebar_path_requested = MagicMock()
    return NavigationCoordinator(
        sidebar=sidebar,
        router=router,
        gallery_vm=gallery_vm,
        context=context,
        facade=facade,
    )


def test_same_album_in_gallery_is_refresh(tmp_path: Path) -> None:
    album = tmp_path / "Paris"
    album.mkdir()
    coord = _make_coordinator(current_album_root=album, gallery_active=True)

    assert coord._should_treat_as_refresh(album) is True


def test_same_album_after_static_selection_is_not_refresh(tmp_path: Path) -> None:
    album = tmp_path / "Paris"
    album.mkdir()
    coord = _make_coordinator(current_album_root=album, gallery_active=True)
    coord._gallery_vm.static_selection.value = "All Photos"

    assert coord._should_treat_as_refresh(album) is False


def test_open_album_delegates_to_gallery_vm(tmp_path: Path) -> None:
    album = tmp_path / "Paris"
    album.mkdir()
    coord = _make_coordinator(current_album_root=None, gallery_active=True)

    coord.open_album(album)

    coord._gallery_vm.open_album.assert_called_once_with(album)


def test_open_all_photos_delegates_to_gallery_vm() -> None:
    coord = _make_coordinator()

    coord.open_all_photos()

    coord._gallery_vm.open_all_photos.assert_called_once_with()


def test_open_recently_deleted_delegates_to_gallery_vm() -> None:
    coord = _make_coordinator()

    coord.open_recently_deleted()

    coord._gallery_vm.open_recently_deleted.assert_called_once_with()
