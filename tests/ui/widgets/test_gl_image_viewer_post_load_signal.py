from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GL image viewer tests")

import os
from unittest.mock import Mock

from PySide6.QtCore import QPointF, QSize
from PySide6.QtGui import QImage
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.gl_image_viewer import GLImageViewer


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_gl_image_viewer_queues_one_post_load_view_transform(qapp) -> None:
    viewer = GLImageViewer()
    viewer._pending_post_load_view_transform = True

    spy = QSignalSpy(viewer.viewTransformChanged)
    viewer._schedule_post_load_view_transform()
    qapp.processEvents()

    assert spy.count() == 1
    assert viewer._pending_post_load_view_transform is False
    assert viewer._post_load_view_transform_scheduled is False


def test_gl_image_viewer_maps_image_geometry_before_texture_upload(qapp) -> None:
    viewer = GLImageViewer()
    viewer.resize(420, 320)
    image = QImage(420, 320, QImage.Format.Format_ARGB32)
    image.fill(0xFF000000)

    viewer.set_image(image, {}, image_source="startup-still")
    qapp.processEvents()

    renderer = getattr(viewer, "_renderer", None)
    assert renderer is None or not renderer.has_texture()

    rect = viewer.image_rect_to_viewport(
        100,
        80,
        120,
        90,
        image_width=420,
        image_height=320,
    )
    assert rect.isEmpty() is False
    assert rect.left() == pytest.approx(100.0)
    assert rect.top() == pytest.approx(80.0)
    assert rect.width() == pytest.approx(120.0)
    assert rect.height() == pytest.approx(90.0)

    image_point = viewer.viewport_to_image(
        QPointF(210.0, 160.0),
        image_width=420,
        image_height=320,
    )
    assert image_point.x() == pytest.approx(210.0)
    assert image_point.y() == pytest.approx(160.0)


def test_rhi_render_without_pending_upload_has_defined_presentation_flags() -> None:
    """Regression: an idle Metal render must not read an unbound local."""

    viewer = Mock()
    viewer._gl_initialized = True
    viewer._renderer.has_texture.return_value = True
    viewer._using_video_frame_source = False
    viewer._video_frame_dirty = False
    viewer._video_frame_presentation_pending = False
    viewer._video_frame = None
    viewer._pending_video_image = None
    viewer._image = None
    viewer._texture_manager.needs_texture_upload.return_value = False
    viewer.renderTarget.return_value.pixelSize.return_value = QSize(64, 64)
    viewer._transform_controller.get_effective_scale.return_value = 1.0
    viewer._transform_controller.get_image_cover_scale.return_value = 1.0
    viewer._transform_controller.get_pan_pixels.return_value = QPointF()
    viewer._crop_controller.is_active.return_value = False
    viewer._display_adjustments.return_value = {}
    viewer._display_texture_dimensions.return_value = (64, 64)
    viewer._transparent_rounded_clip_enabled = False
    viewer._rounded_clip_radius = 0.0
    viewer._time_base = 0.0
    viewer.devicePixelRatioF.return_value = 1.0

    GLImageViewer._render_rhi(viewer, Mock())

    viewer._renderer.render.assert_called_once()
    viewer.videoFramePresented.emit.assert_not_called()


def test_rhi_render_presents_video_uploaded_before_render() -> None:
    """A Linux-style immediate upload is acknowledged by the next GPU draw."""

    viewer = Mock()
    viewer._gl_initialized = True
    viewer._renderer.has_texture.return_value = True
    viewer._using_video_frame_source = True
    viewer._video_frame_dirty = False
    viewer._video_frame_presentation_pending = True
    viewer._video_frame = None
    viewer._pending_video_image = None
    viewer._image = None
    viewer._texture_manager.needs_texture_upload.return_value = False
    viewer.renderTarget.return_value.pixelSize.return_value = QSize(64, 64)
    viewer._transform_controller.get_effective_scale.return_value = 1.0
    viewer._transform_controller.get_image_cover_scale.return_value = 1.0
    viewer._transform_controller.get_pan_pixels.return_value = QPointF()
    viewer._crop_controller.is_active.return_value = False
    viewer._display_adjustments.return_value = {}
    viewer._display_texture_dimensions.return_value = (64, 64)
    viewer._transparent_rounded_clip_enabled = False
    viewer._rounded_clip_radius = 0.0
    viewer._time_base = 0.0
    viewer.devicePixelRatioF.return_value = 1.0

    GLImageViewer._render_rhi(viewer, Mock())

    viewer._renderer.render.assert_called_once()
    viewer.videoFramePresented.emit.assert_called_once()
    assert viewer._video_frame_presentation_pending is False
