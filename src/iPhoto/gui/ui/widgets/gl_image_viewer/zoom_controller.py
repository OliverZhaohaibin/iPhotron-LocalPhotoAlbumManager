"""
Coordinate transformation helpers for the GL image viewer.

Wraps the low-level :class:`ViewTransformController` and provides
convenience methods that check renderer readiness before delegating.
"""

from __future__ import annotations

from typing import Callable, Tuple

from PySide6.QtCore import QPointF

from ..view_transform_controller import compute_fit_to_view_scale
from .view_helpers import clamp_center_to_texture_bounds


class ZoomController:
    """Coordinate-transform utilities composed into the viewer widget.

    Parameters
    ----------
    transform_controller:
        The underlying :class:`ViewTransformController`.
    renderer_provider:
        Callable returning the current ``GLRenderer`` (or ``None``).
    display_texture_dimensions:
        Callable returning ``(width, height)`` in logical pixels.
    """

    def __init__(
        self,
        transform_controller,
        renderer_provider: Callable,
        display_texture_dimensions: Callable[[], Tuple[int, int]],
    ) -> None:
        self._tc = transform_controller
        self._renderer_provider = renderer_provider
        self._display_texture_dimensions = display_texture_dimensions

    # ------------------------------------------------------------------

    def _has_texture(self) -> bool:
        r = self._renderer_provider()
        return bool(r and r.has_texture())

    # ------------------------------------------------------------------
    # Delegating helpers
    # ------------------------------------------------------------------

    def view_dimensions_device_px(self) -> tuple[float, float]:
        return self._tc._get_view_dimensions_device_px()

    def screen_to_world(self, screen_pt: QPointF) -> QPointF:
        """Map a Qt screen coordinate to the GL view's centre-origin space."""
        return self._tc.convert_screen_to_world(screen_pt)

    def world_to_screen(self, world_vec: QPointF) -> QPointF:
        """Convert a GL centre-origin vector into a Qt screen coordinate."""
        return self._tc.convert_world_to_screen(world_vec)

    def effective_scale(self) -> float:
        if not self._has_texture():
            return 1.0
        return self._tc.get_effective_scale()

    def image_center_pixels(self) -> QPointF:
        if not self._has_texture():
            return QPointF(0.0, 0.0)
        return self._tc.get_image_center_pixels()

    def set_image_center_pixels(
        self, center: QPointF, *, scale: float | None = None
    ) -> None:
        if not self._has_texture():
            return
        self._tc.apply_image_center_pixels(center, scale)

    def image_to_viewport(self, x: float, y: float) -> QPointF:
        if not self._has_texture():
            return QPointF()
        return self._tc.convert_image_to_viewport(x, y)

    def viewport_to_image(self, point: QPointF) -> QPointF:
        if not self._has_texture():
            return QPointF()
        return self._tc.convert_viewport_to_image(point)

    def create_clamp_function(self):
        """Return a clamp callable suitable for the crop controller."""
        def clamp_fn(center: QPointF, scale: float) -> QPointF:
            return clamp_center_to_texture_bounds(
                center=center,
                scale=scale,
                texture_dimensions=self._display_texture_dimensions(),
                view_dimensions=self.view_dimensions_device_px(),
                has_texture=self._has_texture(),
            )
        return clamp_fn

    def fit_to_view_scale(self, view_width: float, view_height: float) -> float:
        """Return the baseline scale that fits the texture within the viewport."""
        texture_size = self._display_texture_dimensions()
        return compute_fit_to_view_scale(texture_size, view_width, view_height)
