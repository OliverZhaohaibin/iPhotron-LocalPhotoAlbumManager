"""Tests for the export engine."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtGui import QImage

from iPhoto.core.export import (
    export_asset,
    get_unique_destination,
    render_image,
    resolve_export_path,
    _parse_hhmmss_duration,
    probe_duration_seconds,
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


@patch("iPhoto.core.export.sidecar")
@patch("iPhoto.core.export.image_loader")
@patch("iPhoto.core.export.apply_adjustments")
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


@patch("iPhoto.core.export.render_video")
@patch("iPhoto.core.export.render_image")
@patch("iPhoto.core.export.shutil")
@patch("iPhoto.core.export.sidecar")
def test_export_asset(mock_sidecar, mock_shutil, mock_render, mock_render_video, tmp_path: Path) -> None:
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
    mock_ipo_missing = MagicMock()
    mock_ipo_missing.exists.return_value = False
    mock_sidecar.sidecar_path_for_asset.return_value = mock_ipo_missing
    assert export_asset(video, export_root, library_root)
    mock_shutil.copy2.assert_called()
    mock_render.assert_not_called()
    mock_render_video.assert_not_called()

    # Case B: Image + No Sidecar -> Copy
    mock_ipo_missing = MagicMock()
    mock_ipo_missing.exists.return_value = False
    mock_sidecar.sidecar_path_for_asset.return_value = mock_ipo_missing

    mock_shutil.reset_mock()
    assert export_asset(source, export_root, library_root)
    mock_shutil.copy2.assert_called()
    mock_render.assert_not_called()
    mock_render_video.assert_not_called()

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


@patch("iPhoto.core.export.render_video")
@patch("iPhoto.core.export.shutil")
@patch("iPhoto.core.export.probe_media")
@patch("iPhoto.core.export.sidecar")
def test_export_asset_renders_edited_video(
    mock_sidecar,
    mock_probe_media,
    mock_shutil,
    mock_render_video,
    tmp_path: Path,
) -> None:
    export_root = tmp_path / "exported"
    library_root = tmp_path
    album = tmp_path / "Album"
    album.mkdir()
    video = album / "edited.mov"
    video.touch()

    mock_ipo_exists = MagicMock()
    mock_ipo_exists.exists.return_value = True
    mock_sidecar.sidecar_path_for_asset.return_value = mock_ipo_exists
    mock_sidecar.load_adjustments.return_value = {"Video_Trim_In_Sec": 1.0}
    mock_sidecar.video_has_visible_edits.return_value = True
    mock_probe_media.return_value = {"format": {"duration": "12.0"}}
    mock_render_video.return_value = True

    assert export_asset(video, export_root, library_root)
    mock_render_video.assert_called_once()
    mock_shutil.copy2.assert_not_called()


class TestParseHhmmss:
    def test_valid_hhmmss(self):
        assert _parse_hhmmss_duration("00:01:23.456") == pytest.approx(83.456)

    def test_valid_zero_hours(self):
        assert _parse_hhmmss_duration("00:00:10.0") == pytest.approx(10.0)

    def test_valid_large_hours(self):
        assert _parse_hhmmss_duration("02:00:00.0") == pytest.approx(7200.0)

    def test_zero_duration_returns_none(self):
        assert _parse_hhmmss_duration("00:00:00.000") is None

    def test_minutes_out_of_range(self):
        assert _parse_hhmmss_duration("00:60:00.0") is None

    def test_seconds_out_of_range(self):
        assert _parse_hhmmss_duration("00:00:60.0") is None

    def test_negative_hours(self):
        assert _parse_hhmmss_duration("-1:00:00.0") is None

    def test_wrong_separator_count(self):
        assert _parse_hhmmss_duration("83.456") is None

    def test_non_numeric(self):
        assert _parse_hhmmss_duration("hh:mm:ss") is None

    def test_none_input(self):
        assert _parse_hhmmss_duration(None) is None

    def test_non_string_input(self):
        assert _parse_hhmmss_duration(123) is None


class TestProbeDurationSeconds:
    def test_returns_format_duration(self):
        meta = {"format": {"duration": "12.5"}, "streams": []}
        assert probe_duration_seconds(meta) == pytest.approx(12.5)

    def test_format_duration_takes_priority_over_stream(self):
        meta = {
            "format": {"duration": "12.5"},
            "streams": [{"codec_type": "video", "duration": "99.0"}],
        }
        assert probe_duration_seconds(meta) == pytest.approx(12.5)

    def test_falls_back_to_format_tags_hhmmss(self):
        meta = {
            "format": {"tags": {"DURATION": "00:00:08.500000000"}},
            "streams": [],
        }
        assert probe_duration_seconds(meta) == pytest.approx(8.5)

    def test_falls_back_to_stream_duration_in_seconds(self):
        meta = {
            "format": {},
            "streams": [{"codec_type": "video", "duration": "30.0"}],
        }
        # stream["duration"] is already seconds — must NOT be multiplied by time_base
        assert probe_duration_seconds(meta) == pytest.approx(30.0)

    def test_stream_duration_not_multiplied_by_time_base(self):
        meta = {
            "format": {},
            "streams": [
                {"codec_type": "video", "duration": "10.0", "time_base": "1/90000"}
            ],
        }
        # If incorrectly multiplied: 10.0 * (1/90000) ≈ 0.000111 — must return 10.0
        assert probe_duration_seconds(meta) == pytest.approx(10.0)

    def test_falls_back_to_duration_ts_times_time_base(self):
        meta = {
            "format": {},
            "streams": [
                {"codec_type": "video", "duration_ts": "900000", "time_base": "1/90000"}
            ],
        }
        assert probe_duration_seconds(meta) == pytest.approx(10.0)

    def test_skips_audio_streams(self):
        meta = {
            "format": {},
            "streams": [
                {"codec_type": "audio", "duration": "5.0"},
                {"codec_type": "video", "duration": "8.0"},
            ],
        }
        assert probe_duration_seconds(meta) == pytest.approx(8.0)

    def test_returns_none_for_missing_duration(self):
        assert probe_duration_seconds({}) is None

    def test_returns_none_for_non_dict(self):
        assert probe_duration_seconds(None) is None
