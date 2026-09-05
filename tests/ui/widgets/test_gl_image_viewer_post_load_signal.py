from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GL image viewer tests")

import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QPointF, QSize
from PySide6.QtGui import QImage
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QGridLayout, QWidget

from iPhoto.gui.ui.widgets.gl_image_viewer import GLImageViewer
from iPhoto.gui.ui.widgets.gl_image_viewer.widget import _crop_preview_adjustments


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


def test_presentation_transition_is_generation_bound_and_preserves_texture(qapp) -> None:
    viewer = GLImageViewer()
    image = QImage(32, 24, QImage.Format.Format_RGBA8888)
    image.fill(0xFFFF0000)
    viewer.set_image(image, {}, image_source="old-still")

    viewer.begin_presentation_transition(8)

    assert viewer.current_image_source() == "old-still"
    assert viewer.has_image_content() is True
    assert viewer.complete_presentation_transition(7) is False
    assert viewer._presentation_suppressed_generation == 8
    assert viewer.complete_presentation_transition(8) is True
    assert viewer.current_image_source() == "old-still"


def test_raw_gl_suppression_clears_without_drawing_or_mutating_residency() -> None:
    viewer = Mock()
    viewer._uses_raw_gl = True
    viewer._gl_initialized = True
    viewer._gl_funcs = Mock()
    viewer._presentation_suppressed_generation = 9
    viewer._renderer.has_texture.return_value = True
    viewer._pending_resident_activation = "new-still"
    viewer._pending_warm_surfaces = ["neighbor"]
    target = Mock()
    target.pixelSize.return_value = QSize(320, 240)
    viewer.renderTarget.return_value = target
    command_buffer = Mock()

    GLImageViewer.render(viewer, command_buffer)

    command_buffer.beginPass.assert_called_once()
    command_buffer.beginExternal.assert_not_called()
    viewer._renderer.render.assert_not_called()
    viewer._texture_manager.activate_resident_texture.assert_not_called()
    viewer._texture_manager.needs_texture_upload.assert_not_called()
    assert viewer._pending_resident_activation == "new-still"
    assert viewer._pending_warm_surfaces == ["neighbor"]
    assert viewer._rendered_content_identity is None


def test_rhi_suppression_clears_without_drawing_or_consuming_new_surface() -> None:
    viewer = Mock()
    viewer._gl_initialized = True
    viewer._presentation_suppressed_generation = 10
    viewer._pending_resident_activation = "new-still"
    viewer._pending_warm_surfaces = ["neighbor"]
    target = Mock()
    target.pixelSize.return_value = QSize(320, 240)
    viewer.renderTarget.return_value = target
    command_buffer = Mock()

    GLImageViewer._render_rhi(viewer, command_buffer)

    command_buffer.beginPass.assert_called_once()
    viewer._renderer.render.assert_not_called()
    viewer._texture_manager.activate_resident_texture.assert_not_called()
    viewer._texture_manager.needs_texture_upload.assert_not_called()
    assert viewer._pending_resident_activation == "new-still"
    assert viewer._pending_warm_surfaces == ["neighbor"]
    assert viewer._rendered_content_identity is None


@pytest.mark.gpu
@pytest.mark.windows_compositor
def test_visible_windows_transition_never_exposes_previous_still(qapp) -> None:
    """Validate transition pixels on a real visible Windows QRhi compositor."""

    if sys.platform != "win32":
        pytest.skip("requires a visible Windows compositor integration runner")
    if QApplication.platformName().lower() in {"offscreen", "minimal"}:
        pytest.skip("requires a visible platform QRhi compositor")

    host = QWidget()
    layout = QGridLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    viewer = GLImageViewer(host)
    layout.addWidget(viewer, 0, 0)
    host.resize(320, 180)
    host.show()
    qapp.processEvents()

    def wait_for(spy: QSignalSpy, count: int) -> None:
        deadline = time.monotonic() + 5.0
        while spy.count() < count and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert spy.count() >= count

    def center_pixel():
        screen = qapp.primaryScreen()
        assert screen is not None
        image = screen.grabWindow(int(host.winId())).toImage()
        assert not image.isNull()
        return image.pixelColor(image.width() // 2, image.height() // 2)

    submitted = QSignalSpy(viewer.stillFrameSubmitted)
    composed = QSignalSpy(viewer.frameSubmitted)
    red = QImage(320, 180, QImage.Format.Format_RGBA8888)
    red.fill(0xFFFF0000)
    viewer._still_generation_by_key["red"] = 1
    viewer.set_image(red, {}, image_source="red")
    viewer.update()
    wait_for(submitted, 1)
    red_pixel = center_pixel()
    assert red_pixel.red() > red_pixel.blue()
    cycles = max(
        1,
        min(100, int(os.environ.get("IPHOTO_WINDOWS_COMPOSITOR_CYCLES", "1"))),
    )
    for index in range(cycles):
        generation = index + 2
        viewer.begin_presentation_transition(generation)
        composed_before = composed.count()
        viewer.update()
        wait_for(composed, composed_before + 1)
        transition_pixel = center_pixel()
        expected_background = viewer._transition_clear_color()
        assert transition_pixel.alpha() == 255
        assert abs(transition_pixel.red() - expected_background.red()) <= 20
        assert abs(transition_pixel.green() - expected_background.green()) <= 20
        assert abs(transition_pixel.blue() - expected_background.blue()) <= 20

        next_is_blue = index % 2 == 0
        color = 0xFF0000FF if next_is_blue else 0xFFFF0000
        source = f"transition-{generation}"
        image = QImage(320, 180, QImage.Format.Format_RGBA8888)
        image.fill(color)
        viewer._still_generation_by_key[source] = generation
        viewer.set_image(image, {}, image_source=source)
        assert viewer.complete_presentation_transition(generation)
        viewer.update()
        wait_for(submitted, index + 2)
        presented_pixel = center_pixel()
        if next_is_blue:
            assert presented_pixel.blue() > presented_pixel.red()
        else:
            assert presented_pixel.red() > presented_pixel.blue()
    host.close()


def test_still_surface_retains_transaction_generation_until_gpu_upload(qapp) -> None:
    viewer = GLImageViewer()
    image = QImage(32, 24, QImage.Format.Format_RGBA8888)
    image.fill(0xFF123456)
    surface = SimpleNamespace(
        image=image,
        decode_key="asset-7-lod-1024",
        source_size=(3200, 2400),
    )

    viewer.set_still_surface(surface, {}, generation=7)

    assert viewer._still_generation_by_key[surface.decode_key] == 7


def test_gl_image_viewer_maps_full_resolution_face_box_onto_viewport_surface(qapp) -> None:
    """Face annotations stay anchored when Detail displays a lower-resolution LOD."""

    viewer = GLImageViewer()
    viewer.resize(600, 400)
    surface = QImage(600, 400, QImage.Format.Format_RGBA8888)
    surface.fill(0xFF000000)

    viewer.set_image(
        surface,
        {},
        image_source="viewport-surface",
        source_size=(6000, 4000),
    )
    qapp.processEvents()

    rect = viewer.image_rect_to_viewport(
        1000,
        800,
        1200,
        900,
        image_width=6000,
        image_height=4000,
    )
    assert rect.left() == pytest.approx(100.0)
    assert rect.top() == pytest.approx(80.0)
    assert rect.width() == pytest.approx(120.0)
    assert rect.height() == pytest.approx(90.0)

    image_point = viewer.viewport_to_image(
        QPointF(220.0, 170.0),
        image_width=6000,
        image_height=4000,
    )
    assert image_point.x() == pytest.approx(2200.0)
    assert image_point.y() == pytest.approx(1700.0)

    implicit_source_point = viewer.viewport_to_image(QPointF(220.0, 170.0))
    assert implicit_source_point.x() == pytest.approx(2200.0)
    assert implicit_source_point.y() == pytest.approx(1700.0)


def test_rhi_render_without_pending_upload_has_defined_presentation_flags() -> None:
    """Regression: an idle Metal render must not read an unbound local."""

    viewer = Mock()
    viewer._presentation_suppressed_generation = None
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


def test_crop_preview_disables_persisted_crop_mask() -> None:
    """The yellow overlay, not a hidden unit-square mask, owns Crop preview bounds."""

    persisted = {
        "Crop_CX": 0.3,
        "Crop_CY": 0.4,
        "Crop_W": 0.5,
        "Crop_H": 0.6,
        "Straighten": 22.0,
    }

    preview = _crop_preview_adjustments(persisted)

    assert preview["Crop_CX"] == 0.5
    assert preview["Crop_CY"] == 0.5
    assert preview["Crop_W"] > 1.0
    assert preview["Crop_H"] > 1.0
    assert preview["Straighten"] == 22.0
    assert persisted["Crop_W"] == 0.5


def test_rhi_render_presents_video_uploaded_before_render() -> None:
    """A video draw is acknowledged only after window-frame submission."""

    viewer = Mock()
    viewer._presentation_suppressed_generation = None
    viewer._gl_initialized = True
    viewer._renderer.has_texture.return_value = True
    viewer._using_video_frame_source = True
    viewer._video_frame_dirty = False
    viewer._still_presentation_pending = False
    viewer._video_frame_presentation_pending = True
    viewer._video_frame_content_generation = 6
    viewer._video_frame_content_serial = 12
    viewer._content_revision = 0
    viewer._rendered_content_identity = None
    viewer._last_composed_content_identity = None
    viewer._take_pending_content_submission = lambda: (
        GLImageViewer._take_pending_content_submission(viewer)
    )
    viewer._first_render_submission_pending = False
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

    with patch(
        "iPhoto.gui.ui.widgets.gl_image_viewer.widget.geometry",
        SimpleNamespace(logical_crop_mapping_from_texture=lambda values: values),
    ):
        GLImageViewer._render_rhi(viewer, Mock())

    viewer._renderer.render.assert_called_once()
    viewer.videoFramePresented.emit.assert_not_called()
    assert viewer._video_frame_presentation_pending is False
    assert viewer._rendered_content_identity == ("video", 6, 12, 1)

    GLImageViewer._on_frame_submitted(viewer)

    viewer.videoFramePresented.emit.assert_called_once_with(6, 12)
    assert viewer._last_composed_content_identity == ("video", 6, 12, 1)


def test_still_submission_carries_content_identity_and_generation() -> None:
    viewer = Mock()
    viewer._first_render_submission_pending = False
    viewer._content_revision = 0
    viewer._rendered_content_identity = None
    viewer._last_composed_content_identity = None
    viewer._still_presentation_pending = True
    viewer._video_frame_presentation_pending = False
    viewer._texture_manager.get_current_image_source.return_value = "image-b-key"
    viewer._still_generation_by_key = {"image-b-key": 22}

    submission = GLImageViewer._take_pending_content_submission(viewer)
    viewer._rendered_content_identity = submission

    viewer.stillFrameSubmitted.emit.assert_not_called()
    viewer.stillFramePresented.emit.assert_not_called()

    GLImageViewer._on_frame_submitted(viewer)

    viewer.stillFrameSubmitted.emit.assert_called_once_with("image-b-key", 22)
    viewer.stillFramePresented.emit.assert_called_once_with("image-b-key")


def test_empty_compositions_cannot_delay_later_video_content() -> None:
    viewer = Mock()
    viewer._first_render_submission_pending = False
    viewer._rendered_content_identity = None
    viewer._last_composed_content_identity = None

    for _ in range(20):
        GLImageViewer._on_frame_submitted(viewer)
    viewer.videoFramePresented.emit.assert_not_called()

    viewer._rendered_content_identity = ("video", 9, 14, 21)
    GLImageViewer._on_frame_submitted(viewer)
    viewer.videoFramePresented.emit.assert_called_once_with(9, 14)


def test_replayed_gl_video_serial_gets_new_composition_acknowledgement() -> None:
    viewer = Mock()
    viewer._first_render_submission_pending = False
    viewer._last_composed_content_identity = ("video", 9, 14, 1)
    viewer._rendered_content_identity = ("video", 9, 14, 2)

    GLImageViewer._on_frame_submitted(viewer)

    viewer.videoFramePresented.emit.assert_called_once_with(9, 14)


def test_rhi_first_texture_failure_is_reported_before_no_texture_return() -> None:
    """A failed first allocation must trigger LOD fallback without an old texture."""

    viewer = Mock()
    viewer._presentation_suppressed_generation = None
    viewer._gl_initialized = True
    viewer._using_video_frame_source = False
    viewer._video_frame_dirty = False
    viewer._video_frame = None
    viewer._pending_video_image = None
    viewer._image = None
    viewer._pending_resident_activation = None
    viewer._pending_warm_surfaces = []
    viewer._still_presentation_pending = True
    viewer._still_generation_by_key = {"first-still": 12}
    viewer._texture_manager.needs_texture_upload.return_value = False
    viewer._renderer.has_texture.return_value = False
    viewer._renderer.take_still_upload_result.return_value = {
        "key": "first-still",
        "activate": True,
        "success": False,
        "reason": "create_failed",
    }
    viewer.renderTarget.return_value.pixelSize.return_value = QSize(64, 64)
    viewer._consume_still_upload_result = lambda: (
        GLImageViewer._consume_still_upload_result(viewer)
    )

    command_buffer = Mock()
    GLImageViewer._render_rhi(viewer, command_buffer)

    viewer.stillTextureAllocationFailed.emit.assert_called_once_with(
        "first-still",
        12,
        "create_failed",
    )
    assert viewer._still_presentation_pending is False
    viewer._renderer.render.assert_not_called()


def test_windows_gl_first_texture_failure_is_reported_before_no_texture_return(
    mocker,
) -> None:
    """The Windows raw-GL path must also report a failed first allocation."""

    viewer = Mock()
    viewer._presentation_suppressed_generation = None
    viewer._uses_raw_gl = True
    viewer._gl_initialized = True
    viewer._using_video_frame_source = False
    viewer._video_frame_dirty = False
    viewer._video_frame = None
    viewer._pending_video_image = None
    viewer._image = None
    viewer._pending_resident_activation = None
    viewer._pending_warm_surfaces = []
    viewer._still_presentation_pending = True
    viewer._still_generation_by_key = {"first-gl-still": 15}
    viewer._texture_manager.needs_texture_upload.return_value = False
    viewer._renderer.has_texture.return_value = False
    viewer._renderer.take_still_upload_result.return_value = {
        "key": "first-gl-still",
        "activate": True,
        "success": False,
        "reason": "upload_failed",
    }
    viewer.renderTarget.return_value.pixelSize.return_value = QSize(64, 64)
    viewer._gl_clear_rgba.return_value = (0.0, 0.0, 0.0, 1.0)
    viewer._consume_still_upload_result = lambda: (
        GLImageViewer._consume_still_upload_result(viewer)
    )
    mocker.patch(
        "iPhoto.gui.ui.widgets.gl_image_viewer.widget._load_gl_module",
        return_value=SimpleNamespace(GL_COLOR_BUFFER_BIT=0x4000),
    )

    GLImageViewer.render(viewer, Mock())

    viewer.stillTextureAllocationFailed.emit.assert_called_once_with(
        "first-gl-still",
        15,
        "upload_failed",
    )
    assert viewer._still_presentation_pending is False
    viewer._renderer.render.assert_not_called()
