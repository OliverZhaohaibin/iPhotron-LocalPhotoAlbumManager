from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.errors import ExternalToolError
from iPhoto.infrastructure.services.exiftool_metadata_writer import ExifToolMetadataWriter


class _FakeLocationMetadata:
    def __init__(self, refreshed: dict) -> None:
        self.refreshed = refreshed
        self.writes: list[tuple[Path, dict]] = []

    def write_gps_metadata(self, path: Path, **kwargs) -> None:
        self.writes.append((path, kwargs))

    def read_back_metadata(self, path: Path, **kwargs) -> dict:
        del path, kwargs
        return dict(self.refreshed)


def test_exiftool_metadata_writer_verifies_written_gps(tmp_path: Path) -> None:
    metadata = _FakeLocationMetadata({"gps": {"lat": 48.137154, "lon": 11.576124}})
    writer = ExifToolMetadataWriter(metadata)

    refreshed = writer.write_location(
        tmp_path / "clip.mov",
        latitude=48.137154,
        longitude=11.576124,
        is_video=True,
    )

    assert refreshed["gps"] == {"lat": 48.137154, "lon": 11.576124}
    assert metadata.writes[0][1]["is_video"] is True


def test_exiftool_metadata_writer_fails_when_readback_gps_does_not_match(
    tmp_path: Path,
) -> None:
    metadata = _FakeLocationMetadata({"gps": {"lat": 1.0, "lon": 2.0}})
    writer = ExifToolMetadataWriter(metadata)

    with pytest.raises(ExternalToolError):
        writer.write_location(
            tmp_path / "clip.mov",
            latitude=48.137154,
            longitude=11.576124,
            is_video=True,
        )
