"""Tests covering the video metadata extraction helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.io import metadata
from iPhoto.io.metadata_extractors import _parse_duration_value, _extract_duration_from_exiftool


# ── _parse_duration_value ─────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("8.01", 8.01),
    ("123.456000", 123.456),
    ("10.50 s", 10.5),
    ("0.5 s", 0.5),
    (8.01, 8.01),
    (120, 120.0),
    ("00:01:23.456000000", 83.456),
    ("00:00:45.123000000", 45.123),
    ("01:30:00.000000000", 5400.0),
    ("00:00:10", 10.0),
    ("0:00:00", None),       # zero duration
    ("", None),              # empty string
    (None, None),            # None
    (0, None),               # zero numeric
    (-5.0, None),            # negative
])
def test_parse_duration_value(value, expected):
    result = _parse_duration_value(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, rel=1e-3)


# ── _extract_duration_from_exiftool ───────────────────────────────────────────

def test_extract_duration_from_quicktime_group():
    meta = {"QuickTime": {"Duration": "12.5 s"}}
    assert _extract_duration_from_exiftool(meta) == pytest.approx(12.5, rel=1e-3)


def test_extract_duration_from_composite_group():
    meta = {"Composite": {"Duration": "30.0 s"}}
    assert _extract_duration_from_exiftool(meta) == pytest.approx(30.0, rel=1e-3)


def test_extract_duration_from_flattened_key():
    meta = {"Composite:Duration": "5.0 s"}
    assert _extract_duration_from_exiftool(meta) == pytest.approx(5.0, rel=1e-3)


def test_extract_duration_returns_none_when_absent():
    meta = {"QuickTime": {"Make": "Apple"}}
    assert _extract_duration_from_exiftool(meta) is None


# ── read_video_meta integration tests ─────────────────────────────────────────

def test_read_video_meta_enriches_quicktime_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ensure ``read_video_meta`` merges ffprobe data with ExifTool metadata."""

    sample_path = tmp_path / "clip.MOV"

    def fake_probe_media(path: Path) -> dict[str, object]:
        """Return a deterministic ffprobe-like mapping for the test run."""

        assert path == sample_path
        return {
            "format": {
                "duration": "8.01",
                "size": "24192000",
                "tags": {
                    "com.apple.quicktime.content.identifier": "ABC-123",
                },
            },
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "60000/1001",
                    "tags": {
                        "com.apple.quicktime.still-image-time": "1.5",
                    },
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                },
            ],
        }

    monkeypatch.setattr(metadata, "probe_media", fake_probe_media)

    exif_payload = {
        "QuickTime": {
            "Make": "Apple",
            "Model": "Apple iPhone 13 Pro",
        }
    }

    info = metadata.read_video_meta(sample_path, exif_payload)

    assert info["make"] == "Apple"
    assert info["model"] == "Apple iPhone 13 Pro"
    assert info["content_id"] == "ABC-123"
    assert info["w"] == 1920 and info["h"] == 1080
    assert info["bytes"] == 24192000
    assert info["codec"] == "hevc"
    assert info["frame_rate"] == pytest.approx(59.94, rel=1e-3)
    assert info["dur"] == pytest.approx(8.01, rel=1e-3)
    assert info["still_image_time"] == pytest.approx(1.5, rel=1e-6)


def test_read_video_meta_extracts_content_id_from_flattened_exiftool_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sample_path = tmp_path / "clip.MP4"

    def fake_probe_media(_: Path) -> dict[str, object]:
        raise metadata.ExternalToolError("ffprobe unavailable")

    monkeypatch.setattr(metadata, "probe_media", fake_probe_media)

    exif_payload = {
        "Keys:ContentIdentifier": " CID-FROM-KEYS ",
    }

    info = metadata.read_video_meta(sample_path, exif_payload)

    assert info["content_id"] == "CID-FROM-KEYS"


def test_read_video_meta_falls_back_to_stream_duration_when_format_duration_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Duration from the video stream is used when format.duration is absent (Linux MKV/WebM)."""

    sample_path = tmp_path / "clip.mkv"

    def fake_probe_media(_: Path) -> dict[str, object]:
        return {
            "format": {
                "size": "5000000",
            },
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "vp9",
                    "width": 1280,
                    "height": 720,
                    "avg_frame_rate": "30/1",
                    "duration": "12.500000",
                },
            ],
        }

    monkeypatch.setattr(metadata, "probe_media", fake_probe_media)

    info = metadata.read_video_meta(sample_path)

    assert info["dur"] == pytest.approx(12.5, rel=1e-6)
    assert info["w"] == 1280 and info["h"] == 720
    assert info["codec"] == "vp9"


def test_read_video_meta_falls_back_to_matroska_duration_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Matroska DURATION tag (HH:MM:SS format) is parsed when stream.duration is absent."""

    sample_path = tmp_path / "clip.mkv"

    def fake_probe_media(_: Path) -> dict[str, object]:
        return {
            "format": {
                "size": "5000000",
            },
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "24/1",
                    "tags": {
                        "DURATION": "00:01:23.456000000",
                    },
                },
            ],
        }

    monkeypatch.setattr(metadata, "probe_media", fake_probe_media)

    info = metadata.read_video_meta(sample_path)

    assert info["dur"] == pytest.approx(83.456, rel=1e-3)
    assert info["w"] == 1920 and info["h"] == 1080


def test_read_video_meta_uses_format_tags_duration_for_matroska(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """format.tags.DURATION is used when format.duration is absent (Matroska on Linux)."""

    sample_path = tmp_path / "clip.webm"

    def fake_probe_media(_: Path) -> dict[str, object]:
        return {
            "format": {
                "size": "2000000",
                "tags": {
                    "DURATION": "00:00:45.123000000",
                },
            },
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "vp9",
                    "width": 640,
                    "height": 480,
                    "avg_frame_rate": "30/1",
                },
            ],
        }

    monkeypatch.setattr(metadata, "probe_media", fake_probe_media)

    info = metadata.read_video_meta(sample_path)

    assert info["dur"] == pytest.approx(45.123, rel=1e-3)


def test_read_video_meta_exiftool_fallback_when_ffprobe_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ExifTool duration is used when ffprobe is not available (common on Linux)."""

    sample_path = tmp_path / "clip.mp4"

    def fake_probe_media(_: Path) -> dict[str, object]:
        raise metadata.ExternalToolError("ffprobe not found")

    monkeypatch.setattr(metadata, "probe_media", fake_probe_media)

    exif_payload = {
        "QuickTime": {
            "Duration": "10.50 s",
            "Make": "Samsung",
        }
    }

    info = metadata.read_video_meta(sample_path, exif_payload)

    assert info["dur"] == pytest.approx(10.5, rel=1e-3)
    assert info["make"] == "Samsung"


def test_read_video_meta_exiftool_composite_duration_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Composite.Duration from ExifTool is used as fallback."""

    sample_path = tmp_path / "clip.avi"

    def fake_probe_media(_: Path) -> dict[str, object]:
        raise metadata.ExternalToolError("ffprobe not found")

    monkeypatch.setattr(metadata, "probe_media", fake_probe_media)

    exif_payload = {
        "Composite": {
            "Duration": "25.3 s",
        }
    }

    info = metadata.read_video_meta(sample_path, exif_payload)

    assert info["dur"] == pytest.approx(25.3, rel=1e-3)


def test_read_video_meta_ffprobe_overrides_exiftool_duration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ffprobe duration takes precedence over ExifTool duration."""

    sample_path = tmp_path / "clip.mp4"

    def fake_probe_media(_: Path) -> dict[str, object]:
        return {
            "format": {
                "duration": "15.5",
                "size": "1000000",
            },
            "streams": [],
        }

    monkeypatch.setattr(metadata, "probe_media", fake_probe_media)

    exif_payload = {
        "QuickTime": {
            "Duration": "15.48 s",
        }
    }

    info = metadata.read_video_meta(sample_path, exif_payload)

    # ffprobe value (15.5) should win over ExifTool (15.48)
    assert info["dur"] == pytest.approx(15.5, rel=1e-6)
