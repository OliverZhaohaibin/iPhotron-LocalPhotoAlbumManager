from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iPhoto.gui.coordinators.edit_coordinator import EditCoordinator


def test_restore_detail_video_preview_reloads_and_restarts_playback(mocker) -> None:
    coordinator = EditCoordinator.__new__(EditCoordinator)
    video_area = mocker.Mock()
    coordinator._ui = SimpleNamespace(video_area=video_area)

    source = Path("/fake/video.mp4")
    raw_adjustments = {"Crop_W": 0.8}
    render_adjustments = {"Crop_W": 0.8, "Exposure": 0.2}

    mocker.patch(
        "iPhoto.gui.coordinators.edit_coordinator.sidecar.load_adjustments",
        return_value=raw_adjustments,
    )
    mocker.patch(
        "iPhoto.gui.coordinators.edit_coordinator.sidecar.trim_is_non_default",
        return_value=True,
    )
    mocker.patch(
        "iPhoto.gui.coordinators.edit_coordinator.sidecar.has_non_default_adjustments",
        return_value=True,
    )
    mocker.patch(
        "iPhoto.gui.coordinators.edit_coordinator.sidecar.normalise_video_trim",
        return_value=(1.25, 4.5),
    )
    mocker.patch(
        "iPhoto.gui.coordinators.edit_coordinator.sidecar.resolve_render_adjustments",
        return_value=render_adjustments,
    )

    EditCoordinator._restore_detail_video_preview(coordinator, source)

    video_area.load_video.assert_called_once_with(
        source,
        adjustments=render_adjustments,
        trim_range_ms=(1250, 4500),
        adjusted_preview=True,
    )
    video_area.play.assert_called_once_with()
