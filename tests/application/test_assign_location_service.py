from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from iPhoto.application.services import assign_location_service as service_module
from iPhoto.application.services.assign_location_service import AssignLocationService
from iPhoto.errors import ExternalToolError


def test_assign_location_persists_library_geodata_when_exiftool_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Mock()
    monkeypatch.setattr(service_module, "get_global_repository", Mock(return_value=repository))
    monkeypatch.setattr(
        service_module,
        "write_gps_metadata",
        Mock(side_effect=ExternalToolError("exiftool executable not found")),
    )

    service = AssignLocationService(tmp_path)
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

    repository.update_asset_geodata.assert_called_once_with(
        "image.jpg",
        gps={"lat": 48.8566, "lon": 2.3522},
        location="Paris",
        metadata_updates=result.metadata,
    )


def test_assign_location_merges_refreshed_metadata_without_overwriting_with_empty_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Mock()
    monkeypatch.setattr(service_module, "get_global_repository", Mock(return_value=repository))
    monkeypatch.setattr(service_module, "write_gps_metadata", Mock())
    monkeypatch.setattr(
        service_module,
        "get_metadata_batch",
        Mock(return_value=[{"SourceFile": "image.jpg"}]),
    )
    monkeypatch.setattr(
        service_module,
        "read_image_meta_with_exiftool",
        Mock(return_value={"make": None, "model": "", "iso": 640, "lens": "XF 23mm"}),
    )

    service = AssignLocationService(tmp_path)
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
    repository.update_asset_geodata.assert_called_once()
