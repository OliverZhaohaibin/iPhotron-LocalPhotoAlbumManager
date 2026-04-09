"""Tests for NavigationCoordinator Location and cluster-gallery binder flows."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator


def _make_coordinator(
    *,
    current_album_root: Path | None = None,
    gallery_active: bool = True,
) -> NavigationCoordinator:
    sidebar = MagicMock()
    router = MagicMock()
    router.is_gallery_view_active.return_value = gallery_active
    router.gallery_page.return_value = MagicMock()
    router.map_view.return_value = MagicMock()

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


def test_open_location_view_delegates_to_gallery_vm() -> None:
    coord = _make_coordinator()

    coord.open_location_view()

    coord._gallery_vm.open_location_map.assert_called_once_with()


def test_open_cluster_gallery_delegates_to_gallery_vm() -> None:
    coord = _make_coordinator()
    assets = [MagicMock(), MagicMock()]

    coord.open_cluster_gallery(assets)

    coord._gallery_vm.open_cluster_gallery.assert_called_once_with(assets)


def test_open_location_asset_delegates_to_gallery_vm() -> None:
    coord = _make_coordinator()

    coord.open_location_asset("nested/b.jpg")

    coord._gallery_vm.open_location_asset.assert_called_once_with("nested/b.jpg")


def test_route_requested_updates_router() -> None:
    coord = _make_coordinator()

    coord._handle_route_requested("gallery")
    coord._handle_route_requested("map")
    coord._handle_route_requested("albums_dashboard")
    coord._handle_route_requested("detail")

    coord._router.show_gallery.assert_called_once_with()
    coord._router.show_map.assert_called_once_with()
    coord._router.show_albums_dashboard.assert_called_once_with()
    coord._router.show_detail.assert_called_once_with()


def test_detail_requested_uses_playback_coordinator() -> None:
    coord = _make_coordinator()
    playback = MagicMock()
    coord.set_playback_coordinator(playback)

    coord._handle_detail_requested(3)

    playback.play_asset.assert_called_once_with(3)


def test_cluster_gallery_mode_signal_updates_header() -> None:
    coord = _make_coordinator()

    coord._handle_cluster_gallery_mode_changed(True)
    coord._handle_cluster_gallery_mode_changed(False)

    assert coord._router.gallery_page().set_cluster_gallery_mode.call_count == 2
