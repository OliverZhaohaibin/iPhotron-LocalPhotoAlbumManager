import pytest
from unittest.mock import Mock
from pathlib import Path
from src.iPhoto.domain.models import Asset, MediaType
from src.iPhoto.domain.models.query import AssetQuery
from src.iPhoto.gui.viewmodels.asset_data_source import AssetDataSource

def test_to_dto_converts_media_type_correctly():
    # Arrange
    repo = Mock()
    source = AssetDataSource(repo)

    asset_video = Asset(
        id="1", album_id="a", path=Path("video.mp4"),
        media_type=MediaType.VIDEO, size_bytes=0
    )
    asset_image = Asset(
        id="2", album_id="a", path=Path("image.jpg"),
        media_type=MediaType.IMAGE, size_bytes=0
    )

    # Act
    dto_video = source._to_dto(asset_video)
    dto_image = source._to_dto(asset_image)

    # Assert
    assert dto_video.media_type == "video"
    assert dto_video.is_video is True

    assert dto_image.media_type == "image" or dto_image.media_type == "photo"
    assert dto_image.is_image is True

def test_to_dto_handles_int_media_type():
    # Arrange
    repo = Mock()
    source = AssetDataSource(repo)

    # Simulate an asset coming from repository with legacy int/string
    # Asset dataclass expects MediaType enum, but in python we can pass anything
    asset_video_legacy = Asset(
        id="1", album_id="a", path=Path("video.mp4"),
        media_type="2", size_bytes=0
    )

    dto = source._to_dto(asset_video_legacy)
    assert dto.media_type == "video"
    assert dto.is_video is True

    asset_video_str = Asset(
        id="1", album_id="a", path=Path("video.mp4"),
        media_type="MediaType.VIDEO", size_bytes=0
    )
    dto2 = source._to_dto(asset_video_str)
    assert dto2.media_type == "video"

def test_update_favorite_status():
    repo = Mock()
    # Mock find_by_query to return a list
    repo.find_by_query.return_value = [
        Asset(id="1", album_id="a", path=Path("p.jpg"), media_type=MediaType.IMAGE, size_bytes=0, is_favorite=False)
    ]
    source = AssetDataSource(repo)
    source.load(AssetQuery())

    assert source.asset_at(0).is_favorite is False

    source.update_favorite_status(0, True)
    assert source.asset_at(0).is_favorite is True
