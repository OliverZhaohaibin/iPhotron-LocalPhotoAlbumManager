from pathlib import Path
from unittest.mock import MagicMock

from src.iPhoto.application.dtos import (
    OpenAlbumResponse,
    ScanAlbumResponse,
    PairLivePhotosResponse,
)
from src.iPhoto.application.services.album_service import AlbumService
from src.iPhoto.application.services.asset_service import AssetService
from src.iPhoto.domain.models import Asset, MediaType


def test_album_service_delegates_to_use_cases():
    open_uc = MagicMock()
    scan_uc = MagicMock()
    pair_uc = MagicMock()

    open_uc.execute.return_value = OpenAlbumResponse(
        album_id="1",
        title="Test",
        asset_count=3,
    )
    scan_uc.execute.return_value = ScanAlbumResponse(
        added_count=1,
        updated_count=2,
        deleted_count=0,
    )
    pair_uc.execute.return_value = PairLivePhotosResponse(
        paired_count=2,
    )

    service = AlbumService(open_uc, scan_uc, pair_uc)

    open_response = service.open_album(Path("/tmp/album"))
    scan_response = service.scan_album("1", force_rescan=True)
    pair_response = service.pair_live_photos("1")

    open_uc.execute.assert_called_once()
    scan_uc.execute.assert_called_once()
    pair_uc.execute.assert_called_once()

    assert open_response.asset_count == 3
    assert scan_response.added_count == 1
    assert pair_response.paired_count == 2


def test_asset_service_toggle_favorite_updates_repo():
    asset = Asset(
        id="asset-1",
        album_id="album-1",
        path=Path("img.jpg"),
        media_type=MediaType.IMAGE,
        size_bytes=123,
    )
    repo = MagicMock()
    repo.get.return_value = asset

    service = AssetService(repo)

    result = service.toggle_favorite("asset-1")

    assert result is True
    assert asset.is_favorite is True
    repo.save.assert_called_once_with(asset)
