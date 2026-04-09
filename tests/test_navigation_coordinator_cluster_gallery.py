"""Tests for NavigationCoordinator Location and cluster-gallery flows."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
    router.is_edit_view_active.return_value = False
    router.gallery_page.return_value = MagicMock()
    router.map_view.return_value = MagicMock()

    facade = MagicMock()
    if current_album_root is not None:
        facade.current_album.root.resolve.return_value = current_album_root.resolve()
    else:
        facade.current_album = None

    context = MagicMock()
    context.library.root.return_value = library_root

    asset_vm = MagicMock()

    return NavigationCoordinator(
        sidebar=sidebar,
        router=router,
        asset_vm=asset_vm,
        context=context,
        facade=facade,
    )


def _make_geo_asset(root: Path, rel: str) -> SimpleNamespace:
    return SimpleNamespace(
        library_relative=Path(rel).as_posix(),
        absolute_path=root / Path(rel),
    )


def _prime_location_snapshot(coord: NavigationCoordinator, root: Path, assets: list[object]) -> int:
    serial = coord._location_session.begin_load(root)
    assert coord._location_session.accept_loaded(serial, root, assets) is True
    return serial


class TestClearClusterGalleryMode:
    """Verify that cluster-gallery cleanup resets the session and hides the header."""

    def test_clears_flag_and_calls_gallery_page(self) -> None:
        coord = _make_coordinator()
        coord._static_selection = "Location"
        coord._location_session.set_mode("cluster_gallery")

        coord._clear_cluster_gallery_mode()

        assert coord.is_in_cluster_gallery() is False
        assert coord._location_session.mode == "map"
        coord._router.gallery_page().set_cluster_gallery_mode.assert_called_once_with(False)

    def test_noop_when_not_in_cluster_gallery(self) -> None:
        coord = _make_coordinator()
        coord._location_session.set_mode("inactive")

        coord._clear_cluster_gallery_mode()

        assert coord.is_in_cluster_gallery() is False
        coord._router.gallery_page().set_cluster_gallery_mode.assert_not_called()


class TestNavigationClearsClusterGallery:
    """Ensure navigation methods reset cluster-gallery mode."""

    def test_open_all_photos_clears_cluster_gallery(self) -> None:
        coord = _make_coordinator()
        coord._location_session.set_mode("cluster_gallery")

        coord.open_all_photos()

        assert coord.is_in_cluster_gallery() is False
        assert coord._location_session.mode == "inactive"
        coord._router.gallery_page().set_cluster_gallery_mode.assert_called_with(False)

    def test_open_album_clears_cluster_gallery(self, tmp_path: Path) -> None:
        album = tmp_path / "TestAlbum"
        album.mkdir()
        coord = _make_coordinator(library_root=tmp_path)
        coord._location_session.set_mode("cluster_gallery")
        coord._album_path_for_query = MagicMock(return_value="TestAlbum")

        opened_album = MagicMock()
        opened_album.root = album
        coord._facade.open_album.return_value = opened_album

        coord.open_album(album)

        assert coord.is_in_cluster_gallery() is False
        assert coord._location_session.mode == "inactive"
        coord._router.gallery_page().set_cluster_gallery_mode.assert_called_with(False)

    def test_open_location_view_clears_cluster_gallery(self, tmp_path: Path) -> None:
        coord = _make_coordinator(library_root=tmp_path)
        coord._static_selection = "Location"
        coord._location_session.set_mode("cluster_gallery")

        with patch("iPhoto.gui.coordinators.navigation_coordinator.QTimer.singleShot") as single_shot:
            coord.open_location_view()

        assert coord.is_in_cluster_gallery() is False
        assert coord._location_session.mode == "map"
        coord._router.gallery_page().set_cluster_gallery_mode.assert_called_with(False)
        single_shot.assert_called_once()


def test_open_cluster_gallery_uses_single_selection_entry(tmp_path: Path) -> None:
    coord = _make_coordinator(library_root=tmp_path)
    assets = [MagicMock(name="asset-1"), MagicMock(name="asset-2")]

    coord.open_cluster_gallery(assets)

    assert coord.is_in_cluster_gallery() is True
    coord._asset_vm.load_selection.assert_called_once_with(
        tmp_path,
        direct_assets=assets,
        library_root=tmp_path,
    )


def test_open_filtered_collection_uses_single_selection_entry(tmp_path: Path) -> None:
    coord = _make_coordinator(library_root=tmp_path)

    coord._open_filtered_collection("Favorites", is_favorite=True)

    coord._asset_vm.load_selection.assert_called_once()
    active_root = coord._asset_vm.load_selection.call_args.args[0]
    query = coord._asset_vm.load_selection.call_args.kwargs["query"]
    assert active_root == tmp_path
    assert query.is_favorite is True


def test_open_location_view_reuses_cached_snapshot_without_worker(tmp_path: Path) -> None:
    coord = _make_coordinator(library_root=tmp_path)
    assets = [_make_geo_asset(tmp_path, "a.jpg"), _make_geo_asset(tmp_path, "nested/b.jpg")]
    _prime_location_snapshot(coord, tmp_path, assets)

    with patch("iPhoto.gui.coordinators.navigation_coordinator.QTimer.singleShot") as single_shot:
        coord.open_location_view()

    coord._router.show_map.assert_called_once()
    coord._asset_vm.set_active_root.assert_called_once_with(tmp_path)
    coord._router.map_view().set_assets.assert_called_once_with(assets, tmp_path)
    coord._router.map_view().clear.assert_not_called()
    single_shot.assert_not_called()


def test_open_location_view_invalidated_snapshot_starts_worker(tmp_path: Path) -> None:
    coord = _make_coordinator(library_root=tmp_path)
    assets = [_make_geo_asset(tmp_path, "a.jpg")]
    _prime_location_snapshot(coord, tmp_path, assets)
    coord.invalidate_location_session()

    with patch("iPhoto.gui.coordinators.navigation_coordinator.QTimer.singleShot") as single_shot:
        coord.open_location_view()

    coord._router.map_view().clear.assert_called_once_with()
    coord._router.map_view().set_assets.assert_not_called()
    single_shot.assert_called_once()


def test_handle_location_assets_loaded_accepts_current_request_and_caches_snapshot(tmp_path: Path) -> None:
    coord = _make_coordinator(library_root=tmp_path)
    assets = [_make_geo_asset(tmp_path, "a.jpg"), _make_geo_asset(tmp_path, "nested/b.jpg")]
    serial = coord._location_session.begin_load(tmp_path)

    coord._handle_location_assets_loaded(serial, tmp_path, assets, 12.5)

    assert coord._location_session.has_snapshot is True
    assert coord._location_session.invalidated is False
    assert coord._location_session.full_assets() == assets
    coord._router.map_view().set_assets.assert_called_once_with(assets, tmp_path)


def test_handle_location_assets_loaded_ignores_stale_request(tmp_path: Path) -> None:
    coord = _make_coordinator(library_root=tmp_path)
    stale_serial = coord._location_session.begin_load(tmp_path)
    coord._location_session.begin_load(tmp_path)

    coord._handle_location_assets_loaded(
        stale_serial,
        tmp_path,
        [_make_geo_asset(tmp_path, "a.jpg")],
        5.0,
    )

    assert coord._location_session.has_snapshot is False
    coord._router.map_view().set_assets.assert_not_called()


def test_open_location_asset_uses_full_location_snapshot_and_playback_row(tmp_path: Path) -> None:
    coord = _make_coordinator(library_root=tmp_path)
    assets = [_make_geo_asset(tmp_path, "a.jpg"), _make_geo_asset(tmp_path, "nested/b.jpg")]
    _prime_location_snapshot(coord, tmp_path, assets)
    coord._asset_vm.row_for_path.return_value = 1
    playback = MagicMock()
    coord.set_playback_coordinator(playback)

    coord.open_location_asset("nested/b.jpg")

    coord._asset_vm.load_selection.assert_called_once_with(
        tmp_path,
        direct_assets=assets,
        library_root=tmp_path,
    )
    coord._asset_vm.row_for_path.assert_called_once_with(tmp_path / "nested" / "b.jpg")
    playback.play_asset.assert_called_once_with(1)
    assert coord._location_session.mode == "gallery"
    assert coord.static_selection() == "Location"


def test_return_to_map_from_cluster_gallery_reuses_cached_snapshot(tmp_path: Path) -> None:
    coord = _make_coordinator(library_root=tmp_path)
    assets = [_make_geo_asset(tmp_path, "a.jpg"), _make_geo_asset(tmp_path, "nested/b.jpg")]
    _prime_location_snapshot(coord, tmp_path, assets)
    coord._static_selection = "Location"
    coord._location_session.set_mode("cluster_gallery")

    with patch("iPhoto.gui.coordinators.navigation_coordinator.QTimer.singleShot") as single_shot:
        coord.return_to_map_from_cluster_gallery()

    assert coord._location_session.mode == "map"
    coord._router.gallery_page().set_cluster_gallery_mode.assert_called_once_with(False)
    coord._router.show_map.assert_called_once()
    coord._router.map_view().set_assets.assert_called_once_with(assets, tmp_path)
    coord._router.map_view().clear.assert_not_called()
    single_shot.assert_not_called()
