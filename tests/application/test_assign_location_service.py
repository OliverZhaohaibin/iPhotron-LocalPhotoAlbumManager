from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from iPhoto.application.services.assign_location_service import AssignLocationService
from iPhoto.errors import ExternalToolError


def test_assign_location_persists_library_geodata_when_exiftool_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_repository = Mock()
    metadata = Mock()
    metadata.write_gps_metadata.side_effect = ExternalToolError(
        "exiftool executable not found"
    )

    service = AssignLocationService(state_repository, metadata)
    result = service.assign(
        asset_path=tmp_path / "image.jpg",
        asset_rel="image.jpg",
        display_name="  Paris  ",
        latitude=48.8566,
        longitude=2.3522,
        is_video=False,
        existing_metadata={
            "make": "FUJIFILM",
            "model": "X-T4",
            "gps": None,
            "micro_thumbnail": b"preview-bytes",
        },
    )

    assert result.display_name == "Paris"
    assert result.gps == {"lat": 48.8566, "lon": 2.3522}
    assert result.metadata["gps"] == {"lat": 48.8566, "lon": 2.3522}
    assert result.metadata["location"] == "Paris"
    assert result.metadata["location_name"] == "Paris"
    assert result.metadata["make"] == "FUJIFILM"
    assert result.metadata["micro_thumbnail"] == b"preview-bytes"
    assert result.file_write_error == "exiftool executable not found"

    metadata.read_back_metadata.assert_not_called()
    state_repository.update_asset_geodata.assert_called_once_with(
        "image.jpg",
        gps={"lat": 48.8566, "lon": 2.3522},
        location="Paris",
        metadata_updates=result.metadata,
    )


def test_assign_location_merges_refreshed_metadata_without_overwriting_with_empty_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_repository = Mock()
    metadata = Mock()
    metadata.read_back_metadata.return_value = {
        "make": "FUJIFILM",
        "model": "X-T4",
        "iso": 640,
        "lens": "XF 23mm",
    }

    service = AssignLocationService(state_repository, metadata)
    result = service.assign(
        asset_path=tmp_path / "image.jpg",
        asset_rel="image.jpg",
        display_name="Munich",
        latitude=48.137154,
        longitude=11.576124,
        is_video=False,
        existing_metadata={"make": "FUJIFILM", "model": "X-T4", "iso": 320},
    )

    assert result.file_write_error is None
    assert result.metadata["make"] == "FUJIFILM"
    assert result.metadata["model"] == "X-T4"
    assert result.metadata["iso"] == 640
    assert result.metadata["lens"] == "XF 23mm"
    assert result.metadata["gps"] == {"lat": 48.137154, "lon": 11.576124}
    metadata.write_gps_metadata.assert_called_once_with(
        tmp_path / "image.jpg",
        latitude=48.137154,
        longitude=11.576124,
        is_video=False,
    )
    metadata.read_back_metadata.assert_called_once_with(
        tmp_path / "image.jpg",
        is_video=False,
        existing_metadata={"make": "FUJIFILM", "model": "X-T4", "iso": 320},
    )
    state_repository.update_asset_geodata.assert_called_once()
