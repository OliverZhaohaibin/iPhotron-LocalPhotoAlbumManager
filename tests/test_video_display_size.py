"""Tests for SAR / rotation-corrected display size logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.io import metadata


# ── metadata.py: SAR correction in read_video_meta ──────────────────────────


def _make_probe(streams, fmt=None):
    """Return a fake probe_media function with the given stream data."""

    def fake_probe_media(_: Path) -> dict[str, object]:
        result: dict[str, object] = {"streams": streams}
        if fmt is not None:
            result["format"] = fmt
        return result

    return fake_probe_media


def test_sar_correction_applied_to_stored_dimensions(monkeypatch, tmp_path):
    """Non-square SAR multiplies the coded width to get display width."""

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

    # 720 * 16 / 11 ≈ 1047.27  →  round → 1047
    assert info["w"] == round(720 * 16 / 11)
    assert info["h"] == 576


def test_sar_1_1_leaves_dimensions_unchanged(monkeypatch, tmp_path):
    """SAR 1:1 is the common case and must not alter coded dimensions."""

    sample = tmp_path / "clip.mp4"

    monkeypatch.setattr(
        metadata,
        "probe_media",
        _make_probe([{
            "codec_type": "video",
            "codec_name": "hevc",
            "width": 1920,
            "height": 1080,
            "sample_aspect_ratio": "1:1",
        }]),
    )

    info = metadata.read_video_meta(sample)

    assert info["w"] == 1920
    assert info["h"] == 1080


def test_rotation_90_swaps_dimensions(monkeypatch, tmp_path):
    """A 90° rotation (portrait iPhone video) should swap w/h."""

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

    assert info["w"] == 1080
    assert info["h"] == 1920


def test_rotation_270_swaps_dimensions(monkeypatch, tmp_path):
    """A 270° rotation should also swap w/h."""

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
                {"side_data_type": "Display Matrix", "rotation": -270},
            ],
        }]),
    )

    info = metadata.read_video_meta(sample)

    assert info["w"] == 1080
    assert info["h"] == 1920


def test_rotation_180_keeps_dimensions(monkeypatch, tmp_path):
    """A 180° rotation does not swap w/h."""

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
                {"side_data_type": "Display Matrix", "rotation": 180},
            ],
        }]),
    )

    info = metadata.read_video_meta(sample)

    assert info["w"] == 1920
    assert info["h"] == 1080


def test_rotation_from_legacy_tag(monkeypatch, tmp_path):
    """Older QuickTime files use a tags.rotate field instead of side_data."""

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

    assert info["w"] == 1080
    assert info["h"] == 1920


def test_sar_with_rotation_combined(monkeypatch, tmp_path):
    """Both SAR and rotation should be applied."""

    sample = tmp_path / "clip.mov"

    monkeypatch.setattr(
        metadata,
        "probe_media",
        _make_probe([{
            "codec_type": "video",
            "codec_name": "h264",
            "width": 720,
            "height": 576,
            "sample_aspect_ratio": "16:11",
            "side_data_list": [
                {"side_data_type": "Display Matrix", "rotation": -90},
            ],
        }]),
    )

    info = metadata.read_video_meta(sample)

    # SAR first: 720 * 16/11 ≈ 1047, then 90° rotation swaps
    assert info["w"] == 576
    assert info["h"] == round(720 * 16 / 11)


def test_no_sar_field_leaves_dimensions_unchanged(monkeypatch, tmp_path):
    """Missing SAR field should leave coded dimensions as-is."""

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


def test_probe_display_size_with_rotation(monkeypatch):
    """_probe_display_size should swap dimensions for 90° rotation."""
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
    assert abs(result.width() - 1080) < 0.1
    assert abs(result.height() - 1920) < 0.1


def test_probe_display_size_no_ffprobe(monkeypatch):
    """_probe_display_size should return None when ffprobe fails."""
    from iPhoto.gui.ui.widgets.video_area import _probe_display_size
    from iPhoto.utils import ffmpeg as ffmpeg_mod

    def fake_probe_media(_):
        raise RuntimeError("ffprobe not available")

    monkeypatch.setattr(ffmpeg_mod, "probe_media", fake_probe_media)

    result = _probe_display_size(Path("/fake/video.mp4"))
    assert result is None
