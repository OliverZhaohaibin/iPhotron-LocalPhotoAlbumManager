"""Tests for packed-video upload format detection."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")
pytest.importorskip("PySide6.QtMultimedia", reason="QtMultimedia is required")

from OpenGL import GL as gl
from PySide6.QtCore import QSize
from PySide6.QtMultimedia import QVideoFrameFormat

from iPhoto.gui.ui.widgets.gl_texture_manager import _packed_frame_upload_spec


def test_packed_frame_upload_spec_supports_rgba8888() -> None:
    fmt = QVideoFrameFormat(QSize(320, 240), QVideoFrameFormat.PixelFormat.Format_RGBA8888)

    assert _packed_frame_upload_spec(fmt) == (gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, 4)


def test_packed_frame_upload_spec_supports_bgra8888_when_available() -> None:
    pixel_format = getattr(QVideoFrameFormat.PixelFormat, "Format_BGRA8888", None)
    if pixel_format is None:
        pytest.skip("Qt build does not expose BGRA8888 video frames")

    fmt = QVideoFrameFormat(QSize(320, 240), pixel_format)

    assert _packed_frame_upload_spec(fmt) == (gl.GL_BGRA, gl.GL_UNSIGNED_BYTE, 4)
