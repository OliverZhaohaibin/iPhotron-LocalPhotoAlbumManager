"""Tests for SAR/rotation-corrected display size logic.

``_probe_display_size`` applies both SAR and rotation corrections so the
video item bounding box matches the **post-rotation** display aspect ratio.
Qt's ``QGraphicsVideoItem`` renders post-rotation content inside the item;
without the correct aspect ratio the content is letterboxed with black bars.

``read_video_meta`` stores raw coded dimensions from ffprobe — no SAR or
rotation correction — matching the original codebase behaviour.
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


def test_sar_does_not_alter_stored_dimensions(monkeypatch, tmp_path):
    """SAR is NOT applied to stored w/h — coded dimensions are stored as-is.

    The display-size correction for non-square SAR is handled at rendering
    time by ``_probe_display_size()`` in video_area.py.  The stored metadata
    reflects the coded resolution from ffprobe, matching the original
    codebase behaviour.
    """

    sample = tmp_path / "clip.mp4"

    monkeypatch.setattr(
        metadata,
        "probe_media",
        _make_probe([{
            "codec_type": "video",
            "codec_name": "h264",
            "width": 720,
            "height": 576,
            "sample_aspect_ratio": "16:11",
        }]),
    )

    info = metadata.read_video_meta(sample)

    # Coded dimensions, not SAR-corrected display dimensions
    assert info["w"] == 720
    assert info["h"] == 576


def test_rotation_does_not_swap_stored_dimensions(monkeypatch, tmp_path):
    """Rotation metadata must NOT swap stored w/h — coded dims are stored."""

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


def test_probe_display_size_swaps_for_90_rotation(monkeypatch):
    """_probe_display_size must swap dimensions for 90° rotation.

    Qt renders post-rotation content inside the video item bounding box.
    For a coded 1920×1080 video with -90° rotation, the display size is
    1080×1920 (portrait).  The item must be sized to this portrait ratio
    so the content fills it without internal black bars.
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
    # Dimensions swapped for 90° rotation
    assert abs(result.width() - 1080) < 0.1
    assert abs(result.height() - 1920) < 0.1


def test_probe_display_size_swaps_for_270_rotation(monkeypatch):
    """_probe_display_size must swap dimensions for 270° rotation."""
    from iPhoto.gui.ui.widgets.video_area import _probe_display_size
    from iPhoto.utils import ffmpeg as ffmpeg_mod

    def fake_probe_media(_):
        return {
            "streams": [{
                "codec_type": "video",
                "width": 1920,
                "height": 1440,
                "side_data_list": [
                    {"side_data_type": "Display Matrix", "rotation": -270},
                ],
            }],
        }

    monkeypatch.setattr(ffmpeg_mod, "probe_media", fake_probe_media)

    result = _probe_display_size(Path("/fake/video.mp4"))
    assert result is not None
    # Dimensions swapped for 270° rotation
    assert abs(result.width() - 1440) < 0.1
    assert abs(result.height() - 1920) < 0.1


def test_probe_display_size_no_swap_for_180_rotation(monkeypatch):
    """180° rotation does not swap w/h."""
    from iPhoto.gui.ui.widgets.video_area import _probe_display_size
    from iPhoto.utils import ffmpeg as ffmpeg_mod

    def fake_probe_media(_):
        return {
            "streams": [{
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "side_data_list": [
                    {"side_data_type": "Display Matrix", "rotation": 180},
                ],
            }],
        }

    monkeypatch.setattr(ffmpeg_mod, "probe_media", fake_probe_media)

    result = _probe_display_size(Path("/fake/video.mp4"))
    assert result is not None
    assert abs(result.width() - 1920) < 0.1
    assert abs(result.height() - 1080) < 0.1


def test_probe_display_size_no_swap_for_zero_rotation(monkeypatch):
    """A video with no rotation keeps coded dimensions."""
    from iPhoto.gui.ui.widgets.video_area import _probe_display_size
    from iPhoto.utils import ffmpeg as ffmpeg_mod

    def fake_probe_media(_):
        return {
            "streams": [{
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
            }],
        }

    monkeypatch.setattr(ffmpeg_mod, "probe_media", fake_probe_media)

    result = _probe_display_size(Path("/fake/video.mp4"))
    assert result is not None
    assert abs(result.width() - 1920) < 0.1
    assert abs(result.height() - 1080) < 0.1


def test_probe_display_size_legacy_rotate_tag(monkeypatch):
    """_probe_display_size should swap for legacy QuickTime rotate tag."""
    from iPhoto.gui.ui.widgets.video_area import _probe_display_size
    from iPhoto.utils import ffmpeg as ffmpeg_mod

    def fake_probe_media(_):
        return {
            "streams": [{
                "codec_type": "video",
                "width": 1920,
                "height": 1440,
                "tags": {"rotate": "90"},
            }],
        }

    monkeypatch.setattr(ffmpeg_mod, "probe_media", fake_probe_media)

    result = _probe_display_size(Path("/fake/video.mp4"))
    assert result is not None
    assert abs(result.width() - 1440) < 0.1
    assert abs(result.height() - 1920) < 0.1


def test_probe_display_size_sar_plus_rotation(monkeypatch):
    """Both SAR correction and rotation should be applied together."""
    from iPhoto.gui.ui.widgets.video_area import _probe_display_size
    from iPhoto.utils import ffmpeg as ffmpeg_mod

    def fake_probe_media(_):
        return {
            "streams": [{
                "codec_type": "video",
                "width": 720,
                "height": 576,
                "sample_aspect_ratio": "16:11",
                "side_data_list": [
                    {"side_data_type": "Display Matrix", "rotation": -90},
                ],
            }],
        }

    monkeypatch.setattr(ffmpeg_mod, "probe_media", fake_probe_media)

    result = _probe_display_size(Path("/fake/video.mp4"))
    assert result is not None
    # SAR correction: 720 * 16/11 ≈ 1047.27, then 90° rotation swaps
    assert abs(result.width() - 576) < 0.1
    assert abs(result.height() - 720 * 16 / 11) < 0.1


def test_probe_display_size_no_ffprobe(monkeypatch):
    """_probe_display_size should return None when ffprobe fails."""
    from iPhoto.gui.ui.widgets.video_area import _probe_display_size
    from iPhoto.utils import ffmpeg as ffmpeg_mod

    def fake_probe_media(_):
        raise RuntimeError("ffprobe not available")

    monkeypatch.setattr(ffmpeg_mod, "probe_media", fake_probe_media)

    result = _probe_display_size(Path("/fake/video.mp4"))
    assert result is None


def test_probe_display_size_frame_cropping(monkeypatch):
    """_probe_display_size applies frame cropping before SAR/rotation.

    For a coded 1920×1440 video with crop l/r=88, t/b=66 and -90° rotation:
    - After crop: (1920-176) × (1440-132) = 1744 × 1308
    - After rotation: 1308 × 1744
    """
    from iPhoto.gui.ui.widgets.video_area import _probe_display_size
    from iPhoto.utils import ffmpeg as ffmpeg_mod

    def fake_probe_media(_):
        return {
            "streams": [{
                "codec_type": "video",
                "width": 1920,
                "height": 1440,
                "side_data_list": [
                    {
                        "side_data_type": "Frame Cropping",
                        "crop_top": 66,
                        "crop_bottom": 66,
                        "crop_left": 88,
                        "crop_right": 88,
                    },
                    {"side_data_type": "Display Matrix", "rotation": -90},
                ],
            }],
        }

    monkeypatch.setattr(ffmpeg_mod, "probe_media", fake_probe_media)

    result = _probe_display_size(Path("/fake/video.mov"))
    assert result is not None
    # Cropped then rotated: 1308 × 1744
    assert abs(result.width() - 1308) < 0.1
    assert abs(result.height() - 1744) < 0.1


def test_probe_display_size_frame_cropping_no_rotation(monkeypatch):
    """Frame cropping without rotation reduces display dimensions."""
    from iPhoto.gui.ui.widgets.video_area import _probe_display_size
    from iPhoto.utils import ffmpeg as ffmpeg_mod

    def fake_probe_media(_):
        return {
            "streams": [{
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "side_data_list": [
                    {
                        "side_data_type": "Frame Cropping",
                        "crop_top": 10,
                        "crop_bottom": 10,
                        "crop_left": 20,
                        "crop_right": 20,
                    },
                ],
            }],
        }

    monkeypatch.setattr(ffmpeg_mod, "probe_media", fake_probe_media)

    result = _probe_display_size(Path("/fake/video.mp4"))
    assert result is not None
    assert abs(result.width() - 1880) < 0.1
    assert abs(result.height() - 1060) < 0.1


def test_probe_display_size_frame_cropping_with_sar(monkeypatch):
    """Frame cropping is applied before SAR correction."""
    from iPhoto.gui.ui.widgets.video_area import _probe_display_size
    from iPhoto.utils import ffmpeg as ffmpeg_mod

    def fake_probe_media(_):
        return {
            "streams": [{
                "codec_type": "video",
                "width": 720,
                "height": 576,
                "sample_aspect_ratio": "16:11",
                "side_data_list": [
                    {
                        "side_data_type": "Frame Cropping",
                        "crop_left": 10,
                        "crop_right": 10,
                        "crop_top": 0,
                        "crop_bottom": 0,
                    },
                ],
            }],
        }

    monkeypatch.setattr(ffmpeg_mod, "probe_media", fake_probe_media)

    result = _probe_display_size(Path("/fake/video.mp4"))
    assert result is not None
    # Cropped width = 720-20 = 700, SAR = 16:11 → 700 * 16/11 ≈ 1018.18
    assert abs(result.width() - 700 * 16 / 11) < 0.1
    assert abs(result.height() - 576) < 0.1
