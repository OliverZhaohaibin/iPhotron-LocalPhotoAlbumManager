from __future__ import annotations

from collections.abc import Mapping

import pytest
from PySide6.QtCore import QPointF, QRectF, QSize
from PySide6.QtGui import QImage

from iPhoto.gui.ui.widgets.gl_image_viewer import GLImageViewer


def _make_cropped_viewer(
    adjustments: Mapping[str, float],
    *,
    logical_size: tuple[int, int] = (600, 400),
    initial_target: tuple[int, int] = (1920, 1080),
) -> GLImageViewer:
    viewer = GLImageViewer()
    viewer.resize(*logical_size)
    image = QImage(400, 300, QImage.Format.Format_RGBA8888)
    image.fill(0xFF202020)
    viewer._image = image
    viewer._adjustments = dict(adjustments)
    viewer._last_render_target_size = QSize(*initial_target)
    viewer._last_layout_target_size = QSize(*initial_target)
    viewer.reset_zoom()
    return viewer


def _publish_target_and_sync(
    viewer: GLImageViewer,
    target: tuple[int, int],
) -> None:
    size = QSize(*target)
    viewer._last_render_target_size = size
    viewer._sync_view_transform_for_render_target(size)


def _crop_viewport_rect(viewer: GLImageViewer) -> QRectF:
    crop_rect = viewer._compute_crop_rect_pixels()
    assert crop_rect is not None
    controller = viewer._transform_controller
    top_left = controller.convert_image_to_viewport(
        crop_rect.left(), crop_rect.top()
    )
    bottom_right = controller.convert_image_to_viewport(
        crop_rect.right(), crop_rect.bottom()
    )
    return QRectF(top_left, bottom_right).normalized()


@pytest.mark.parametrize(
    "adjustments",
    [
        {"Crop_CX": 0.65, "Crop_CY": 0.5, "Crop_W": 0.7, "Crop_H": 1.0},
        {"Crop_CX": 0.5, "Crop_CY": 0.65, "Crop_W": 1.0, "Crop_H": 0.7},
        {"Crop_CX": 0.68, "Crop_CY": 0.63, "Crop_W": 0.55, "Crop_H": 0.6},
        {"Crop_CX": 0.30, "Crop_CY": 0.35, "Crop_W": 0.6, "Crop_H": 0.7},
        {"Crop_CX": 0.50, "Crop_CY": 0.50, "Crop_W": 0.6, "Crop_H": 0.7},
    ],
    ids=["left", "top", "left-top", "right-bottom", "symmetric"],
)
def test_render_target_sync_recenters_crop_after_stale_fullscreen_layout(
    qapp,
    adjustments: Mapping[str, float],
) -> None:
    viewer = _make_cropped_viewer(adjustments)
    normal_target = (1200, 800)
    viewer._last_render_target_size = QSize(*normal_target)

    stale_rect = _crop_viewport_rect(viewer)
    expected_center = QPointF(viewer.width() / 2.0, viewer.height() / 2.0)
    if adjustments["Crop_CX"] != 0.5 or adjustments["Crop_CY"] != 0.5:
        assert (
            abs(stale_rect.center().x() - expected_center.x()) > 1.0
            or abs(stale_rect.center().y() - expected_center.y()) > 1.0
        )

    viewer._sync_view_transform_for_render_target(QSize(*normal_target))

    restored_rect = _crop_viewport_rect(viewer)
    assert restored_rect.center().x() == pytest.approx(expected_center.x(), abs=1.0)
    assert restored_rect.center().y() == pytest.approx(expected_center.y(), abs=1.0)
    assert restored_rect.width() <= viewer.width() + 1.0
    assert restored_rect.height() <= viewer.height() + 1.0
    assert (
        restored_rect.width() == pytest.approx(viewer.width(), abs=1.0)
        or restored_rect.height() == pytest.approx(viewer.height(), abs=1.0)
    )


def test_uncropped_media_keeps_full_frame_fit_after_target_change(qapp) -> None:
    viewer = _make_cropped_viewer(
        {"Crop_CX": 0.5, "Crop_CY": 0.5, "Crop_W": 1.0, "Crop_H": 1.0}
    )

    _publish_target_and_sync(viewer, (1200, 800))

    controller = viewer._transform_controller
    top_left = controller.convert_image_to_viewport(0.0, 0.0)
    bottom_right = controller.convert_image_to_viewport(400.0, 300.0)
    image_rect = QRectF(top_left, bottom_right).normalized()
    assert viewer._auto_crop_view_locked is False
    assert image_rect.center().x() == pytest.approx(300.0, abs=1.0)
    assert image_rect.center().y() == pytest.approx(200.0, abs=1.0)
    assert image_rect.width() <= viewer.width() + 1.0
    assert image_rect.height() <= viewer.height() + 1.0


def test_playback_crop_center_lock_reflows_against_restored_target(qapp) -> None:
    viewer = _make_cropped_viewer(
        {"Crop_CX": 0.70, "Crop_CY": 0.62, "Crop_W": 0.55, "Crop_H": 0.58}
    )
    viewer.set_crop_framing_enabled(False)
    viewer.reset_zoom()
    assert viewer._auto_crop_center_locked is True

    _publish_target_and_sync(viewer, (1200, 800))

    restored_rect = _crop_viewport_rect(viewer)
    assert restored_rect.center().x() == pytest.approx(300.0, abs=1.0)
    assert restored_rect.center().y() == pytest.approx(200.0, abs=1.0)


def test_render_target_sync_handles_rotation_and_high_dpi_target(qapp) -> None:
    viewer = _make_cropped_viewer(
        {
            "Crop_CX": 0.72,
            "Crop_CY": 0.34,
            "Crop_W": 0.5,
            "Crop_H": 0.6,
            "Crop_Rotate90": 1.0,
        },
        logical_size=(600, 400),
    )

    _publish_target_and_sync(viewer, (1200, 800))

    restored_rect = _crop_viewport_rect(viewer)
    assert restored_rect.center().x() == pytest.approx(300.0, abs=1.0)
    assert restored_rect.center().y() == pytest.approx(200.0, abs=1.0)


def test_repeated_fullscreen_round_trips_do_not_accumulate_crop_drift(qapp) -> None:
    viewer = _make_cropped_viewer(
        {"Crop_CX": 0.70, "Crop_CY": 0.62, "Crop_W": 0.55, "Crop_H": 0.58}
    )
    normal_target = (1200, 800)
    fullscreen_target = (1920, 1080)
    restored_rects: list[QRectF] = []

    for _ in range(10):
        _publish_target_and_sync(viewer, fullscreen_target)
        _publish_target_and_sync(viewer, normal_target)
        restored_rects.append(_crop_viewport_rect(viewer))

    baseline = restored_rects[0]
    for restored in restored_rects[1:]:
        assert restored.center().x() == pytest.approx(baseline.center().x(), abs=1.0)
        assert restored.center().y() == pytest.approx(baseline.center().y(), abs=1.0)
        assert restored.width() == pytest.approx(baseline.width(), abs=1.0)
        assert restored.height() == pytest.approx(baseline.height(), abs=1.0)


def test_viewport_sync_preserves_manual_transform_without_auto_crop_lock(qapp) -> None:
    viewer = _make_cropped_viewer(
        {"Crop_CX": 0.70, "Crop_CY": 0.60, "Crop_W": 0.5, "Crop_H": 0.6}
    )
    viewer._cancel_auto_crop_lock()
    viewer._transform_controller.set_zoom_factor_direct(2.25)
    viewer._transform_controller.set_pan_pixels(QPointF(37.0, -19.0))

    _publish_target_and_sync(viewer, (1200, 800))

    assert viewer._transform_controller.get_zoom_factor() == pytest.approx(2.25)
    assert viewer._transform_controller.get_pan_pixels() == QPointF(37.0, -19.0)


def test_fullscreen_exit_reset_restores_crop_fit_after_lock_was_cleared(qapp) -> None:
    viewer = _make_cropped_viewer(
        {"Crop_CX": 0.70, "Crop_CY": 0.62, "Crop_W": 0.55, "Crop_H": 0.58}
    )
    viewer._cancel_auto_crop_lock()
    viewer._transform_controller.set_zoom_factor_direct(2.25)
    viewer._transform_controller.set_pan_pixels(QPointF(37.0, -19.0))

    viewer.request_viewport_relayout(reset_view=True)
    # A later resize event may add a preserving request, but must not downgrade
    # the fullscreen-exit reset before the new render target is available.
    viewer.request_viewport_relayout()
    _publish_target_and_sync(viewer, (1200, 800))

    restored_rect = _crop_viewport_rect(viewer)
    assert viewer._auto_crop_view_locked is True
    assert restored_rect.center().x() == pytest.approx(300.0, abs=1.0)
    assert restored_rect.center().y() == pytest.approx(200.0, abs=1.0)
    assert restored_rect.width() <= viewer.width() + 1.0
    assert restored_rect.height() <= viewer.height() + 1.0
    assert (
        restored_rect.width() == pytest.approx(viewer.width(), abs=1.0)
        or restored_rect.height() == pytest.approx(viewer.height(), abs=1.0)
    )


def test_identical_target_is_coalesced_until_relayout_is_requested(qapp, mocker) -> None:
    viewer = _make_cropped_viewer(
        {"Crop_CX": 0.70, "Crop_CY": 0.60, "Crop_W": 0.5, "Crop_H": 0.6}
    )
    target = QSize(1200, 800)
    viewer._last_render_target_size = target
    reapply = mocker.patch.object(viewer, "_reapply_locked_crop_view")

    viewer._sync_view_transform_for_render_target(target)
    viewer._sync_view_transform_for_render_target(target)
    assert reapply.call_count == 1

    viewer.request_viewport_relayout()
    viewer._sync_view_transform_for_render_target(target)
    assert reapply.call_count == 2
