"""Tests for bounded video frame fallback behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)

from PySide6.QtCore import QSize

from iPhoto.errors import ExternalToolTimeoutError
from iPhoto.gui.ui.tasks import video_frame_grabber


def test_timeout_stops_additional_seek_attempts(mocker) -> None:
    """One timed-out ffmpeg attempt terminates the whole frame request."""

    source = Path("/fake/stalled.mov")
    pyav = mocker.patch.object(
        video_frame_grabber,
        "extract_frame_with_pyav",
        return_value=None,
    )
    ffmpeg = mocker.patch.object(
        video_frame_grabber,
        "extract_video_frame",
        side_effect=ExternalToolTimeoutError("ffmpeg timed out after 30 seconds"),
    )

    result = video_frame_grabber.grab_video_frame(
        source,
        QSize(320, 240),
        still_image_time=1.0,
        duration=10.0,
    )

    assert result is None
    pyav.assert_called_once()
    ffmpeg.assert_called_once()
