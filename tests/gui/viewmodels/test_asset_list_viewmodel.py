import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from PySide6.QtCore import Qt, QModelIndex, QSize

from iPhoto.gui.viewmodels.asset_list_viewmodel import AssetListViewModel
from iPhoto.gui.viewmodels.asset_data_source import AssetDataSource
from iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService
from iPhoto.application.dtos import AssetDTO
from iPhoto.gui.ui.models.roles import Roles
from iPhoto.domain.models.query import AssetQuery

@pytest.fixture
def mock_data_source():
    return MagicMock(spec=AssetDataSource)

@pytest.fixture
def mock_thumb_service():
    return MagicMock(spec=ThumbnailCacheService)

@pytest.fixture
def view_model(mock_data_source, mock_thumb_service):
    # Default count for mock is a MagicMock object, which isn't 0.
    # Set default behavior
    mock_data_source.count.return_value = 0
    return AssetListViewModel(mock_data_source, mock_thumb_service)


def _make_dto(**overrides) -> AssetDTO:
    """Create a minimal AssetDTO for testing."""
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


def test_viewmodel_init(view_model):
    assert view_model.rowCount() == 0


# ---------------------------------------------------------------------------
# Regression tests: Roles.INFO must include is_video and name
# ---------------------------------------------------------------------------

def test_info_role_includes_is_video_false_for_photo(view_model, mock_data_source):
    """Roles.INFO dict must carry is_video=False for photo assets (regression guard)."""
    mock_data_source.count.return_value = 1
    dto = _make_dto(rel_path=Path("photo.jpg"), media_type="image")
    mock_data_source.asset_at.return_value = dto

    index = view_model.index(0, 0)
    info = view_model.data(index, Roles.INFO)

    assert isinstance(info, dict)
    assert "is_video" in info, "Roles.INFO must contain 'is_video' key"
    assert info["is_video"] is False


def test_info_role_includes_is_video_true_for_video(view_model, mock_data_source):
    """Roles.INFO dict must carry is_video=True for video assets (regression guard)."""
    mock_data_source.count.return_value = 1
    dto = _make_dto(
        rel_path=Path("clip.mp4"),
        abs_path=Path("/videos/clip.mp4"),
        media_type="video",
        duration=5.0,
    )
    mock_data_source.asset_at.return_value = dto

    index = view_model.index(0, 0)
    info = view_model.data(index, Roles.INFO)

    assert isinstance(info, dict)
    assert "is_video" in info, "Roles.INFO must contain 'is_video' key"
    assert info["is_video"] is True


def test_info_role_includes_name_for_photo(view_model, mock_data_source):
    """Roles.INFO dict must carry the filename in 'name' for photo assets."""
    mock_data_source.count.return_value = 1
    dto = _make_dto(rel_path=Path("subdir/IMG_0001.jpg"), media_type="image")
    mock_data_source.asset_at.return_value = dto

    index = view_model.index(0, 0)
    info = view_model.data(index, Roles.INFO)

    assert isinstance(info, dict)
    assert info.get("name") == "IMG_0001.jpg"


def test_info_role_includes_name_for_video(view_model, mock_data_source):
    """Roles.INFO dict must carry the filename in 'name' for video assets."""
    mock_data_source.count.return_value = 1
    dto = _make_dto(rel_path=Path("subdir/IMG_3160.MOV"), media_type="video")
    mock_data_source.asset_at.return_value = dto

    index = view_model.index(0, 0)
    info = view_model.data(index, Roles.INFO)

    assert isinstance(info, dict)
    assert info.get("name") == "IMG_3160.MOV"


def test_info_role_contains_required_keys(view_model, mock_data_source):
    """Roles.INFO dict must always contain the full set of keys InfoPanel depends on."""
    mock_data_source.count.return_value = 1
    dto = _make_dto(
        rel_path=Path("clip.mov"),
        abs_path=Path("/lib/clip.mov"),
        media_type="video",
        width=1920,
        height=1080,
        duration=8.5,
        size_bytes=1_000_000,
    )
    mock_data_source.asset_at.return_value = dto

    index = view_model.index(0, 0)
    info = view_model.data(index, Roles.INFO)

    for key in ("rel", "abs", "name", "is_video", "w", "h", "dur", "bytes"):
        assert key in info, f"Roles.INFO is missing required key '{key}'"

def test_load_query(view_model, mock_data_source):
    query = AssetQuery(album_path="test")
    mock_data_source.active_root.return_value = Path("/library/test")
    view_model.load_query(query)
    mock_data_source.load_selection.assert_called_once_with(
        Path("/library/test"),
        query=query,
        direct_assets=None,
        library_root=None,
    )


def test_load_selection_with_query(view_model, mock_data_source):
    query = AssetQuery(album_path="favorites")

    view_model.load_selection(Path("/library"), query=query)

    mock_data_source.load_selection.assert_called_once_with(
        Path("/library"),
        query=query,
        direct_assets=None,
        library_root=None,
    )


def test_load_selection_with_direct_assets(view_model, mock_data_source):
    assets = [MagicMock()]

    view_model.load_selection(Path("/library"), direct_assets=assets, library_root=Path("/library"))

    mock_data_source.load_selection.assert_called_once_with(
        Path("/library"),
        query=None,
        direct_assets=assets,
        library_root=Path("/library"),
    )

def test_reload_current_query(view_model, mock_data_source):
    view_model.reload_current_query()
    mock_data_source.reload_current_query.assert_called_once()


def test_reload_current_selection(view_model, mock_data_source):
    view_model.reload_current_selection()
    mock_data_source.reload_current_selection.assert_called_once()


def test_load_query_balances_model_reset_on_error(view_model, mock_data_source):
    mock_data_source.load_selection.side_effect = RuntimeError("boom")
    query = AssetQuery(album_path="test")
    mock_data_source.active_root.return_value = Path("/library/test")

    with (
        pytest.raises(RuntimeError, match="boom"),
        patch.object(view_model, "beginResetModel") as begin_reset,
        patch.object(view_model, "endResetModel") as end_reset,
    ):
        view_model.load_query(query)

    begin_reset.assert_called_once()
    end_reset.assert_called_once()

def test_row_count(view_model, mock_data_source):
    mock_data_source.count.return_value = 5
    assert view_model.rowCount() == 5

def test_data_display_role(view_model, mock_data_source):
    # Ensure rowCount > 0 so index is valid
    mock_data_source.count.return_value = 1

    dto = _make_dto(rel_path=Path("photo.jpg"))
    mock_data_source.asset_at.return_value = dto

    index = view_model.index(0, 0)
    result = view_model.data(index, Qt.DisplayRole)

    assert result == "photo.jpg"

def test_data_path_role(view_model, mock_data_source):
    mock_data_source.count.return_value = 1

    dto = _make_dto(abs_path=Path("/full/path/photo.jpg"))
    mock_data_source.asset_at.return_value = dto

    index = view_model.index(0, 0)
    result = view_model.data(index, Roles.ABS)

    # On linux/mac this is posix, win is nt.
    assert str(result).endswith("photo.jpg")

def test_data_thumbnail_role(view_model, mock_data_source, mock_thumb_service):
    mock_data_source.count.return_value = 1

    dto = _make_dto()
    mock_data_source.asset_at.return_value = dto

    # Mock pixmap return
    mock_pixmap = MagicMock()
    mock_thumb_service.get_thumbnail.return_value = mock_pixmap

    index = view_model.index(0, 0)
    result = view_model.data(index, Qt.DecorationRole)

    assert result == mock_pixmap
    mock_thumb_service.get_thumbnail.assert_called()

def test_get_qml_helper(view_model, mock_data_source):
    mock_data_source.count.return_value = 1

    dto = _make_dto(abs_path=Path("photo.jpg"))
    mock_data_source.asset_at.return_value = dto

    result = view_model.get(0)
    assert str(result) == "photo.jpg"

def test_invalid_index(view_model):
    result = view_model.data(QModelIndex(), Qt.DisplayRole)
    assert result is None

def test_current_role_without_loaded_asset(view_model, mock_data_source):
    mock_data_source.count.return_value = 1
    view_model.set_current_row(0)
    index = view_model.index(0, 0)
    assert view_model.data(index, Roles.IS_CURRENT) is True

def test_prioritize_rows_delegates_to_data_source(view_model, mock_data_source):
    view_model.prioritize_rows(10, 25)
    mock_data_source.prioritize_rows.assert_called_once_with(10, 25)


def test_rebind_repository_updates_data_source(view_model, mock_data_source):
    repo = MagicMock()
    root = Path("/library")

    view_model.rebind_repository(repo, root)

    mock_data_source.set_repository.assert_called_once_with(repo)
    mock_data_source.set_library_root.assert_called_once_with(root)


def test_handle_scan_chunk_delegates_to_data_source(view_model, mock_data_source):
    root = Path("/library")
    chunk = [{"rel": "foo.jpg"}]

    view_model.handle_scan_chunk(root, chunk)

    mock_data_source.handle_scan_chunk.assert_called_once_with(root, chunk)


def test_handle_scan_finished_delegates_to_data_source(view_model, mock_data_source):
    root = Path("/library")

    view_model.handle_scan_finished(root, True)

    mock_data_source.handle_scan_finished.assert_called_once_with(root, True)


def test_row_for_path_delegates_to_data_source(view_model, mock_data_source):
    path = Path("/library/photo.jpg")
    mock_data_source.row_for_path.return_value = 7

    assert view_model.row_for_path(path) == 7
    mock_data_source.row_for_path.assert_called_once_with(path)


def test_unchanged_count_skips_reset(view_model, mock_data_source):
    mock_data_source.count.return_value = 2
    view_model._last_snapshot = (2, b"sig")
    with (
        patch.object(view_model, "beginResetModel") as begin_reset,
        patch.object(view_model, "endResetModel") as end_reset,
        patch.object(view_model, "_snapshot_hash", return_value=b"sig"),
    ):
        view_model._on_source_changed()
    begin_reset.assert_not_called()
    end_reset.assert_not_called()


def test_changed_count_triggers_reset(view_model, mock_data_source):
    mock_data_source.count.return_value = 1
    view_model._last_snapshot = (0, b"sig")
    with (
        patch.object(view_model, "beginResetModel") as begin_reset,
        patch.object(view_model, "endResetModel") as end_reset,
        patch.object(view_model, "_snapshot_hash", return_value=b"new"),
    ):
        view_model._on_source_changed()
    begin_reset.assert_called_once()
    end_reset.assert_called_once()


def test_changed_paths_triggers_reset(view_model, mock_data_source):
    mock_data_source.count.return_value = 2
    view_model._last_snapshot = (2, b"old")
    with (
        patch.object(view_model, "beginResetModel") as begin_reset,
        patch.object(view_model, "endResetModel") as end_reset,
        patch.object(view_model, "_snapshot_hash", return_value=b"new"),
    ):
        view_model._on_source_changed()
    begin_reset.assert_called_once()
    end_reset.assert_called_once()


# ---------------------------------------------------------------------------
# Sidecar-aware duration badge tests
# ---------------------------------------------------------------------------

def test_size_role_returns_trimmed_duration_for_video(view_model, mock_data_source):
    """Roles.SIZE duration should reflect sidecar trim when a video has trim values."""
    mock_data_source.count.return_value = 1
    dto = _make_dto(
        abs_path=Path("/videos/clip.mp4"),
        media_type="video",
        duration=10.0,
    )
    mock_data_source.asset_at.return_value = dto

    with patch(
        "iPhoto.gui.viewmodels.asset_list_viewmodel._io_sidecar.load_adjustments",
        return_value={"Video_Trim_In_Sec": 2.0, "Video_Trim_Out_Sec": 7.0},
    ):
        index = view_model.index(0, 0)
        result = view_model.data(index, Roles.SIZE)

    assert result is not None
    assert result["duration"] == pytest.approx(5.0)  # 7.0 - 2.0


def test_size_role_returns_full_duration_for_video_without_sidecar(view_model, mock_data_source):
    """Roles.SIZE duration should equal the raw container duration when no sidecar trim exists."""
    mock_data_source.count.return_value = 1
    dto = _make_dto(
        abs_path=Path("/videos/clip.mp4"),
        media_type="video",
        duration=10.0,
    )
    mock_data_source.asset_at.return_value = dto

    with patch(
        "iPhoto.gui.viewmodels.asset_list_viewmodel._io_sidecar.load_adjustments",
        return_value={},
    ):
        index = view_model.index(0, 0)
        result = view_model.data(index, Roles.SIZE)

    assert result is not None
    assert result["duration"] == pytest.approx(10.0)


def test_size_role_serves_trimmed_duration_from_cache_without_repeated_sidecar_reads(
    view_model, mock_data_source
):
    """Sidecar is only read once per path; subsequent calls use the cache."""
    mock_data_source.count.return_value = 1
    dto = _make_dto(
        abs_path=Path("/videos/clip.mp4"),
        media_type="video",
        duration=10.0,
    )
    mock_data_source.asset_at.return_value = dto

    with patch(
        "iPhoto.gui.viewmodels.asset_list_viewmodel._io_sidecar.load_adjustments",
        return_value={"Video_Trim_In_Sec": 1.0, "Video_Trim_Out_Sec": 6.0},
    ) as mock_load:
        index = view_model.index(0, 0)
        view_model.data(index, Roles.SIZE)
        view_model.data(index, Roles.SIZE)

    assert mock_load.call_count == 1


def test_invalidate_thumbnail_clears_duration_cache_and_emits_size_role(
    view_model, mock_data_source
):
    """invalidate_thumbnail() must clear the cached effective duration and emit
    dataChanged with Roles.SIZE so the gallery badge re-reads the sidecar."""
    path = Path("/videos/clip.mp4")
    # Pre-populate the cache so we can verify it's cleared.
    view_model._duration_cache[path] = 8.0

    mock_data_source.count.return_value = 1
    mock_data_source.row_for_path = MagicMock(return_value=0)

    emitted_roles: list = []

    def on_data_changed(top_left, bottom_right, roles):
        emitted_roles.extend(roles)

    view_model.dataChanged.connect(on_data_changed)

    with patch.object(view_model._thumbnails, "invalidate"):
        view_model.invalidate_thumbnail(str(path))

    assert path not in view_model._duration_cache
    assert Roles.SIZE in emitted_roles

