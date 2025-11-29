"""Tests for the export engine."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtGui import QImage

from iPhotos.src.iPhoto.core.export import (
    export_asset,
    get_unique_destination,
    render_image,
    resolve_export_path,
)


def test_get_unique_destination(tmp_path: Path) -> None:
    # Setup
    dest = tmp_path / "test.txt"
    dest.touch()

    # Test 1: Conflict
    unique = get_unique_destination(dest)
    assert unique.name == "test (1).txt"
    assert unique.parent == tmp_path

    # Test 2: Double Conflict
    unique.touch()
    unique2 = get_unique_destination(dest)
    assert unique2.name == "test (2).txt"

    # Test 3: No Conflict
    other = tmp_path / "other.txt"
    assert get_unique_destination(other) == other


def test_resolve_export_path() -> None:
    library_root = Path("/lib")
    export_root = Path("/lib/exported")

    # Case 1: Nested
    source = Path("/lib/AlbumA/SubAlbum/image.jpg")
    resolved = resolve_export_path(source, export_root, library_root)
    assert resolved == Path("/lib/exported/AlbumA/SubAlbum/image.jpg")

    # Case 2: Root Album
    source = Path("/lib/AlbumA/image.jpg")
    resolved = resolve_export_path(source, export_root, library_root)
    assert resolved == Path("/lib/exported/AlbumA/image.jpg")

    # Case 3: Outside (Fallback)
    source = Path("/other/ExternalAlbum/image.jpg")
    resolved = resolve_export_path(source, export_root, library_root)
    # relative_to raises ValueError. Fallback uses parent name.
    assert resolved == Path("/lib/exported/ExternalAlbum/image.jpg")


@patch("iPhotos.src.iPhoto.core.export.sidecar")
@patch("iPhotos.src.iPhoto.core.export.image_loader")
@patch("iPhotos.src.iPhoto.core.export.apply_adjustments")
def test_render_image(mock_apply, mock_loader, mock_sidecar) -> None:
    path = Path("/path/to/image.jpg")

    # Setup mocks
    mock_sidecar.load_adjustments.return_value = {"Crop_CX": 0.5}
    mock_sidecar.resolve_render_adjustments.return_value = {}

    mock_image = MagicMock(spec=QImage)
    mock_image.isNull.return_value = False
    mock_image.width.return_value = 100
    mock_image.height.return_value = 100
    mock_loader.load_qimage.return_value = mock_image

    mock_apply.return_value = mock_image

    # Test
    result = render_image(path)

    assert result is not None
    mock_sidecar.load_adjustments.assert_called_with(path)
    mock_loader.load_qimage.assert_called_with(path)
    mock_apply.assert_called()


@patch("iPhotos.src.iPhoto.core.export.render_image")
@patch("iPhotos.src.iPhoto.core.export.shutil")
@patch("iPhotos.src.iPhoto.core.export.sidecar")
def test_export_asset(mock_sidecar, mock_shutil, mock_render, tmp_path: Path) -> None:
    export_root = tmp_path / "exported"
    library_root = tmp_path

    # Create source
    album = tmp_path / "Album"
    album.mkdir()
    source = album / "img.jpg"
    source.touch()

    # Case A: Video -> Copy
    video = album / "vid.mov"
    video.touch()
    assert export_asset(video, export_root, library_root)
    mock_shutil.copy2.assert_called()
    mock_render.assert_not_called()

    # Case B: Image + No Sidecar -> Copy
    mock_ipo_missing = MagicMock()
    mock_ipo_missing.exists.return_value = False
    mock_sidecar.sidecar_path_for_asset.return_value = mock_ipo_missing

    mock_shutil.reset_mock()
    assert export_asset(source, export_root, library_root)
    mock_shutil.copy2.assert_called()
    mock_render.assert_not_called()

    # Case C: Image + Sidecar -> Render
    mock_ipo_exists = MagicMock()
    mock_ipo_exists.exists.return_value = True
    mock_sidecar.sidecar_path_for_asset.return_value = mock_ipo_exists

    # Mock render return
    mock_qimage = MagicMock(spec=QImage)
    mock_render.return_value = mock_qimage

    assert export_asset(source, export_root, library_root)
    mock_render.assert_called_with(source)
    mock_qimage.save.assert_called()
