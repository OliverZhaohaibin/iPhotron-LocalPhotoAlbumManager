"""Test the to_dto converter directly (bypasses GUI import chain)."""

from pathlib import Path
from iPhoto.domain.models import Asset, MediaType
from iPhoto.gui.viewmodels.asset_dto_converter import to_dto


def test_to_dto_none_width_height_does_not_raise():
    """Regression: to_dto must not TypeError when width/height are None."""
    asset = Asset(
        id="1", album_id="a", path=Path("image.jpg"),
        media_type=MediaType.IMAGE, size_bytes=0,
        width=None, height=None,
    )
    dto = to_dto(asset, library_root=None)
    assert dto.width == 0
    assert dto.height == 0
    assert dto.is_pano is False


def test_to_dto_none_width_only():
    """to_dto handles width=None with valid height."""
    asset = Asset(
        id="2", album_id="a", path=Path("photo.jpg"),
        media_type=MediaType.IMAGE, size_bytes=0,
        width=None, height=600,
    )
    dto = to_dto(asset, library_root=None)
    assert dto.height == 600
    assert dto.is_pano is False


def test_to_dto_none_height_only():
    """to_dto handles height=None with valid width."""
    asset = Asset(
        id="3", album_id="a", path=Path("photo.jpg"),
        media_type=MediaType.IMAGE, size_bytes=0,
        width=800, height=None,
    )
    dto = to_dto(asset, library_root=None)
    assert dto.width == 800
    assert dto.is_pano is False


def test_to_dto_valid_width_height():
    """to_dto works normally with valid width/height."""
    asset = Asset(
        id="4", album_id="a", path=Path("photo.jpg"),
        media_type=MediaType.IMAGE, size_bytes=0,
        width=800, height=600,
    )
    dto = to_dto(asset, library_root=None)
    assert dto.width == 800
    assert dto.height == 600
