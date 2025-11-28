"""
Resize strategy for crop box edge/corner dragging.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF

from ..model import CropSessionModel
from ..utils import CropHandle
from .abstract import InteractionStrategy


class ResizeStrategy(InteractionStrategy):
    """Strategy for resizing crop box via edge/corner dragging."""

    def __init__(
        self,
        *,
        handle: CropHandle,
        model: CropSessionModel,
        texture_size_provider: Callable[[], tuple[int, int]],
        get_effective_scale: Callable[[], float],
        get_dpr: Callable[[], float],
        on_crop_changed: Callable[[], None],
        apply_edge_push_zoom: Callable[[QPointF], None],
    ) -> None:
        """Initialize resize strategy.

        Parameters
        ----------
        handle:
            The crop handle being dragged.
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
        apply_edge_push_zoom:
            Callback to apply edge-push auto-zoom.
        """
        self._handle = handle
        self._model = model
        self._texture_size_provider = texture_size_provider
        self._get_effective_scale = get_effective_scale
        self._get_dpr = get_dpr
        self._on_crop_changed = on_crop_changed
        self._apply_edge_push_zoom = apply_edge_push_zoom

    def on_drag(self, delta_view: QPointF) -> None:
        """Handle resize drag movement."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return

        view_scale = self._get_effective_scale()
        if view_scale <= 1e-6:
            return

        snapshot = self._model.create_snapshot()
        dpr = self._get_dpr()
        delta_world = QPointF(
            float(delta_view.x()) * dpr / view_scale,
            -float(delta_view.y()) * dpr / view_scale,
        )

        # Crop box definition must be constrained by the original
        # texture bounds (scale=1, offset=0), not by the view transform state
        half_width_orig = tex_w * 0.5
        half_height_orig = tex_h * 0.5
        img_bounds_world = {
            "left": -half_width_orig,
            "right": half_width_orig,
            "bottom": -half_height_orig,
            "top": half_height_orig,
        }

        # Convert the current crop rectangle into world coordinates
        crop_state = self._model.get_crop_state()
        crop_rect_px = crop_state.to_pixel_rect(tex_w, tex_h)
        crop_world = {
            "left": crop_rect_px["left"] - half_width_orig,
            "right": crop_rect_px["right"] - half_width_orig,
            "top": half_height_orig - crop_rect_px["top"],
            "bottom": half_height_orig - crop_rect_px["bottom"],
        }

        # Minimum crop dimensions
        min_width_px = max(1.0, crop_state.min_width * tex_w)
        min_height_px = max(1.0, crop_state.min_height * tex_h)

        # Apply delta to appropriate edges
        # We rely on ensure_valid_or_revert() at the end and shader boundary detection
        # to prevent black borders. Intermediate validation was causing mismatches.
        texture_handle = self._handle
        delta_x, delta_y = delta_world.x(), delta_world.y()

        if texture_handle in (CropHandle.LEFT, CropHandle.TOP_LEFT, CropHandle.BOTTOM_LEFT):
            new_left = crop_world["left"] + delta_x
            new_left = min(new_left, crop_world["right"] - min_width_px)
            crop_world["left"] = new_left

        if texture_handle in (CropHandle.RIGHT, CropHandle.TOP_RIGHT, CropHandle.BOTTOM_RIGHT):
            new_right = crop_world["right"] + delta_x
            new_right = max(new_right, crop_world["left"] + min_width_px)
            crop_world["right"] = new_right

        if texture_handle in (CropHandle.BOTTOM, CropHandle.BOTTOM_LEFT, CropHandle.BOTTOM_RIGHT):
            new_bottom = crop_world["bottom"] + delta_y
            new_bottom = min(new_bottom, crop_world["top"] - min_height_px)
            crop_world["bottom"] = new_bottom

        if texture_handle in (CropHandle.TOP, CropHandle.TOP_LEFT, CropHandle.TOP_RIGHT):
            new_top = crop_world["top"] + delta_y
            new_top = max(new_top, crop_world["bottom"] + min_height_px)
            crop_world["top"] = new_top

        # Convert back to normalised coordinates
        new_px_left = crop_world["left"] + half_width_orig
        new_px_right = crop_world["right"] + half_width_orig
        new_px_top = half_height_orig - crop_world["top"]
        new_px_bottom = half_height_orig - crop_world["bottom"]

        new_width = new_px_right - new_px_left
        new_height = new_px_bottom - new_px_top
        crop_state.cx = (new_px_left + new_px_right) * 0.5 / tex_w
        crop_state.cy = (new_px_top + new_px_bottom) * 0.5 / tex_h
        crop_state.width = new_width / tex_w
        crop_state.height = new_height / tex_h
        crop_state.clamp()

        if not self._model.ensure_valid_or_revert(snapshot, allow_shrink=False):
            return
        self._on_crop_changed()
        self._apply_edge_push_zoom(delta_view)

    def on_end(self) -> None:
        """Handle end of resize interaction."""
        # No special cleanup needed
