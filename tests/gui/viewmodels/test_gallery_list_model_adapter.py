from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QModelIndex, Qt

from iPhoto.application.dtos import AssetDTO
from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.viewmodels.gallery_collection_store import GalleryCollectionStore
from iPhoto.gui.viewmodels.gallery_list_model_adapter import GalleryListModelAdapter
from iPhoto.gui.viewmodels.gallery_page_loader import GalleryPageRequest
from iPhoto.gui.viewmodels.signal import Signal
from iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService


@pytest.fixture
def mock_store():
    store = MagicMock(spec=GalleryCollectionStore)
    store.data_changed = MagicMock()
    store.window_changed = MagicMock()
    store.row_changed = MagicMock()
    store.window_load_requested = Signal()
    store.count.return_value = 0
    store.selection_revision = 0
    store.asset_query_service.return_value = MagicMock()
    store.library_root.return_value = Path("/library")
    return store


@pytest.fixture
def mock_thumb_service():
    return MagicMock(spec=ThumbnailCacheService)


@pytest.fixture
def adapter(mock_store, mock_thumb_service):
    return GalleryListModelAdapter(mock_store, mock_thumb_service)


def _make_dto(**overrides) -> AssetDTO:
    defaults = dict(
        id="1",
        abs_path=Path("photo.jpg"),
        rel_path=Path("photo.jpg"),
        media_type="image",
        created_at=None,
        width=100,
        height=100,
        duration=0.0,
        size_bytes=100,
        metadata={},
        is_favorite=False,
    )
    defaults.update(overrides)
    return AssetDTO(**defaults)


def test_adapter_init(adapter):
    assert adapter.rowCount() == 0


def test_info_role_contains_required_keys(adapter, mock_store):
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = _make_dto(
        rel_path=Path("clip.mov"),
        abs_path=Path("/lib/clip.mov"),
        media_type="video",
        width=1920,
        height=1080,
        duration=8.5,
        size_bytes=1_000_000,
    )

    index = adapter.index(0, 0)
    info = adapter.data(index, Roles.INFO)

    for key in ("rel", "abs", "name", "is_video", "w", "h", "dur", "bytes"):
        assert key in info


def test_data_display_role(adapter, mock_store):
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = _make_dto(rel_path=Path("photo.jpg"))

    index = adapter.index(0, 0)
    assert adapter.data(index, Qt.DisplayRole) == "photo.jpg"


def test_row_for_path_delegates_to_store(adapter, mock_store):
    path = Path("/library/photo.jpg")
    mock_store.row_for_path.return_value = 7

    assert adapter.row_for_path(path) == 7
    mock_store.row_for_path.assert_called_once_with(path)


def test_prioritize_rows_delegates_to_store(adapter, mock_store):
    adapter.prioritize_rows(10, 25)
    mock_store.prioritize_rows.assert_called_once_with(10, 25)


def test_rebind_asset_query_service_updates_store(adapter, mock_store):
    query_service = MagicMock()
    root = Path("/library")

    adapter.rebind_asset_query_service(query_service, root)

    mock_store.rebind_asset_query_service.assert_called_once_with(query_service, root)


def test_invalidate_thumbnail_clears_duration_cache_and_emits_size_role(adapter, mock_store):
    path = Path("/videos/clip.mp4")
    adapter._duration_cache[path] = 8.0
    mock_store.row_for_path.return_value = 0
    mock_store.count.return_value = 1

    emitted_roles = []
    adapter.dataChanged.connect(lambda _top, _bottom, roles: emitted_roles.extend(roles))

    with patch.object(adapter._thumbnails, "invalidate"):
        adapter.invalidate_thumbnail(str(path))

    assert path not in adapter._duration_cache
    assert Roles.SIZE in emitted_roles


def test_size_role_returns_trimmed_duration_for_video(adapter, mock_store):
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = _make_dto(
        abs_path=Path("/videos/clip.mp4"),
        media_type="video",
        duration=10.0,
    )

    edit_service = MagicMock()
    edit_service.describe_adjustments.return_value = SimpleNamespace(
        effective_duration_sec=5.0,
    )
    adapter._edit_service_getter = lambda: edit_service

    index = adapter.index(0, 0)
    result = adapter.data(index, Roles.SIZE)

    assert result["duration"] == pytest.approx(5.0)
    edit_service.describe_adjustments.assert_called_once_with(
        Path("/videos/clip.mp4"),
        duration_hint=10.0,
    )


def test_invalid_index_returns_none(adapter):
    assert adapter.data(QModelIndex(), Qt.DisplayRole) is None


def test_row_changed_emits_targeted_favorite_update(adapter, mock_store):
    mock_store.count.return_value = 1
    mock_store.asset_at.return_value = _make_dto()

    emitted_roles = []
    adapter.dataChanged.connect(lambda _top, _bottom, roles: emitted_roles.extend(roles))

    adapter._on_row_changed(0)

    assert Roles.FEATURED in emitted_roles


def test_source_changed_skips_reset_when_selection_and_count_are_unchanged(
    adapter,
    mock_store,
):
    mock_store.count.return_value = 0
    mock_store.selection_revision = 0

    with patch.object(adapter, "beginResetModel") as begin_reset, patch.object(
        adapter,
        "endResetModel",
    ) as end_reset:
        adapter._on_source_changed()

    begin_reset.assert_not_called()
    end_reset.assert_not_called()


def test_source_changed_resets_when_selection_revision_changes(
    adapter,
    mock_store,
):
    mock_store.count.return_value = 3
    mock_store.selection_revision = 1

    with patch.object(adapter, "beginResetModel") as begin_reset, patch.object(
        adapter,
        "endResetModel",
    ) as end_reset:
        adapter._on_source_changed()

    begin_reset.assert_called_once()
    end_reset.assert_called_once()


def test_window_load_requested_delegates_to_page_loader(adapter, mock_store):
    request = GalleryPageRequest(
        request_id=1,
        selection_revision=1,
        root=Path("/library"),
        query=MagicMock(),
        first=10,
        last=50,
    )

    with patch.object(adapter._page_loader, "load") as load:
        adapter._on_window_load_requested(request)
        load.assert_called_once_with(
            asset_query_service=mock_store.asset_query_service(),
            library_root=mock_store.library_root(),
            request=request,
        )


def test_window_load_failed_delegates_to_store(adapter, mock_store):
    adapter._on_window_load_failed(5, 9)

    mock_store.handle_window_load_failed.assert_called_once_with(5, 9)


def test_resolve_live_motion_uses_sync_store_scan(adapter, mock_store):
    asset = _make_dto(metadata={"live_photo_group_id": "group-1"})
    candidate = _make_dto(
        rel_path=Path("photo.mov"),
        abs_path=Path("/library/photo.mov"),
        media_type="video",
        duration=5.0,
        metadata={"live_photo_group_id": "group-1"},
    )
    mock_store.count.return_value = 2
    mock_store.asset_at.return_value = None
    mock_store.asset_at_sync.side_effect = [None, candidate]

    motion_rel, motion_abs = adapter._resolve_live_motion(asset)

    assert motion_rel == candidate.rel_path
    assert motion_abs == candidate.abs_path
    assert mock_store.asset_at_sync.call_count == 2
    mock_store.asset_at.assert_not_called()
