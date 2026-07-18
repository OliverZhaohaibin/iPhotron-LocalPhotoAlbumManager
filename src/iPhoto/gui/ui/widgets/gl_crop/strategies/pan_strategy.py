"""
Pan/move strategy for crop box interaction.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF

from ..model import CropSessionModel
from .abstract import InteractionStrategy


class PanStrategy(InteractionStrategy):
    """Move the image beneath a screen-stationary crop box."""

    def __init__(
        self,
        *,
        model: CropSessionModel,
        texture_size_provider: Callable[[], tuple[int, int]],
        get_effective_scale: Callable[[], float],
        get_dpr: Callable[[], float],
        get_pan_pixels: Callable[[], QPointF],
        set_pan_pixels: Callable[[QPointF], None],
        on_crop_changed: Callable[[], None],
        get_viewport_device_scale: Callable[[], tuple[float, float]] | None = None,
    ) -> None:
        """Initialize pan strategy.

        Parameters
        ----------
        model:
            Crop session model.
        texture_size_provider:
            Callable that returns (width, height) of the current texture.
        get_effective_scale:
            Callable that returns the current effective scale.
        get_dpr:
            Callable that returns the device pixel ratio.
        get_pan_pixels, set_pan_pixels:
            Accessors for the shared image view transform.
        on_crop_changed:
            Callback when the image movement changes the persisted crop.
        """
        self._model = model
        self._texture_size_provider = texture_size_provider
        self._get_effective_scale = get_effective_scale
        self._get_dpr = get_dpr
        self._get_pan_pixels = get_pan_pixels
        self._set_pan_pixels = set_pan_pixels
        self._get_viewport_device_scale = get_viewport_device_scale
        self._on_crop_changed = on_crop_changed

    def on_drag(self, delta_view: QPointF) -> None:
        """Handle pan drag movement."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return

        view_scale = self._get_effective_scale()
        if view_scale <= 1e-6:
            return

        if self._get_viewport_device_scale is not None:
            scale_x, scale_y = self._get_viewport_device_scale()
        else:
            scale_x = scale_y = self._get_dpr()
        delta_device_x = float(delta_view.x()) * scale_x
        delta_device_y = float(delta_view.y()) * scale_y
        snapshot = self._model.create_snapshot()
        crop_state = self._model.get_crop_state()
        # The image follows the pointer while the crop frame remains fixed on
        # screen.  Persist the equivalent inverse translation in image space,
        # then compensate it in the view transform so the overlay does not
        # visually move.
        crop_state.translate_pixels(
            QPointF(-delta_device_x / view_scale, -delta_device_y / view_scale),
            (tex_w, tex_h),
            self._model.get_crop_bounds(),
        )
        if not self._model.ensure_valid_or_revert(snapshot, allow_shrink=False):
            return
        if self._model.has_changed(snapshot):
            old_cx, old_cy, _, _ = snapshot
            delta_crop_x = (float(crop_state.cx) - old_cx) * tex_w
            delta_crop_y = (float(crop_state.cy) - old_cy) * tex_h
            current_pan = self._get_pan_pixels()
            self._set_pan_pixels(
                QPointF(
                    current_pan.x() - delta_crop_x * view_scale,
                    current_pan.y() + delta_crop_y * view_scale,
                )
            )
            self._on_crop_changed()

    def on_end(self) -> None:
        """Handle end of pan interaction."""
        # No special cleanup needed
