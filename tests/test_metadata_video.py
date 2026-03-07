"""Tests covering the video metadata extraction helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.io import metadata


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
