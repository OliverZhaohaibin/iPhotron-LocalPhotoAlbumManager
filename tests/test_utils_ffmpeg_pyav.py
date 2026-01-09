
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from PIL import Image

from src.iPhoto.utils import ffmpeg

def test_extract_frame_with_pyav_returns_none_when_av_missing(monkeypatch):
    """Ensure it returns None if av module is not present."""
    monkeypatch.setattr(ffmpeg, "av", None)
    result = ffmpeg.extract_frame_with_pyav(Path("video.mp4"))
    assert result is None

@patch("src.iPhoto.utils.ffmpeg.av")
def test_extract_frame_with_pyav_opens_container(mock_av, tmp_path):
    """Test that it opens the container and seeks."""
    video_path = tmp_path / "video.mp4"

    # Mock container and stream
    mock_container = MagicMock()
    mock_av.open.return_value.__enter__.return_value = mock_container

    mock_stream = MagicMock()
    mock_stream.time_base = 1/30
    mock_container.streams.video = [mock_stream]

    # Mock frame
    mock_frame = MagicMock()
    mock_frame.pts = 30 # Matching target
    mock_image = Image.new("RGB", (100, 100))
    mock_frame.to_image.return_value = mock_image

    mock_container.decode.return_value = [mock_frame]

    # Call
    result = ffmpeg.extract_frame_with_pyav(video_path, at=1.0)

    # Assertions
    mock_av.open.assert_called_with(str(video_path))
    assert mock_container.seek.called
    assert result == mock_image

@patch("src.iPhoto.utils.ffmpeg.av")
def test_extract_frame_with_pyav_no_seek(mock_av, tmp_path):
    """Test extraction without seeking (first frame)."""
    video_path = tmp_path / "video.mp4"

    mock_container = MagicMock()
    mock_av.open.return_value.__enter__.return_value = mock_container

    mock_stream = MagicMock()
    mock_container.streams.video = [mock_stream]

    # Frame at 0
    mock_frame = MagicMock()
    mock_frame.pts = 0
    mock_image = Image.new("RGB", (100, 100))
    mock_frame.to_image.return_value = mock_image

    mock_container.decode.return_value = [mock_frame]

    # Call with at=None
    result = ffmpeg.extract_frame_with_pyav(video_path, at=None)

    assert result == mock_image
    assert not mock_container.seek.called

@patch("src.iPhoto.utils.ffmpeg.av")
def test_extract_frame_with_pyav_handles_scaling(mock_av, tmp_path):
    """Test scaling logic."""
    video_path = tmp_path / "video.mp4"

    mock_container = MagicMock()
    mock_av.open.return_value.__enter__.return_value = mock_container

    mock_stream = MagicMock()
    mock_container.streams.video = [mock_stream]

    # Original 1920x1080
    mock_frame = MagicMock()
    mock_frame.pts = 0
    mock_image = Image.new("RGB", (1920, 1080))
    mock_frame.to_image.return_value = mock_image

    mock_container.decode.return_value = [mock_frame]

    # Request scale to 320x240
    result = ffmpeg.extract_frame_with_pyav(video_path, scale=(320, 240))

    assert result is not None
    # 1920/320 = 6, 1080/240 = 4.5. Ratio is 4.5.
    # New width = 1920 / 6 -> 320? No logic is min(max_w/w, max_h/h)
    # Ratio = min(320/1920, 240/1080) = min(0.166, 0.222) = 0.166
    # New width = 1920 * 0.166 = 320
    # New height = 1080 * 0.166 = 180
    assert result.size == (320, 180)

@patch("src.iPhoto.utils.ffmpeg.av")
def test_extract_frame_with_pyav_handles_scaling_odd_dimensions(mock_av, tmp_path):
    """Test scaling logic ensures even dimensions matching ffmpeg logic."""
    video_path = tmp_path / "video.mp4"

    mock_container = MagicMock()
    mock_av.open.return_value.__enter__.return_value = mock_container
    mock_container.streams.video = [MagicMock()]

    # 100x100 source
    mock_frame = MagicMock()
    mock_frame.pts = 0
    mock_image = Image.new("RGB", (100, 100))
    mock_frame.to_image.return_value = mock_image

    mock_container.decode.return_value = [mock_frame]

    # Request scaling to 35x35
    # Ratio = 35/100 = 0.35.
    # New width = 100 * 0.35 = 35.
    # Logic: max(2, trunc(35/2)*2) = max(2, 17*2) = 34.
    result = ffmpeg.extract_frame_with_pyav(video_path, scale=(35, 35))

    assert result is not None
    assert result.size == (34, 34)

@patch("src.iPhoto.utils.ffmpeg.av")
def test_extract_frame_with_pyav_exception_returns_none(mock_av, tmp_path):
    """Test exception handling."""
    # Ensure av.FFmpegError is a real exception type so it can be caught
    mock_av.FFmpegError = Exception

    mock_av.open.side_effect = Exception("Boom")
    result = ffmpeg.extract_frame_with_pyav(tmp_path / "video.mp4")
    assert result is None
