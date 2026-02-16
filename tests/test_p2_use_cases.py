"""Tests for P2 Use Cases: ManageTrash, AggregateGeoData, WatchFilesystem, ExportAssets, ApplyEdit."""
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iPhoto.application.use_cases.manage_trash import (
    ManageTrashUseCase, ManageTrashRequest, ManageTrashResponse,
)
from iPhoto.application.use_cases.aggregate_geo_data import (
    AggregateGeoDataUseCase, AggregateGeoDataRequest, AggregateGeoDataResponse,
)
from iPhoto.application.use_cases.watch_filesystem import (
    WatchFilesystemUseCase, WatchFilesystemRequest, WatchFilesystemResponse,
)
from iPhoto.application.use_cases.export_assets import (
    ExportAssetsUseCase, ExportAssetsRequest, ExportAssetsResponse,
)
from iPhoto.application.use_cases.apply_edit import (
    ApplyEditUseCase, ApplyEditRequest, ApplyEditResponse,
)
from iPhoto.domain.models.core import Album, Asset, MediaType
from iPhoto.events.bus import EventBus


# -- Helpers / Fixtures --

def _make_album(id="album-1", path=None):
    return Album(id=id, path=path or Path("/albums/test"), title="Test Album")


def _make_asset(id="asset-1", album_id="album-1", path=Path("photo.jpg"), metadata=None):
    return Asset(
        id=id,
        album_id=album_id,
        path=path,
        media_type=MediaType.IMAGE,
        size_bytes=1024,
        metadata=metadata or {},
    )


def _mock_repos():
    asset_repo = MagicMock()
    album_repo = MagicMock()
    return asset_repo, album_repo


# ============= ManageTrashUseCase Tests =============

class TestManageTrashUseCase:
    def test_trash_assets_moves_file(self, tmp_path):
        asset_repo, album_repo = _mock_repos()
        event_bus = EventBus()
        trash_dir = tmp_path / "trash"

        album = _make_album(path=tmp_path)
        album_repo.get.return_value = album

        # Create a real file
        photo = tmp_path / "photo.jpg"
        photo.write_bytes(b"fake jpg data")

        asset = _make_asset(path=Path("photo.jpg"))
        asset_repo.get.return_value = asset

        uc = ManageTrashUseCase(asset_repo, album_repo, event_bus, trash_dir=trash_dir)
        resp = uc.execute(ManageTrashRequest(action="trash", asset_ids=["asset-1"]))

        assert resp.affected_count == 1
        assert "asset-1" in resp.trashed_ids
        assert not photo.exists()
        assert (trash_dir / "photo.jpg").exists()

    def test_trash_unknown_action_returns_error(self):
        asset_repo, album_repo = _mock_repos()
        event_bus = EventBus()
        uc = ManageTrashUseCase(asset_repo, album_repo, event_bus)
        resp = uc.execute(ManageTrashRequest(action="invalid"))
        assert resp.success is False

    def test_trash_missing_asset_skipped(self, tmp_path):
        asset_repo, album_repo = _mock_repos()
        event_bus = EventBus()
        asset_repo.get.return_value = None

        uc = ManageTrashUseCase(asset_repo, album_repo, event_bus)
        resp = uc.execute(ManageTrashRequest(action="trash", asset_ids=["missing"]))
        assert resp.affected_count == 0
        assert resp.trashed_ids == []

    def test_trash_nonexistent_file_skipped(self, tmp_path):
        asset_repo, album_repo = _mock_repos()
        event_bus = EventBus()

        album = _make_album(path=tmp_path)
        album_repo.get.return_value = album
        asset = _make_asset(path=Path("does_not_exist.jpg"))
        asset_repo.get.return_value = asset

        uc = ManageTrashUseCase(asset_repo, album_repo, event_bus)
        resp = uc.execute(ManageTrashRequest(action="trash", asset_ids=["asset-1"]))
        assert resp.affected_count == 0


# ============= AggregateGeoDataUseCase Tests =============

class TestAggregateGeoDataUseCase:
    def test_aggregates_geotagged_assets(self):
        asset_repo = MagicMock()
        asset_repo.get_by_album.return_value = [
            _make_asset(id="a1", metadata={"latitude": 37.7749, "longitude": -122.4194, "location_name": "San Francisco"}),
            _make_asset(id="a2", metadata={"latitude": 40.7128, "longitude": -74.0060}),
            _make_asset(id="a3", metadata={}),  # no GPS
        ]
        uc = AggregateGeoDataUseCase(asset_repo)
        resp = uc.execute(AggregateGeoDataRequest(album_id="album-1"))
        assert resp.total_count == 2
        assert len(resp.geotagged_assets) == 2
        assert resp.geotagged_assets[0].location_name == "San Francisco"

    def test_empty_album_returns_empty(self):
        asset_repo = MagicMock()
        asset_repo.get_by_album.return_value = []
        uc = AggregateGeoDataUseCase(asset_repo)
        resp = uc.execute(AggregateGeoDataRequest(album_id="album-1"))
        assert resp.total_count == 0

    def test_assets_without_gps_excluded(self):
        asset_repo = MagicMock()
        asset_repo.get_by_album.return_value = [
            _make_asset(id="a1", metadata={"some_key": "val"}),
            _make_asset(id="a2", metadata={}),
        ]
        uc = AggregateGeoDataUseCase(asset_repo)
        resp = uc.execute(AggregateGeoDataRequest(album_id="album-1"))
        assert resp.total_count == 0
        assert resp.geotagged_assets == []


# ============= WatchFilesystemUseCase Tests =============

class TestWatchFilesystemUseCase:
    def test_start_watching_adds_paths(self, tmp_path):
        event_bus = EventBus()
        uc = WatchFilesystemUseCase(event_bus)
        resp = uc.execute(WatchFilesystemRequest(
            action="start",
            watch_paths=[str(tmp_path)],
        ))
        assert resp.is_watching is True
        assert str(tmp_path) in resp.watched_paths

    def test_stop_watching_removes_paths(self, tmp_path):
        event_bus = EventBus()
        uc = WatchFilesystemUseCase(event_bus)
        uc.execute(WatchFilesystemRequest(action="start", watch_paths=[str(tmp_path)]))
        resp = uc.execute(WatchFilesystemRequest(action="stop", watch_paths=[str(tmp_path)]))
        assert resp.is_watching is False
        assert str(tmp_path) not in resp.watched_paths

    def test_pause_resume(self, tmp_path):
        event_bus = EventBus()
        uc = WatchFilesystemUseCase(event_bus)
        uc.execute(WatchFilesystemRequest(action="start", watch_paths=[str(tmp_path)]))
        resp = uc.execute(WatchFilesystemRequest(action="pause"))
        assert resp.is_watching is False
        resp = uc.execute(WatchFilesystemRequest(action="resume"))
        assert resp.is_watching is True


# ============= ExportAssetsUseCase Tests =============

class TestExportAssetsUseCase:
    def test_export_copies_files(self, tmp_path):
        asset_repo, album_repo = _mock_repos()
        event_bus = EventBus()

        album_dir = tmp_path / "album"
        album_dir.mkdir()
        photo = album_dir / "photo.jpg"
        photo.write_bytes(b"image data")

        album = _make_album(path=album_dir)
        album_repo.get.return_value = album
        asset = _make_asset(path=Path("photo.jpg"))
        asset_repo.get.return_value = asset

        export_dir = tmp_path / "export"
        uc = ExportAssetsUseCase(asset_repo, album_repo, event_bus)
        resp = uc.execute(ExportAssetsRequest(
            asset_ids=["asset-1"],
            export_dir=str(export_dir),
        ))
        assert resp.exported_count == 1
        assert resp.failed_count == 0
        assert (export_dir / "photo.jpg").exists()

    def test_export_missing_asset_fails(self, tmp_path):
        asset_repo, album_repo = _mock_repos()
        event_bus = EventBus()
        asset_repo.get.return_value = None

        uc = ExportAssetsUseCase(asset_repo, album_repo, event_bus)
        resp = uc.execute(ExportAssetsRequest(
            asset_ids=["missing"],
            export_dir=str(tmp_path / "export"),
        ))
        assert resp.failed_count == 1

    def test_export_creates_target_dir(self, tmp_path):
        asset_repo, album_repo = _mock_repos()
        event_bus = EventBus()

        album_dir = tmp_path / "album"
        album_dir.mkdir()
        photo = album_dir / "pic.jpg"
        photo.write_bytes(b"data")

        album = _make_album(path=album_dir)
        album_repo.get.return_value = album
        asset = _make_asset(path=Path("pic.jpg"))
        asset_repo.get.return_value = asset

        export_dir = tmp_path / "new_dir" / "nested"
        assert not export_dir.exists()

        uc = ExportAssetsUseCase(asset_repo, album_repo, event_bus)
        resp = uc.execute(ExportAssetsRequest(
            asset_ids=["asset-1"],
            export_dir=str(export_dir),
        ))
        assert resp.exported_count == 1
        assert export_dir.is_dir()


# ============= ApplyEditUseCase Tests =============

class TestApplyEditUseCase:
    def test_apply_edit_succeeds(self):
        asset_repo, album_repo = _mock_repos()
        event_bus = EventBus()
        asset = _make_asset()
        album = _make_album()
        asset_repo.get.return_value = asset
        album_repo.get.return_value = album

        save_fn = MagicMock()
        uc = ApplyEditUseCase(asset_repo, album_repo, event_bus, save_adjustments_fn=save_fn)
        resp = uc.execute(ApplyEditRequest(
            asset_id="asset-1",
            adjustments={"brightness": 0.5, "contrast": 1.2},
        ))
        assert resp.success is True
        assert "brightness" in resp.applied_adjustments
        save_fn.assert_called_once()

    def test_apply_edit_no_adjustments_returns_error(self):
        asset_repo, album_repo = _mock_repos()
        event_bus = EventBus()
        asset_repo.get.return_value = _make_asset()
        album_repo.get.return_value = _make_album()

        uc = ApplyEditUseCase(asset_repo, album_repo, event_bus)
        resp = uc.execute(ApplyEditRequest(asset_id="asset-1", adjustments={}))
        assert resp.success is False

    def test_apply_edit_missing_asset_returns_error(self):
        asset_repo, album_repo = _mock_repos()
        event_bus = EventBus()
        asset_repo.get.return_value = None

        uc = ApplyEditUseCase(asset_repo, album_repo, event_bus)
        resp = uc.execute(ApplyEditRequest(asset_id="nope", adjustments={"x": 1}))
        assert resp.success is False
