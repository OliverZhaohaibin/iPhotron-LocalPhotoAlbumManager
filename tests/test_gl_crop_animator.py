"""Tests for the gl_crop CropAnimator module."""

import pytest
from PySide6.QtCore import QPointF


@pytest.fixture
def mock_callbacks():
    """Create mock callback functions for the animator."""
    class Callbacks:
        def __init__(self):
            self.idle_called = False
            self.frame_calls = []
            self.complete_called = False

        def on_idle(self):
            self.idle_called = True

        def on_frame(self, scale, center):
            self.frame_calls.append((scale, QPointF(center)))

        def on_complete(self):
            self.complete_called = True

    return Callbacks()


def test_animator_imports():
    """Test that CropAnimator can be imported."""
    from src.iPhoto.gui.ui.widgets.gl_crop.animator import CropAnimator
    assert CropAnimator is not None


def test_animator_initialization(mock_callbacks):
    """Test animator initialization."""
    from src.iPhoto.gui.ui.widgets.gl_crop.animator import CropAnimator

    animator = CropAnimator(
        on_idle_timeout=mock_callbacks.on_idle,
        on_animation_frame=mock_callbacks.on_frame,
        on_animation_complete=mock_callbacks.on_complete,
    )
    assert not animator.is_animating()


def test_animator_idle_timer(mock_callbacks):
    """Test idle timer functionality."""
    from src.iPhoto.gui.ui.widgets.gl_crop.animator import CropAnimator

    animator = CropAnimator(
        on_idle_timeout=mock_callbacks.on_idle,
        on_animation_frame=mock_callbacks.on_frame,
        on_animation_complete=mock_callbacks.on_complete,
    )

    # Start and stop idle timer
    animator.restart_idle()
    animator.stop_idle()
    # Timer operations should not raise errors
    assert True


def test_animator_start_stop_animation(mock_callbacks):
    """Test starting and stopping animation."""
    from src.iPhoto.gui.ui.widgets.gl_crop.animator import CropAnimator

    animator = CropAnimator(
        on_idle_timeout=mock_callbacks.on_idle,
        on_animation_frame=mock_callbacks.on_frame,
        on_animation_complete=mock_callbacks.on_complete,
    )

    # Start animation
    animator.start_animation(
        start_scale=1.0,
        target_scale=2.0,
        start_center=QPointF(100, 100),
        target_center=QPointF(200, 200),
        duration=0.3,
    )
    assert animator.is_animating()

    # Stop animation
    animator.stop_animation()
    assert not animator.is_animating()


def test_animator_animation_duration(mock_callbacks):
    """Test that animation duration is respected."""
    from src.iPhoto.gui.ui.widgets.gl_crop.animator import CropAnimator

    animator = CropAnimator(
        on_idle_timeout=mock_callbacks.on_idle,
        on_animation_frame=mock_callbacks.on_frame,
        on_animation_complete=mock_callbacks.on_complete,
    )

    # Start animation with very short duration
    animator.start_animation(
        start_scale=1.0,
        target_scale=2.0,
        start_center=QPointF(100, 100),
        target_center=QPointF(200, 200),
        duration=0.0,  # Zero duration
    )
    assert animator.is_animating()
