"""Tests for the init cover management in PlayerViewController.

The init cover is an opaque widget that hides uninitialised QRhiWidget
backing textures.  It must stay visible until the currently shown
QRhiWidget has rendered its first opaque frame, and must be re-shown
when switching to a QRhiWidget that has not yet rendered.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)
pytest.importorskip("PySide6.QtMultimedia", reason="QtMultimedia is required", exc_type=ImportError)

from unittest.mock import MagicMock, patch

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget, QWidget

from iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController
from iPhoto.gui.ui.widgets.detail_page import DetailPageWidget
from iPhoto.gui.ui.widgets.video_area import VideoArea
from iPhoto.people.repository import AssetFaceAnnotation


def _assert_ibeam_cursor() -> None:
    cursor = QApplication.overrideCursor()
    assert cursor is not None
    assert cursor.shape() == Qt.CursorShape.IBeamCursor


def _chip_margin_point(overlay, face_id: str = "face-1") -> QPointF:
    hover_rect = overlay._states[face_id].layout.hover_rect
    visual_rect = overlay._states[face_id].layout.chip_rect
    candidates = [
        QPointF(visual_rect.left() - 3.0, visual_rect.center().y()),
        QPointF(visual_rect.right() + 3.0, visual_rect.center().y()),
        QPointF(visual_rect.center().x(), visual_rect.top() - 3.0),
        QPointF(visual_rect.center().x(), visual_rect.bottom() + 3.0),
    ]
    for point in candidates:
        if hover_rect.contains(point) and not visual_rect.contains(point):
            return point
    return QPointF(hover_rect.left() + 1.0, hover_rect.center().y())


def _send_mouse_move_to_overlay_point(target: QWidget, overlay, point: QPointF) -> None:
    target_point = target.mapFromGlobal(overlay.mapToGlobal(point.toPoint()))
    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(target_point),
        QPointF(target_point),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(target, move_event)


class _FakeImageViewer(QWidget):
    """Minimal stand-in for GLImageViewer used by the test harness.

    Provides only the signals and methods that ``PlayerViewController``
    connects to during construction.
    """

    firstFrameReady = Signal()
    replayRequested = Signal()
    viewTransformChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._has_image_content = True
        self.setMouseTracking(True)

    def set_image(self, *args, **kwargs):
        pass

    def set_live_replay_enabled(self, enabled):
        pass

    def update(self):
        pass

    def has_image_content(self) -> bool:
        return self._has_image_content

    def image_rect_to_viewport(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        image_width: float | None = None,
        image_height: float | None = None,
    ) -> QRectF:
        del image_width, image_height
        return QRectF(float(x), float(y), float(width), float(height))


@pytest.fixture(scope="module")
def qapp():
    """Create QApplication instance for Qt tests."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    while QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()


@pytest.fixture
def controller(qapp):
    """Build a PlayerViewController with a fake image viewer."""
    parent_widget = QWidget()
    stack = QStackedWidget(parent_widget)
    placeholder = QLabel("placeholder")
    image_viewer = _FakeImageViewer()
    video_area = VideoArea()
    from iPhoto.gui.ui.widgets.live_badge import LiveBadge

    live_badge = LiveBadge(parent_widget)
    live_badge.hide()

    stack.addWidget(placeholder)
    stack.addWidget(image_viewer)
    stack.addWidget(video_area)

    pvc = PlayerViewController(
        player_stack=stack,
        image_viewer=image_viewer,
        video_area=video_area,
        placeholder=placeholder,
        live_badge=live_badge,
    )
    yield pvc


class TestInitCoverTracking:
    """Per-widget first-render tracking in PlayerViewController."""

    def test_initial_render_flags(self, controller):
        """Both render flags should start as False."""
        assert controller._image_viewer_rendered is False
        assert controller._video_renderer_rendered is False

    def test_still_decode_uses_dedicated_bounded_pool(self, controller):
        assert controller._pool is not QThreadPool.globalInstance()
        assert controller._pool.maxThreadCount() == 2

    def test_stale_decode_generation_is_not_applied(self, controller, mocker):
        apply_ready = mocker.patch.object(controller, "_on_adjusted_image_ready")
        controller._request_generation = 2

        controller._on_scheduled_image_ready(
            1,
            Path("/tmp/stale.jpg"),
            QImage(2, 2, QImage.Format.Format_RGBA8888),
            {},
        )

        apply_ready.assert_not_called()

    def test_oversized_full_decode_uses_safe_display_texture(self, controller, mocker):
        image = QImage(9000, 10, QImage.Format.Format_RGBA8888)
        controller._image_viewer.maximum_texture_size = lambda: 1024
        set_image = mocker.patch.object(controller._image_viewer, "set_image")

        controller._apply_still_frame(Path("/tmp/huge.jpg"), image, {})

        display_image = set_image.call_args.args[0]
        assert display_image.width() == 1024
        assert controller.current_full_image().width() == 9000

    def test_image_first_render_sets_flag(self, controller):
        """_on_image_first_render should mark image as rendered."""
        controller._on_image_first_render()
        assert controller._image_viewer_rendered is True

    def test_video_first_render_sets_flag(self, controller):
        """_on_video_first_render should mark video as rendered."""
        controller._on_video_first_render()
        assert controller._video_renderer_rendered is True

    def test_show_image_surface_shows_cover_if_not_rendered(self, controller, mocker):
        """show_image_surface should re-show the init cover when image hasn't rendered."""
        mock_show_cover = mocker.patch.object(controller, "_show_detail_init_cover")
        controller._image_viewer_rendered = False
        controller.show_image_surface()
        mock_show_cover.assert_called_once()

    def test_show_image_surface_skips_cover_if_rendered(self, controller, mocker):
        """show_image_surface should NOT re-show cover when image has already rendered."""
        mock_show_cover = mocker.patch.object(controller, "_show_detail_init_cover")
        controller._image_viewer_rendered = True
        controller.show_image_surface()
        mock_show_cover.assert_not_called()

    def test_show_video_surface_shows_cover_if_not_rendered(self, controller, mocker):
        """show_video_surface should re-show the init cover when video hasn't rendered."""
        mock_show_cover = mocker.patch.object(controller, "_show_detail_init_cover")
        controller._video_renderer_rendered = False
        controller.show_video_surface(interactive=True)
        mock_show_cover.assert_called_once()

    def test_show_video_surface_skips_cover_if_rendered(self, controller, mocker):
        """show_video_surface should NOT re-show cover when video has already rendered."""
        mock_show_cover = mocker.patch.object(controller, "_show_detail_init_cover")
        controller._video_renderer_rendered = True
        controller.show_video_surface(interactive=True)
        mock_show_cover.assert_not_called()

    def test_image_first_render_hides_cover_when_image_visible(self, controller, mocker):
        """_on_image_first_render should hide cover when image is current widget."""
        mock_hide = mocker.patch.object(controller, "_hide_detail_init_cover")
        controller._player_stack.setCurrentWidget(controller._image_viewer)
        controller._on_image_first_render()
        mock_hide.assert_called_once()

    def test_image_first_render_skips_hide_when_video_visible(self, controller, mocker):
        """_on_image_first_render should NOT hide cover when video is current widget."""
        mock_hide = mocker.patch.object(controller, "_hide_detail_init_cover")
        controller._player_stack.setCurrentWidget(controller._video_area)
        controller._on_image_first_render()
        mock_hide.assert_not_called()
        # But the flag should still be set
        assert controller._image_viewer_rendered is True

    def test_video_first_render_hides_cover_when_video_visible(self, controller, mocker):
        """_on_video_first_render should hide cover when video is current widget."""
        mock_hide = mocker.patch.object(controller, "_hide_detail_init_cover")
        controller._player_stack.setCurrentWidget(controller._video_area)
        controller._on_video_first_render()
        mock_hide.assert_called_once()

    def test_video_first_render_skips_hide_when_image_visible(self, controller, mocker):
        """_on_video_first_render should NOT hide cover when image is current widget."""
        mock_hide = mocker.patch.object(controller, "_hide_detail_init_cover")
        controller._player_stack.setCurrentWidget(controller._image_viewer)
        controller._on_video_first_render()
        mock_hide.assert_not_called()
        assert controller._video_renderer_rendered is True


def test_init_cover_stays_below_face_name_overlay_and_does_not_take_mouse(qapp):
    main_window = QWidget()
    image_viewer = _FakeImageViewer()
    detail = DetailPageWidget(main_window, image_viewer=image_viewer)
    detail.resize(640, 480)
    detail.show()
    detail.player_stack.setCurrentWidget(image_viewer)
    qapp.processEvents()

    detail.face_name_overlay.set_annotations(
        [
            AssetFaceAnnotation(
                face_id="face-1",
                person_id="person-1",
                display_name="Julie",
                box_x=80,
                box_y=80,
                box_w=120,
                box_h=90,
                image_width=420,
                image_height=320,
            )
        ]
    )
    detail.face_name_overlay.set_overlay_active(True)
    image_viewer.viewTransformChanged.emit()
    qapp.processEvents()

    assert detail.face_name_overlay._states["face-1"].layout.chip_rect.isEmpty() is False

    detail.show_rhi_init_cover()
    qapp.processEvents()

    assert detail._rhi_init_cover is not None
    assert detail._rhi_init_cover.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert detail.face_name_overlay.isVisible() is True

    margin_point = _chip_margin_point(detail.face_name_overlay)
    _send_mouse_move_to_overlay_point(detail._rhi_init_cover, detail.face_name_overlay, margin_point)
    qapp.processEvents()

    assert detail.face_name_overlay._hovered_face_id == "face-1"
    _assert_ibeam_cursor()

    detail.hide_rhi_init_cover()
    detail.show_rhi_init_cover()
    qapp.processEvents()

    assert detail._rhi_init_cover is not None
    assert detail._rhi_init_cover.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    _send_mouse_move_to_overlay_point(image_viewer, detail.face_name_overlay, margin_point)
    qapp.processEvents()
    assert detail.face_name_overlay._hovered_face_id == "face-1"
    _assert_ibeam_cursor()

    image_viewer.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
    detail.face_name_overlay.refresh_view_state()
    qapp.processEvents()

    assert not image_viewer.testAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)

    while QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()


class TestPlaceholderMessage:
    def test_show_placeholder_supports_custom_message(self, controller):
        controller.show_placeholder("Writing data, please wait...")

        assert controller._placeholder.text() == "Writing data, please wait..."

    def test_show_placeholder_restores_default_message(self, controller):
        controller.show_placeholder("Writing data, please wait...")

        controller.show_placeholder()

        assert controller._placeholder.text() == "placeholder"
