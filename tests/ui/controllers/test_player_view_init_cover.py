"""Tests for the init cover management in PlayerViewController.

The init cover is an opaque widget that hides uninitialised QRhiWidget
backing textures.  It must stay visible until the currently shown
QRhiWidget has rendered its first opaque frame, and must be re-shown
when switching to a QRhiWidget that has not yet rendered.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")
pytest.importorskip("PySide6.QtMultimedia", reason="QtMultimedia is required")

from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget, QWidget

from iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController
from iPhoto.gui.ui.widgets.video_area import VideoArea


class _FakeImageViewer(QWidget):
    """Minimal stand-in for GLImageViewer used by the test harness.

    Provides only the signals and methods that ``PlayerViewController``
    connects to during construction.
    """

    firstFrameReady = Signal()
    replayRequested = Signal()

    def set_image(self, *args, **kwargs):
        pass

    def set_live_replay_enabled(self, enabled):
        pass

    def update(self):
        pass


@pytest.fixture
def qapp():
    """Create QApplication instance for Qt tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


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
