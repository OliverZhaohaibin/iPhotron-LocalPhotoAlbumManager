from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for preview controller tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtCore", reason="QtCore is required for preview controller tests", exc_type=ImportError)

from PySide6.QtCore import QPoint, QRect

from iPhoto.application.ports import EditRenderingState
from iPhoto.gui.ui.controllers.preview_controller import PreviewController
from iPhoto.gui.ui.models.roles import Roles


def _make_index(data_by_role: dict[int, object]) -> Mock:
    index = Mock()
    index.isValid.return_value = True
    index.data.side_effect = lambda role: data_by_role.get(role)
    return index


def _make_view(rect: QRect) -> Mock:
    viewport = Mock()
    viewport.mapToGlobal.side_effect = lambda point: QPoint(point)
    view = Mock()
    view.visualRect.return_value = rect
    view.viewport.return_value = viewport
    return view


def test_request_preview_passes_adjustments_and_trim_to_preview_window() -> None:
    preview_window = Mock()
    edit_service = Mock()
    edit_service.describe_adjustments.return_value = EditRenderingState(
        sidecar_exists=True,
        raw_adjustments={"Exposure": 0.5},
        resolved_adjustments={"Exposure": 0.5},
        adjusted_preview=True,
        has_visible_edits=True,
        trim_range_ms=(1000, 4000),
        effective_duration_sec=3.0,
    )
    controller = PreviewController(preview_window, edit_service_getter=lambda: edit_service)
    preview_path = Path("D:/fake/video.mp4")
    view = _make_view(QRect(10, 20, 120, 90))
    index = _make_index(
        {
            Roles.IS_VIDEO: True,
            Roles.IS_LIVE: False,
            Roles.ABS: str(preview_path),
            Roles.INFO: {"dur": 5.0, "w": 1920, "h": 1080},
        }
    )

    controller._handle_request_preview(view, index)

    preview_window.show_preview.assert_called_once()
    args, kwargs = preview_window.show_preview.call_args
    assert args[0] == preview_path
    assert args[1] == QRect(QPoint(10, 20), view.visualRect.return_value.size())
    assert kwargs["aspect_ratio_hint"] == pytest.approx(1920 / 1080)
    assert kwargs["adjustments"] == {"Exposure": 0.5}
    assert kwargs["trim_range_ms"] == (1000, 4000)
    assert kwargs["adjusted_preview"] is True


def test_request_preview_falls_back_to_raw_video_without_edit_service() -> None:
    preview_window = Mock()
    controller = PreviewController(preview_window)
    preview_path = Path("D:/fake/video.mp4")
    view = _make_view(QRect(0, 0, 80, 60))
    index = _make_index(
        {
            Roles.IS_VIDEO: True,
            Roles.IS_LIVE: False,
            Roles.ABS: str(preview_path),
            Roles.INFO: {"w": 1280, "h": 720},
        }
    )

    controller._handle_request_preview(view, index)

    preview_window.show_preview.assert_called_once()
    args, kwargs = preview_window.show_preview.call_args
    assert args[0] == preview_path
    assert args[1] == QRect(QPoint(0, 0), view.visualRect.return_value.size())
    assert kwargs["aspect_ratio_hint"] == pytest.approx(1280 / 720)
    assert kwargs["adjustments"] is None
    assert kwargs["trim_range_ms"] is None
    assert kwargs["adjusted_preview"] is False


def test_request_preview_falls_back_to_raw_video_when_edit_lookup_fails() -> None:
    preview_window = Mock()
    edit_service = Mock()
    edit_service.describe_adjustments.side_effect = OSError("broken sidecar")
    controller = PreviewController(preview_window, edit_service_getter=lambda: edit_service)
    preview_path = Path("D:/fake/video.mp4")
    view = _make_view(QRect(0, 0, 80, 60))
    index = _make_index(
        {
            Roles.IS_VIDEO: True,
            Roles.IS_LIVE: False,
            Roles.ABS: str(preview_path),
            Roles.INFO: {"dur": 5.0, "w": 1280, "h": 720},
        }
    )

    controller._handle_request_preview(view, index)

    preview_window.show_preview.assert_called_once()
    args, kwargs = preview_window.show_preview.call_args
    assert args[0] == preview_path
    assert kwargs["adjustments"] is None
    assert kwargs["trim_range_ms"] is None
    assert kwargs["adjusted_preview"] is False


def test_live_preview_uses_motion_video_but_loads_adjustments_from_still_asset() -> None:
    preview_window = Mock()
    edit_service = Mock()
    edit_service.describe_adjustments.return_value = EditRenderingState(
        sidecar_exists=True,
        raw_adjustments={"Exposure": 0.25},
        resolved_adjustments={"Exposure": 0.25},
        adjusted_preview=True,
        has_visible_edits=True,
        trim_range_ms=None,
        effective_duration_sec=3.0,
    )
    controller = PreviewController(preview_window, edit_service_getter=lambda: edit_service)
    still_path = Path("D:/fake/live_still.jpg")
    motion_path = Path("D:/fake/live_motion.mov")
    view = _make_view(QRect(5, 6, 70, 50))
    index = _make_index(
        {
            Roles.IS_VIDEO: False,
            Roles.IS_LIVE: True,
            Roles.ABS: str(still_path),
            Roles.LIVE_MOTION_ABS: str(motion_path),
            Roles.INFO: {"dur": 3.0, "w": 1440, "h": 1080},
        }
    )

    controller._handle_request_preview(view, index)

    edit_service.describe_adjustments.assert_called_once()
    preview_window.show_preview.assert_called_once()
    args, kwargs = preview_window.show_preview.call_args
    assert args[0] == motion_path
    assert kwargs["adjustments"] == {"Exposure": 0.25}
    assert kwargs["adjusted_preview"] is True


def test_macos_request_preview_routes_rotate_only_edits_through_adjusted_popup() -> None:
    preview_window = Mock()
    edit_service = Mock()
    edit_service.describe_adjustments.return_value = EditRenderingState(
        sidecar_exists=True,
        raw_adjustments={"Crop_Rotate90": 3.0},
        resolved_adjustments={"Crop_Rotate90": 3.0},
        adjusted_preview=False,
        has_visible_edits=True,
        trim_range_ms=None,
        effective_duration_sec=5.0,
    )
    controller = PreviewController(preview_window, edit_service_getter=lambda: edit_service)
    preview_path = Path("/fake/video.mov")
    view = _make_view(QRect(0, 0, 80, 60))
    index = _make_index(
        {
            Roles.IS_VIDEO: True,
            Roles.IS_LIVE: False,
            Roles.ABS: str(preview_path),
            Roles.INFO: {"dur": 5.0, "w": 1080, "h": 1920},
        }
    )

    with patch(
        "iPhoto.gui.ui.controllers.preview_controller.sys.platform",
        "darwin",
    ):
        controller._handle_request_preview(view, index)

    preview_window.show_preview.assert_called_once()
    _, kwargs = preview_window.show_preview.call_args
    assert kwargs["adjustments"] == {"Crop_Rotate90": 3.0}
    assert kwargs["adjusted_preview"] is True
