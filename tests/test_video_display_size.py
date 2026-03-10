"""Tests for SAR-corrected display size logic.

Rotation is intentionally NOT applied to stored dimensions or display sizing
because Qt's ``QGraphicsVideoItem`` with ``KeepAspectRatio`` handles
display-matrix rotation internally.  Swapping w/h for rotation would
double-apply the transform, producing incorrect letterboxing (e.g. black
bars on landscape videos).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.io import metadata


# ── metadata.py: stored coded dimensions in read_video_meta ─────────────────


def _make_probe(streams, fmt=None):
    """Return a fake probe_media function with the given stream data."""

    def fake_probe_media(_: Path) -> dict[str, object]:
        result: dict[str, object] = {"streams": streams}
        if fmt is not None:
            result["format"] = fmt
        return result

    return fake_probe_media


def test_coded_dimensions_stored_unchanged(monkeypatch, tmp_path):
    """read_video_meta stores the coded w/h from ffprobe without modification."""

    sample = tmp_path / "clip.mp4"

    monkeypatch.setattr(
        metadata,
        "probe_media",
        _make_probe([{
            "codec_type": "video",
            "codec_name": "hevc",
            "width": 1920,
            "height": 1080,
        }]),
    )

    info = metadata.read_video_meta(sample)

    assert info["w"] == 1920
    assert info["h"] == 1080


def test_rotation_does_not_swap_stored_dimensions(monkeypatch, tmp_path):
    """Rotation metadata must NOT swap w/h — Qt handles rotation internally."""

    sample = tmp_path / "clip.mov"

    monkeypatch.setattr(
        metadata,
        "probe_media",
        _make_probe([{
            "codec_type": "video",
            "codec_name": "hevc",
            "width": 1920,
            "height": 1080,
            "side_data_list": [
                {"side_data_type": "Display Matrix", "rotation": -90},
            ],
        }]),
    )

    info = metadata.read_video_meta(sample)

    # Coded dimensions stored as-is, no rotation swap
    assert info["w"] == 1920
    assert info["h"] == 1080


def test_legacy_rotate_tag_does_not_swap_stored_dimensions(monkeypatch, tmp_path):
    """Legacy QuickTime rotate tag must NOT swap stored w/h."""

    sample = tmp_path / "clip.mov"

    monkeypatch.setattr(
        metadata,
        "probe_media",
        _make_probe([{
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "tags": {"rotate": "90"},
        }]),
    )

    info = metadata.read_video_meta(sample)

    assert info["w"] == 1920
    assert info["h"] == 1080


# ── video_area.py: _parse_sar and _probe_display_size ────────────────────────


def test_parse_sar_valid():
    """_parse_sar should return (num, den) for valid strings."""
    from iPhoto.gui.ui.widgets.video_area import _parse_sar

    assert _parse_sar("16:11") == (16, 11)
    assert _parse_sar("1:1") == (1, 1)
    assert _parse_sar("4:3") == (4, 3)


def test_parse_sar_invalid():
    """_parse_sar should return None for invalid inputs."""
    from iPhoto.gui.ui.widgets.video_area import _parse_sar

    assert _parse_sar(None) is None
    assert _parse_sar("") is None
    assert _parse_sar("abc") is None
    assert _parse_sar("0:0") is None
    assert _parse_sar("1:0") is None
    assert _parse_sar(123) is None


def test_probe_display_size_with_sar(monkeypatch):
    """_probe_display_size should apply SAR correction."""
    from iPhoto.gui.ui.widgets.video_area import _probe_display_size
    from iPhoto.utils import ffmpeg as ffmpeg_mod

    def fake_probe_media(_):
        return {
            "streams": [{
                "codec_type": "video",
                "width": 720,
                "height": 576,
                "sample_aspect_ratio": "16:11",
            }],
        }

    monkeypatch.setattr(ffmpeg_mod, "probe_media", fake_probe_media)

    result = _probe_display_size(Path("/fake/video.mp4"))
    assert result is not None
    assert abs(result.width() - 720 * 16 / 11) < 0.1
    assert abs(result.height() - 576) < 0.1


def test_probe_display_size_ignores_rotation(monkeypatch):
    """_probe_display_size must NOT swap dimensions for rotation.

    Qt handles display-matrix rotation internally in QGraphicsVideoItem.
    Swapping w/h here would double-apply rotation, causing landscape videos
    to get portrait backgrounds with top/bottom black bars.
    """
    from iPhoto.gui.ui.widgets.video_area import _probe_display_size
    from iPhoto.utils import ffmpeg as ffmpeg_mod

    def fake_probe_media(_):
        return {
            "streams": [{
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "side_data_list": [
                    {"side_data_type": "Display Matrix", "rotation": -90},
                ],
            }],
        }

    monkeypatch.setattr(ffmpeg_mod, "probe_media", fake_probe_media)

    result = _probe_display_size(Path("/fake/video.mp4"))
    assert result is not None
    # Dimensions must NOT be swapped — Qt handles rotation internally
    assert abs(result.width() - 1920) < 0.1
    assert abs(result.height() - 1080) < 0.1


def test_probe_display_size_no_ffprobe(monkeypatch):
    """_probe_display_size should return None when ffprobe fails."""
    from iPhoto.gui.ui.widgets.video_area import _probe_display_size
    from iPhoto.utils import ffmpeg as ffmpeg_mod

    def fake_probe_media(_):
        raise RuntimeError("ffprobe not available")

    monkeypatch.setattr(ffmpeg_mod, "probe_media", fake_probe_media)

    result = _probe_display_size(Path("/fake/video.mp4"))
    assert result is None
