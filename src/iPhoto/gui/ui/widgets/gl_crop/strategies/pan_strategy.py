"""
Pan/move strategy for crop box interaction.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF

from ..model import CropSessionModel
from .abstract import InteractionStrategy


class PanStrategy(InteractionStrategy):
    """Strategy for panning/moving the entire crop box."""

    def __init__(
        self,
        *,
        model: CropSessionModel,
        texture_size_provider: Callable[[], tuple[int, int]],
        get_effective_scale: Callable[[], float],
        get_dpr: Callable[[], float],
        on_crop_changed: Callable[[], None],
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
        on_crop_changed:
            Callback when crop values change.
        """
        self._model = model
        self._texture_size_provider = texture_size_provider
        self._get_effective_scale = get_effective_scale
        self._get_dpr = get_dpr
        self._on_crop_changed = on_crop_changed

    def on_drag(self, delta_view: QPointF) -> None:
        """Handle pan drag movement."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return

        view_scale = self._get_effective_scale()
        if view_scale <= 1e-6:
            return

        dpr = self._get_dpr()
        delta_device_x = float(delta_view.x()) * dpr
        delta_device_y = float(delta_view.y()) * dpr
        delta_image = QPointF(delta_device_x / view_scale, delta_device_y / view_scale)

        snapshot = self._model.create_snapshot()
        crop_state = self._model.get_crop_state()
        crop_state.translate_pixels(delta_image, (tex_w, tex_h))
        if not self._model.ensure_valid_or_revert(snapshot, allow_shrink=False):
            return
        if self._model.has_changed(snapshot):
            self._on_crop_changed()

    def on_end(self) -> None:
        """Handle end of pan interaction."""
        # No special cleanup needed
