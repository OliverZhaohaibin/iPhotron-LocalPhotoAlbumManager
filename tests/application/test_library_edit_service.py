from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from iPhoto.bootstrap.library_edit_service import LibraryEditService


def test_library_edit_service_round_trips_adjustments(tmp_path: Path) -> None:
    asset = tmp_path / "photo.jpg"
    asset.touch()
    service = LibraryEditService(tmp_path)

    assert service.sidecar_exists(asset) is False

    service.write_adjustments(asset, {"Crop_W": 0.8, "Light_Master": 0.2})

    assert service.sidecar_exists(asset) is True
    assert service.read_adjustments(asset)["Crop_W"] == 0.8


def test_edit_commit_marks_new_revision_stale_after_atomic_sidecar_write(
    tmp_path: Path,
) -> None:
    asset = tmp_path / "photo.jpg"
    asset.write_bytes(b"image")
    thumbnail_state = Mock()
    service = LibraryEditService(
        tmp_path,
        thumbnail_state_service=thumbnail_state,
    )

    first = service.write_adjustments(asset, {"Crop_Rotate90": 1.0})
    second = service.write_adjustments(asset, {"Crop_Rotate90": 2.0})

    assert first.thumbnail_revision != second.thumbnail_revision
    assert thumbnail_state.mark_thumbnail_stale.call_args_list == [
        ((asset.resolve(), first.thumbnail_revision), {}),
        ((asset.resolve(), second.thumbnail_revision), {}),
    ]


def test_library_edit_service_describes_video_adjustments(tmp_path: Path) -> None:
    asset = tmp_path / "clip.mov"
    asset.touch()
    service = LibraryEditService(tmp_path)
    service.write_adjustments(
        asset,
        {
            "Light_Master": 0.4,
            "Video_Trim_In_Sec": 1.0,
            "Video_Trim_Out_Sec": 4.0,
        },
    )

    state = service.describe_adjustments(asset, duration_hint=5.0)

    assert state.sidecar_exists is True
    assert state.adjusted_preview is True
    assert state.has_visible_edits is True
    assert state.trim_range_ms == (1000, 4000)
    assert state.effective_duration_sec == 3.0
    assert state.resolved_adjustments


def test_library_edit_service_restores_adjustments_after_reopen(tmp_path: Path) -> None:
    asset = tmp_path / "photo.jpg"
    asset.touch()

    first_service = LibraryEditService(tmp_path)
    first_service.write_adjustments(asset, {"Crop_W": 0.7, "Light_Master": 0.1})

    reopened_service = LibraryEditService(tmp_path)

    assert reopened_service.sidecar_exists(asset) is True
    assert reopened_service.read_adjustments(asset)["Crop_W"] == 0.7


def test_library_edit_service_exposes_default_adjustments(tmp_path: Path) -> None:
    service = LibraryEditService(tmp_path)

    defaults = service.default_adjustments()

    assert defaults["Crop_W"] == 1.0
    assert defaults["Video_Trim_In_Sec"] == 0.0
