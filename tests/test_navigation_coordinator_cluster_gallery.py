"""Tests for NavigationCoordinator Location and cluster-gallery binder flows."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from iPhoto.domain.models.query import AssetQuery
import iPhoto.gui.coordinators.navigation_coordinator as navigation_coordinator_module
from iPhoto.gui.coordinators.navigation_coordinator import NavigationCoordinator
from iPhoto.gui.services.pinned_items_service import PinnedSidebarItem


def _make_coordinator(
    *,
    current_album_root: Path | None = None,
    gallery_active: bool = True,
    pinned_items_service=None,
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
        pinned_items_service=pinned_items_service,
    )


def test_open_location_view_delegates_to_gallery_vm() -> None:
    coord = _make_coordinator()

    coord.open_location_view()

    coord._gallery_vm.open_location_map.assert_called_once_with()
    coord._context.library.get_geotagged_assets.assert_not_called()


def test_open_people_view_delegates_to_gallery_vm() -> None:
    coord = _make_coordinator()

    coord.open_people_view()

    coord._gallery_vm.open_people_dashboard.assert_called_once_with()


def test_open_cluster_gallery_delegates_to_gallery_vm() -> None:
    coord = _make_coordinator()
    assets = [MagicMock(), MagicMock()]

    coord.open_cluster_gallery(assets)

    coord._gallery_vm.open_cluster_gallery.assert_called_once_with(assets)


def test_open_location_asset_delegates_to_gallery_vm() -> None:
    coord = _make_coordinator()

    coord.open_location_asset("nested/b.jpg")

    coord._gallery_vm.open_location_asset.assert_called_once_with("nested/b.jpg")


def test_open_pinned_album_delegates_to_gallery_vm(tmp_path: Path) -> None:
    coord = _make_coordinator()
    target = tmp_path / "Trips"
    target.mkdir()

    coord.open_pinned_item(PinnedSidebarItem(kind="album", item_id=str(target), label="Trips"))

    coord._gallery_vm.open_pinned_album.assert_called_once_with(target)


def test_open_pinned_missing_album_warns_and_prunes(tmp_path: Path, monkeypatch) -> None:
    pinned_items_service = MagicMock()
    coord = _make_coordinator(pinned_items_service=pinned_items_service)
    coord._context.library.root.return_value = tmp_path
    warnings: list[str] = []
    monkeypatch.setattr(
        navigation_coordinator_module.dialogs,
        "show_warning",
        lambda _parent, message, title="iPhoto": warnings.append(message),
    )

    coord.open_pinned_item(PinnedSidebarItem(kind="album", item_id=str(tmp_path / "Missing"), label="Trips"))

    coord._gallery_vm.open_pinned_album.assert_not_called()
    pinned_items_service.prune_missing_album.assert_called_once_with(
        tmp_path / "Missing",
        library_root=tmp_path,
    )
    assert warnings == [
        "Pinned album 'Trips' is no longer available and will be removed from the sidebar."
    ]


def test_open_pinned_person_keeps_valid_empty_pin(tmp_path: Path, monkeypatch) -> None:
    pinned_items_service = MagicMock()
    coord = _make_coordinator(pinned_items_service=pinned_items_service)
    coord._context.library.root.return_value = tmp_path

    class _StubPeopleService:
        def __init__(self, library_root: Path) -> None:
            self.library_root = library_root

        def build_cluster_query(self, person_id: str) -> AssetQuery:
            return AssetQuery(asset_ids=[])

        def has_cluster(self, person_id: str) -> bool:
            return True

    monkeypatch.setattr(
        navigation_coordinator_module,
        "create_people_service",
        lambda library_root: _StubPeopleService(library_root),
    )

    coord.open_pinned_item(PinnedSidebarItem(kind="person", item_id="person-a", label="Alice"))

    coord._gallery_vm.open_pinned_people_query.assert_called_once_with(
        AssetQuery(asset_ids=[]),
        kind="person",
        entity_id="person-a",
    )
    pinned_items_service.prune_missing_entity.assert_not_called()


def test_open_pinned_missing_person_prunes_invalid_pin(tmp_path: Path, monkeypatch) -> None:
    pinned_items_service = MagicMock()
    coord = _make_coordinator(pinned_items_service=pinned_items_service)
    coord._context.library.root.return_value = tmp_path
    warnings: list[str] = []

    class _StubPeopleService:
        def __init__(self, library_root: Path) -> None:
            self.library_root = library_root

        def build_cluster_query(self, person_id: str) -> AssetQuery:
            return AssetQuery(asset_ids=[])

        def has_cluster(self, person_id: str) -> bool:
            return False

    monkeypatch.setattr(
        navigation_coordinator_module,
        "create_people_service",
        lambda library_root: _StubPeopleService(library_root),
    )
    monkeypatch.setattr(
        navigation_coordinator_module.dialogs,
        "show_warning",
        lambda _parent, message, title="iPhoto": warnings.append(message),
    )

    coord.open_pinned_item(PinnedSidebarItem(kind="person", item_id="missing-person", label="Ghost"))

    coord._gallery_vm.open_pinned_people_query.assert_not_called()
    pinned_items_service.prune_missing_entity.assert_called_once_with(
        kind="person",
        item_id="missing-person",
        library_root=tmp_path,
    )
    assert warnings == [
        "Pinned person 'Ghost' is no longer available and will be removed from the sidebar."
    ]


def test_open_pinned_missing_group_warns_and_prunes(tmp_path: Path, monkeypatch) -> None:
    pinned_items_service = MagicMock()
    coord = _make_coordinator(pinned_items_service=pinned_items_service)
    coord._context.library.root.return_value = tmp_path
    warnings: list[str] = []

    class _StubPeopleService:
        def __init__(self, library_root: Path) -> None:
            self.library_root = library_root

        def build_group_query(self, group_id: str) -> AssetQuery:
            return AssetQuery(asset_ids=[])

        def has_group(self, group_id: str) -> bool:
            return False

    monkeypatch.setattr(
        navigation_coordinator_module,
        "create_people_service",
        lambda library_root: _StubPeopleService(library_root),
    )
    monkeypatch.setattr(
        navigation_coordinator_module.dialogs,
        "show_warning",
        lambda _parent, message, title="iPhoto": warnings.append(message),
    )

    coord.open_pinned_item(PinnedSidebarItem(kind="group", item_id="missing-group", label="Group 1"))

    coord._gallery_vm.open_pinned_people_query.assert_not_called()
    pinned_items_service.prune_missing_entity.assert_called_once_with(
        kind="group",
        item_id="missing-group",
        library_root=tmp_path,
    )
    assert warnings == [
        "Pinned group 'Group 1' is no longer available and will be removed from the sidebar."
    ]


def test_open_pinned_person_does_not_prune_on_people_service_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pinned_items_service = MagicMock()
    coord = _make_coordinator(pinned_items_service=pinned_items_service)
    coord._context.library.root.return_value = tmp_path
    warnings: list[str] = []

    class _StubPeopleService:
        def __init__(self, library_root: Path) -> None:
            self.library_root = library_root

        def build_cluster_query(self, person_id: str) -> AssetQuery:
            raise RuntimeError("face index unavailable")

    monkeypatch.setattr(
        navigation_coordinator_module,
        "create_people_service",
        lambda library_root: _StubPeopleService(library_root),
    )
    monkeypatch.setattr(
        navigation_coordinator_module.dialogs,
        "show_warning",
        lambda _parent, message, title="iPhoto": warnings.append(message),
    )

    coord.open_pinned_item(PinnedSidebarItem(kind="person", item_id="person-a", label="Alice"))

    coord._gallery_vm.open_pinned_people_query.assert_not_called()
    pinned_items_service.prune_missing_entity.assert_not_called()
    assert warnings == []


def test_open_pinned_group_does_not_prune_on_people_service_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pinned_items_service = MagicMock()
    coord = _make_coordinator(pinned_items_service=pinned_items_service)
    coord._context.library.root.return_value = tmp_path
    warnings: list[str] = []

    class _StubPeopleService:
        def __init__(self, library_root: Path) -> None:
            self.library_root = library_root

        def build_group_query(self, group_id: str) -> AssetQuery:
            raise RuntimeError("face index unavailable")

    monkeypatch.setattr(
        navigation_coordinator_module,
        "create_people_service",
        lambda library_root: _StubPeopleService(library_root),
    )
    monkeypatch.setattr(
        navigation_coordinator_module.dialogs,
        "show_warning",
        lambda _parent, message, title="iPhoto": warnings.append(message),
    )

    coord.open_pinned_item(PinnedSidebarItem(kind="group", item_id="group-a", label="Group 1"))

    coord._gallery_vm.open_pinned_people_query.assert_not_called()
    pinned_items_service.prune_missing_entity.assert_not_called()
    assert warnings == []


def test_route_requested_updates_router() -> None:
    coord = _make_coordinator()

    coord._handle_route_requested("gallery")
    coord._handle_route_requested("people")
    coord._handle_route_requested("map")
    coord._handle_route_requested("albums_dashboard")
    coord._handle_route_requested("detail")

    coord._router.show_gallery.assert_called_once_with()
    coord._router.show_people.assert_called_once_with()
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
    coord._gallery_vm.cluster_gallery_back_tooltip.return_value = "Return to People"

    coord._handle_cluster_gallery_mode_changed(True)
    coord._handle_cluster_gallery_mode_changed(False)

    assert coord._router.gallery_page().set_cluster_gallery_mode.call_count == 2
    coord._router.gallery_page().set_cluster_gallery_mode.assert_any_call(
        True,
        back_tooltip="Return to People",
    )
