"""Tests for Phase 2 use cases and ManifestService."""

import pytest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime

from iPhoto.domain.models import Album, Asset, MediaType
from iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from iPhoto.events.bus import EventBus
from iPhoto.application.use_cases.create_album import CreateAlbumUseCase, CreateAlbumRequest
from iPhoto.application.use_cases.delete_album import DeleteAlbumUseCase, DeleteAlbumRequest
from iPhoto.application.use_cases.import_assets import ImportAssetsUseCase, ImportAssetsRequest
from iPhoto.application.use_cases.move_assets import MoveAssetsUseCase, MoveAssetsRequest
from iPhoto.application.use_cases.update_metadata import UpdateMetadataUseCase, UpdateMetadataRequest
from iPhoto.application.use_cases.generate_thumbnail import GenerateThumbnailUseCase, GenerateThumbnailRequest
from iPhoto.application.interfaces import IThumbnailGenerator
from iPhoto.domain.services.manifest_service import ManifestService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_album(album_id="a1", path=None, title="Test Album"):
    return Album(
        id=album_id,
        path=path or Path("/tmp/albums/test"),
        title=title,
        created_at=datetime.now(),
    )


def _make_asset(asset_id="x1", album_id="a1", path=None):
    return Asset(
        id=asset_id,
        album_id=album_id,
        path=path or Path("photo.jpg"),
        media_type=MediaType.IMAGE,
        size_bytes=1024,
    )


# ===========================================================================
# CreateAlbumUseCase
# ===========================================================================

class TestCreateAlbumUseCase:
    def test_create_album_success(self):
        album_repo = Mock(spec=IAlbumRepository)
        album_repo.get_by_path.return_value = None
        event_bus = Mock(spec=EventBus)

        uc = CreateAlbumUseCase(album_repo, event_bus)
        response = uc.execute(CreateAlbumRequest(path=Path("/tmp/test"), title="My Album"))

        assert response.success is True
        assert response.title == "My Album"
        album_repo.save.assert_called_once()
        event_bus.publish.assert_called_once()

    def test_create_album_already_exists(self):
        album_repo = Mock(spec=IAlbumRepository)
        album_repo.get_by_path.return_value = _make_album()
        event_bus = Mock(spec=EventBus)

        uc = CreateAlbumUseCase(album_repo, event_bus)
        response = uc.execute(CreateAlbumRequest(path=Path("/tmp/test"), title="Dup"))

        assert response.success is False
        assert "already exists" in response.error
        album_repo.save.assert_not_called()


# ===========================================================================
# DeleteAlbumUseCase
# ===========================================================================

class TestDeleteAlbumUseCase:
    def test_delete_album_success(self):
        album_repo = Mock(spec=IAlbumRepository)
        album_repo.get.return_value = _make_album()
        event_bus = Mock(spec=EventBus)

        uc = DeleteAlbumUseCase(album_repo, event_bus)
        response = uc.execute(DeleteAlbumRequest(album_id="a1"))

        assert response.success is True
        album_repo.delete.assert_called_once_with("a1")

    def test_delete_album_not_found(self):
        album_repo = Mock(spec=IAlbumRepository)
        album_repo.get.return_value = None
        event_bus = Mock(spec=EventBus)

        uc = DeleteAlbumUseCase(album_repo, event_bus)
        response = uc.execute(DeleteAlbumRequest(album_id="missing"))

        assert response.success is False
        assert "not found" in response.error.lower()


# ===========================================================================
# ImportAssetsUseCase
# ===========================================================================

class TestImportAssetsUseCase:
    @patch("iPhoto.application.use_cases.import_assets.shutil")
    def test_import_assets_success(self, mock_shutil):
        asset_repo = Mock(spec=IAssetRepository)
        asset_repo.get_by_path.return_value = None
        album_repo = Mock(spec=IAlbumRepository)
        album_repo.get.return_value = _make_album()
        event_bus = Mock(spec=EventBus)

        uc = ImportAssetsUseCase(asset_repo, album_repo, event_bus)
        response = uc.execute(ImportAssetsRequest(
            source_paths=[Path("/photos/a.jpg"), Path("/photos/b.jpg")],
            target_album_id="a1",
            copy_files=True,
        ))

        assert response.success is True
        assert response.imported_count == 2
        assert mock_shutil.copy2.call_count == 2

    def test_import_assets_album_not_found(self):
        asset_repo = Mock(spec=IAssetRepository)
        album_repo = Mock(spec=IAlbumRepository)
        album_repo.get.return_value = None
        event_bus = Mock(spec=EventBus)

        uc = ImportAssetsUseCase(asset_repo, album_repo, event_bus)
        response = uc.execute(ImportAssetsRequest(
            source_paths=[Path("/photos/a.jpg")],
            target_album_id="missing",
        ))

        assert response.success is False
        assert "not found" in response.error.lower()


# ===========================================================================
# MoveAssetsUseCase
# ===========================================================================

class TestMoveAssetsUseCase:
    @patch("iPhoto.application.use_cases.move_assets.shutil")
    def test_move_assets_success(self, mock_shutil):
        asset = _make_asset()
        source_album = _make_album(album_id="a1", path=Path("/tmp/src"))
        target_album = _make_album(album_id="a2", path=Path("/tmp/dst"))

        asset_repo = Mock(spec=IAssetRepository)
        asset_repo.get.return_value = asset
        album_repo = Mock(spec=IAlbumRepository)
        album_repo.get.side_effect = lambda aid: {
            "a1": source_album,
            "a2": target_album,
        }.get(aid)
        event_bus = Mock(spec=EventBus)

        # Mock Path.exists to return True so shutil.move is called
        with patch.object(Path, "exists", return_value=True):
            uc = MoveAssetsUseCase(asset_repo, album_repo, event_bus)
            response = uc.execute(MoveAssetsRequest(
                asset_ids=["x1"],
                target_album_id="a2",
            ))

        assert response.success is True
        assert response.moved_count == 1
        asset_repo.save.assert_called_once()

    def test_move_assets_target_not_found(self):
        asset_repo = Mock(spec=IAssetRepository)
        album_repo = Mock(spec=IAlbumRepository)
        album_repo.get.return_value = None
        event_bus = Mock(spec=EventBus)

        uc = MoveAssetsUseCase(asset_repo, album_repo, event_bus)
        response = uc.execute(MoveAssetsRequest(
            asset_ids=["x1"],
            target_album_id="missing",
        ))

        assert response.success is False
        assert "not found" in response.error.lower()


# ===========================================================================
# UpdateMetadataUseCase
# ===========================================================================

class TestUpdateMetadataUseCase:
    def test_update_metadata_success(self):
        asset = _make_asset()
        asset_repo = Mock(spec=IAssetRepository)
        asset_repo.get.return_value = asset

        uc = UpdateMetadataUseCase(asset_repo)
        response = uc.execute(UpdateMetadataRequest(
            asset_id="x1",
            metadata={"rating": 5},
        ))

        assert response.success is True
        assert asset.metadata["rating"] == 5
        asset_repo.save.assert_called_once()

    def test_update_metadata_asset_not_found(self):
        asset_repo = Mock(spec=IAssetRepository)
        asset_repo.get.return_value = None

        uc = UpdateMetadataUseCase(asset_repo)
        response = uc.execute(UpdateMetadataRequest(
            asset_id="missing",
            metadata={"rating": 5},
        ))

        assert response.success is False
        assert "not found" in response.error.lower()


# ===========================================================================
# GenerateThumbnailUseCase
# ===========================================================================

class TestGenerateThumbnailUseCase:
    def test_generate_thumbnail_success(self):
        asset = _make_asset()
        album = _make_album()
        asset_repo = Mock(spec=IAssetRepository)
        asset_repo.get.return_value = asset
        album_repo = Mock(spec=IAlbumRepository)
        album_repo.get.return_value = album
        thumb_gen = Mock(spec=IThumbnailGenerator)
        thumb_gen.generate_micro_thumbnail.return_value = "base64data"
        event_bus = Mock(spec=EventBus)

        uc = GenerateThumbnailUseCase(asset_repo, album_repo, thumb_gen, event_bus)
        response = uc.execute(GenerateThumbnailRequest(asset_id="x1"))

        assert response.success is True
        assert response.thumbnail_data == "base64data"
        event_bus.publish.assert_called_once()

    def test_generate_thumbnail_asset_not_found(self):
        asset_repo = Mock(spec=IAssetRepository)
        asset_repo.get.return_value = None
        album_repo = Mock(spec=IAlbumRepository)
        thumb_gen = Mock(spec=IThumbnailGenerator)
        event_bus = Mock(spec=EventBus)

        uc = GenerateThumbnailUseCase(asset_repo, album_repo, thumb_gen, event_bus)
        response = uc.execute(GenerateThumbnailRequest(asset_id="missing"))

        assert response.success is False
        assert "not found" in response.error.lower()


# ===========================================================================
# ManifestService
# ===========================================================================

class TestManifestService:
    def test_read_manifest(self, tmp_path):
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text('{"title": "My Album"}', encoding="utf-8")

        svc = ManifestService()
        data = svc.read_manifest(tmp_path)
        assert data == {"title": "My Album"}

    def test_write_manifest(self, tmp_path):
        svc = ManifestService()
        svc.write_manifest(tmp_path, {"title": "Written"})

        manifest_path = tmp_path / "manifest.json"
        assert manifest_path.exists()
        import json
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["title"] == "Written"

    def test_read_manifest_not_found(self, tmp_path):
        from iPhoto.errors import AlbumNotFoundError

        svc = ManifestService()
        with pytest.raises(AlbumNotFoundError):
            svc.read_manifest(tmp_path)
