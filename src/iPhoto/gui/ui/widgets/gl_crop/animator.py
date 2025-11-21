"""
Animation controller for crop fade-out and fit-to-view transitions.

This module manages timers and animation interpolation without direct
knowledge of the view transform or crop state.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QPointF, QTimer

from .utils import ease_out_cubic


class CropAnimator:
    """Manages crop idle timeout and smooth fade-out animations."""

    def __init__(
        self,
        *,
        on_idle_timeout: Callable[[], None],
        on_animation_frame: Callable[[float, QPointF], None],
        on_animation_complete: Callable[[], None],
        timer_parent: QObject | None = None,
    ) -> None:
        """Initialize the crop animator.

        Parameters
        ----------
        on_idle_timeout:
            Callback when idle timer expires (should start animation).
        on_animation_frame:
            Callback for each animation frame with (scale, center).
        on_animation_complete:
            Callback when animation completes.
        timer_parent:
            Parent QObject for timers (optional).
        """
        self._on_idle_timeout = on_idle_timeout
        self._on_animation_frame = on_animation_frame
        self._on_animation_complete = on_animation_complete

        # Idle timer - triggers fade-out after inactivity
        self._idle_timer = QTimer(timer_parent)
        self._idle_timer.setInterval(1000)
        self._idle_timer.timeout.connect(self._handle_idle_timeout)

        # Animation timer - runs fade-out animation
        self._anim_timer = QTimer(timer_parent)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._handle_anim_tick)

        # Animation state
        self._anim_active: bool = False
        self._anim_start_time: float = 0.0
        self._anim_duration: float = 0.3
        self._anim_start_scale: float = 1.0
        self._anim_target_scale: float = 1.0
        self._anim_start_center = QPointF()
        self._anim_target_center = QPointF()

    def restart_idle(self) -> None:
        """Restart the idle timer."""
        self._idle_timer.start()

    def stop_idle(self) -> None:
        """Stop the idle timer."""
        self._idle_timer.stop()

    def is_animating(self) -> bool:
        """Return True if animation is currently running."""
        return self._anim_active

    def stop_animation(self) -> None:
        """Stop the current animation."""
        if self._anim_active:
            self._anim_active = False
            self._anim_timer.stop()

    def start_animation(
        self,
        start_scale: float,
        target_scale: float,
        start_center: QPointF,
        target_center: QPointF,
        duration: float = 0.3,
    ) -> None:
        """Start a new animation.

        Parameters
        ----------
        start_scale:
            Initial scale value.
        target_scale:
            Target scale value.
        start_center:
            Initial center position.
        target_center:
            Target center position.
        duration:
            Animation duration in seconds.
        """
        self._anim_active = True
        self._anim_start_time = time.monotonic()
        self._anim_duration = max(0.0, float(duration))
        self._anim_start_scale = float(start_scale)
        self._anim_target_scale = float(target_scale)
        self._anim_start_center = QPointF(start_center)
        self._anim_target_center = QPointF(target_center)
        self._anim_timer.start()

    def _handle_idle_timeout(self) -> None:
        """Handle idle timer timeout."""
        self._idle_timer.stop()
        self._on_idle_timeout()

    def _handle_anim_tick(self) -> None:
        """Handle animation timer tick."""
        if not self._anim_active:
            self._anim_timer.stop()
            return

        elapsed = time.monotonic() - self._anim_start_time
        if elapsed >= self._anim_duration:
            # Animation complete
            scale = self._anim_target_scale
            center = self._anim_target_center
            self._on_animation_frame(scale, QPointF(center))
            self._anim_active = False
            self._anim_timer.stop()
            self._on_animation_complete()
            return

        # Interpolate
        progress = max(0.0, min(1.0, elapsed / self._anim_duration))
        eased = ease_out_cubic(progress)
        scale = self._anim_start_scale + (
            (self._anim_target_scale - self._anim_start_scale) * eased
        )
        center_x = self._anim_start_center.x() + (
            (self._anim_target_center.x() - self._anim_start_center.x()) * eased
        )
        center_y = self._anim_start_center.y() + (
            (self._anim_target_center.y() - self._anim_start_center.y()) * eased
        )
        self._on_animation_frame(scale, QPointF(center_x, center_y))
