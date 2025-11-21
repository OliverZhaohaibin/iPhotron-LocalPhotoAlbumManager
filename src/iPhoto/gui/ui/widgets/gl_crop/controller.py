"""
Crop interaction controller (refactored as coordinator).

This module acts as the orchestrator, delegating to specialized modules
for hit testing, state management, animation, and interaction strategies.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent

from ..view_transform_controller import compute_fit_to_view_scale
from .animator import CropAnimator
from .hit_tester import HitTester
from .model import CropSessionModel
from .strategies import InteractionStrategy, PanStrategy, ResizeStrategy
from .utils import CropBoxState, CropHandle, cursor_for_handle, ease_in_quad

_LOGGER = logging.getLogger(__name__)


class CropInteractionController:
    """Manages all crop mode interactions, animations, and state (as coordinator)."""

    def __init__(
        self,
        *,
        texture_size_provider: Callable[[], tuple[int, int]],
        clamp_image_center_to_crop: Callable[[QPointF, float], QPointF],
        transform_controller,  # ViewTransformController
        on_crop_changed: Callable[[float, float, float, float], None],
        on_cursor_change: Callable[[Qt.CursorShape | None], None],
        on_request_update: Callable[[], None],
        timer_parent: QObject | None = None,
    ) -> None:
        """Initialize the crop interaction controller.

        Parameters
        ----------
        texture_size_provider:
            Callable that returns (width, height) of the current texture.
        clamp_image_center_to_crop:
            Callable to clamp image center to crop bounds.
        transform_controller:
            ViewTransformController instance for zoom/pan and coordinate transforms.
        on_crop_changed:
            Callback when crop values change, signature: (cx, cy, width, height).
        on_cursor_change:
            Callback to change cursor, signature: (cursor_shape or None to unset).
        on_request_update:
            Callback to request widget update/repaint.
        timer_parent:
            Parent QObject for timers (optional).
        """
        self._texture_size_provider = texture_size_provider
        self._clamp_image_center_to_crop = clamp_image_center_to_crop
        self._transform_controller = transform_controller
        self._on_crop_changed_callback = on_crop_changed
        self._on_cursor_change = on_cursor_change
        self._on_request_update = on_request_update

        # Core modules
        self._model = CropSessionModel()
        self._hit_tester = HitTester(hit_padding=12.0)
        self._animator = CropAnimator(
            on_idle_timeout=self._on_idle_timeout,
            on_animation_frame=self._on_animation_frame,
            on_animation_complete=self._on_animation_complete,
            timer_parent=timer_parent,
        )

        # Interaction state
        self._active: bool = False
        self._crop_drag_handle: CropHandle = CropHandle.NONE
        self._crop_dragging: bool = False
        self._crop_last_pos = QPointF()
        self._crop_edge_threshold: float = 48.0
        self._crop_faded_out: bool = False
        self._current_strategy: InteractionStrategy | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def is_active(self) -> bool:
        """Return True if crop mode is currently active."""
        return self._active

    def get_crop_values(self) -> dict[str, float]:
        """Return the current crop state as a mapping."""
        return self._model.get_crop_state().as_mapping()

    def get_crop_state(self) -> CropBoxState:
        """Return the current crop state object."""
        return self._model.get_crop_state()

    def is_faded_out(self) -> bool:
        """Return True if the crop overlay is currently faded out."""
        return self._crop_faded_out

    def start_perspective_interaction(self) -> None:
        """Snapshot the crop and reveal the translucent preview for sliders."""
        self._model.create_baseline()
        self._animator.stop_idle()
        self._animator.stop_animation()
        if self._active:
            self._crop_faded_out = False
            self._on_request_update()

    def end_perspective_interaction(self) -> None:
        """Clear the cached baseline crop once the slider interaction ends."""
        self._model.clear_baseline()
        self._animator.restart_idle()

    def update_perspective(
        self,
        vertical: float,
        horizontal: float,
        straighten: float = 0.0,
        rotate_steps: int = 0,
        flip_horizontal: bool = False,
    ) -> None:
        """Refresh the cached perspective quad and enforce crop constraints."""
        tex_w, tex_h = self._texture_size_provider()
        aspect_ratio = 1.0
        if tex_w > 0 and tex_h > 0:
            aspect_ratio = float(tex_w) / float(tex_h)

        # Track perspective quad changes separately from crop changes
        quad_changed = self._model.update_perspective(
            vertical, horizontal, straighten, rotate_steps, flip_horizontal, aspect_ratio
        )
        if not quad_changed:
            return

        # Update crop constraints and track if crop state changed
        crop_changed = False
        if self._model.has_baseline():
            crop_changed = self._model.apply_baseline_perspective_fit()
        else:
            crop_changed = self._model.ensure_crop_center_inside_quad()
            if not self._model.is_crop_inside_quad():
                crop_changed = self._model.auto_scale_crop_to_quad() or crop_changed

        # Emit crop changed signal if crop state was modified
        if crop_changed:
            self._model.get_crop_state().clamp()
            self._emit_crop_changed()

        # Request UI update if either quad or crop changed
        if quad_changed or crop_changed:
            self._on_request_update()

    def current_crop_rect_pixels(self) -> dict[str, float] | None:
        """Return the crop rectangle in viewport device pixels."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return None
        crop_state = self._model.get_crop_state()
        rect = crop_state.to_pixel_rect(tex_w, tex_h)
        top_left = self._transform_controller.convert_image_to_viewport(
            rect["left"], rect["top"]
        )
        bottom_right = self._transform_controller.convert_image_to_viewport(
            rect["right"], rect["bottom"]
        )
        dpr = self._transform_controller._get_dpr()
        return {
            "left": top_left.x() * dpr,
            "top": top_left.y() * dpr,
            "right": bottom_right.x() * dpr,
            "bottom": bottom_right.y() * dpr,
        }

    def set_active(self, enabled: bool, values: Mapping[str, float] | None = None) -> None:
        """Enable or disable crop mode with optional initial crop values."""
        if enabled == self._active:
            if enabled and values is not None:
                self._apply_crop_values(values)
            return

        self._active = bool(enabled)
        if not self._active:
            self._animator.stop_animation()
            self._animator.stop_idle()
            self._crop_drag_handle = CropHandle.NONE
            self._crop_dragging = False
            self._crop_faded_out = False
            self._model.clear_baseline()
            self._on_cursor_change(None)
            self._on_request_update()
            return

        # Load the stored crop rectangle when entering crop mode
        self._apply_crop_values(values)
        self._crop_faded_out = False
        self._crop_drag_handle = CropHandle.NONE
        self._crop_dragging = False
        self._animator.stop_animation()
        self._animator.restart_idle()
        self._on_request_update()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def handle_mouse_press(self, event: QMouseEvent) -> None:
        """Handle mouse press events in crop mode."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return

        self._animator.stop_animation()
        self._animator.stop_idle()
        self._crop_faded_out = False
        pos = event.position()
        handle = self._crop_hit_test(pos)

        if handle == CropHandle.NONE:
            self._crop_drag_handle = CropHandle.NONE
            self._crop_dragging = False
            self._on_cursor_change(Qt.CursorShape.ArrowCursor)
            return

        self._crop_drag_handle = handle
        self._crop_dragging = True
        self._crop_last_pos = QPointF(pos)

        # Create appropriate strategy
        if handle == CropHandle.INSIDE:
            self._current_strategy = PanStrategy(
                model=self._model,
                texture_size_provider=self._texture_size_provider,
                get_effective_scale=self._transform_controller.get_effective_scale,
                get_dpr=self._transform_controller._get_dpr,
                on_crop_changed=self._emit_crop_changed,
            )
            self._on_cursor_change(Qt.CursorShape.ClosedHandCursor)
        else:
            self._current_strategy = ResizeStrategy(
                handle=handle,
                model=self._model,
                texture_size_provider=self._texture_size_provider,
                get_effective_scale=self._transform_controller.get_effective_scale,
                get_dpr=self._transform_controller._get_dpr,
                on_crop_changed=self._emit_crop_changed,
                apply_edge_push_zoom=self._apply_edge_push_auto_zoom,
            )
            self._on_cursor_change(cursor_for_handle(handle))

        event.accept()

    def handle_mouse_move(self, event: QMouseEvent) -> None:
        """Handle mouse move events in crop mode."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return

        pos = event.position()
        if not self._crop_dragging:
            handle = self._crop_hit_test(pos)
            self._on_cursor_change(cursor_for_handle(handle))
            return

        previous_pos = QPointF(self._crop_last_pos)
        delta_view = pos - previous_pos
        self._crop_last_pos = QPointF(pos)
        self._crop_faded_out = False

        if self._current_strategy is not None:
            self._current_strategy.on_drag(delta_view)

        self._animator.restart_idle()
        self._on_request_update()

    def handle_mouse_release(self, event: QMouseEvent) -> None:
        """Handle mouse release events in crop mode."""
        del event  # unused
        if self._current_strategy is not None:
            self._current_strategy.on_end()
            self._current_strategy = None
        self._crop_dragging = False
        self._crop_drag_handle = CropHandle.NONE
        self._on_cursor_change(None)
        self._animator.restart_idle()

    def handle_wheel(self, event: QWheelEvent) -> None:
        """Handle wheel events in crop mode for zooming."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return

        self._animator.stop_animation()
        self._crop_faded_out = False
        self._animator.stop_idle()

        angle = event.angleDelta().y()
        if angle == 0:
            self._animator.restart_idle()
            return

        # Guard against devices that emit unusually large wheel deltas
        angle = max(-480, min(480, angle))

        factor = math.pow(1.0015, angle)
        if abs(factor - 1.0) <= 1e-6:
            self._animator.restart_idle()
            event.accept()
            return
        anchor_image = self._transform_controller.convert_viewport_to_image(event.position())
        anchor_norm_x = max(0.0, min(1.0, float(anchor_image.x()) / float(tex_w)))
        anchor_norm_y = max(0.0, min(1.0, float(anchor_image.y()) / float(tex_h)))

        snapshot = self._model.create_snapshot()
        crop_state = self._model.get_crop_state()
        crop_state.zoom_about_point(anchor_norm_x, anchor_norm_y, factor)
        if not self._model.ensure_valid_or_revert(snapshot, allow_shrink=False):
            self._on_request_update()
            self._animator.restart_idle()
            event.accept()
            return
        if self._model.has_changed(snapshot):
            self._emit_crop_changed()
        self._on_request_update()
        self._animator.restart_idle()
        event.accept()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _apply_crop_values(self, values: Mapping[str, float] | None) -> None:
        """Apply crop values to the crop state."""
        crop_state = self._model.get_crop_state()
        if values:
            crop_state.set_from_mapping(values)
        else:
            crop_state.set_full()

        changed = self._model.ensure_crop_center_inside_quad()
        if not self._model.is_crop_inside_quad():
            changed = self._model.auto_scale_crop_to_quad() or changed

        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return

        center = crop_state.center_pixels(tex_w, tex_h)
        scale = self._transform_controller.get_effective_scale()
        clamped_center = self._clamp_image_center_to_crop(center, scale)
        self._transform_controller.apply_image_center_pixels(clamped_center, scale)
        if changed:
            self._emit_crop_changed()

    def _crop_hit_test(self, point: QPointF) -> CropHandle:
        """Determine which crop handle (if any) is under the cursor."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return CropHandle.NONE

        crop_state = self._model.get_crop_state()
        rect = crop_state.to_pixel_rect(tex_w, tex_h)
        top_left = self._transform_controller.convert_image_to_viewport(
            rect["left"], rect["top"]
        )
        top_right = self._transform_controller.convert_image_to_viewport(
            rect["right"], rect["top"]
        )
        bottom_right = self._transform_controller.convert_image_to_viewport(
            rect["right"], rect["bottom"]
        )
        bottom_left = self._transform_controller.convert_image_to_viewport(
            rect["left"], rect["bottom"]
        )

        return self._hit_tester.test(point, top_left, top_right, bottom_right, bottom_left)

    def _emit_crop_changed(self) -> None:
        """Emit the crop changed signal."""
        state = self._model.get_crop_state()
        self._on_crop_changed_callback(
            float(state.cx), float(state.cy), float(state.width), float(state.height)
        )

    # ------------------------------------------------------------------
    # Animation callbacks
    # ------------------------------------------------------------------
    def _on_idle_timeout(self) -> None:
        """Handle idle timeout - start fade-out animation."""
        tex_w, tex_h = self._texture_size_provider()
        if not self._active or tex_w <= 0 or tex_h <= 0:
            return

        target_scale = self._target_scale_for_crop()
        crop_state = self._model.get_crop_state()
        target_center = crop_state.center_pixels(tex_w, tex_h)
        start_scale = self._transform_controller.get_effective_scale()
        start_center = self._transform_controller.get_image_center_pixels()

        self._animator.start_animation(
            start_scale, target_scale, start_center, target_center, duration=0.3
        )
        self._crop_faded_out = False

    def _on_animation_frame(self, scale: float, center: QPointF) -> None:
        """Handle animation frame update."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return

        vw, vh = self._transform_controller._get_view_dimensions_device_px()
        fit_w, fit_h = self._transform_controller._get_fit_texture_size()
        tex_size = (int(fit_w), int(fit_h))
        base_scale = compute_fit_to_view_scale(tex_size, vw, vh)
        min_zoom = self._transform_controller.minimum_zoom()
        max_zoom = self._transform_controller.maximum_zoom()
        zoom_factor = max(min_zoom, min(max_zoom, scale / max(base_scale, 1e-6)))
        self._transform_controller.set_zoom_factor_direct(zoom_factor)
        actual_scale = self._transform_controller.get_effective_scale()
        self._transform_controller.apply_image_center_pixels(center, actual_scale)
        self._on_request_update()

    def _on_animation_complete(self) -> None:
        """Handle animation completion."""
        self._crop_faded_out = True
        self._on_request_update()

    def _target_scale_for_crop(self) -> float:
        """Calculate the target scale for crop fade-out animation."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return self._transform_controller.get_effective_scale()

        vw, vh = self._transform_controller._get_view_dimensions_device_px()
        crop_state = self._model.get_crop_state()
        crop_rect = crop_state.to_pixel_rect(tex_w, tex_h)
        crop_width = max(1.0, crop_rect["right"] - crop_rect["left"])
        crop_height = max(1.0, crop_rect["bottom"] - crop_rect["top"])
        padding = 20.0 * self._transform_controller._get_dpr()
        available_w = max(1.0, vw - padding * 2.0)
        available_h = max(1.0, vh - padding * 2.0)
        scale_w = available_w / crop_width
        scale_h = available_h / crop_height
        target_scale = min(scale_w, scale_h)
        base_scale = compute_fit_to_view_scale((tex_w, tex_h), vw, vh)
        min_scale = base_scale * self._transform_controller.minimum_zoom()
        max_scale = base_scale * self._transform_controller.maximum_zoom()
        return max(min_scale, min(max_scale, target_scale))

    # ------------------------------------------------------------------
    # Edge-push auto zoom helpers
    # ------------------------------------------------------------------
    def _apply_edge_push_auto_zoom(self, delta_view: QPointF) -> None:
        """Shrink and pan automatically when a handle pushes against the viewport."""
        if self._crop_drag_handle in (CropHandle.NONE, CropHandle.INSIDE):
            return

        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return

        crop_rect = self.current_crop_rect_pixels()
        if not crop_rect:
            return

        vw, vh = self._transform_controller._get_view_dimensions_device_px()
        if vw <= 0.0 or vh <= 0.0:
            return

        dpr = self._transform_controller._get_dpr()
        threshold = max(1.0, self._crop_edge_threshold * dpr)
        view_scale = self._transform_controller.get_effective_scale()
        if view_scale <= 1e-6:
            return

        delta_device = QPointF(float(delta_view.x()) * dpr, float(delta_view.y()) * dpr)
        if abs(delta_device.x()) < 1e-6 and abs(delta_device.y()) < 1e-6:
            return

        delta_image = QPointF(
            float(delta_device.x()) / view_scale,
            float(delta_device.y()) / view_scale,
        )

        pressure = 0.0
        offset_x = 0.0
        offset_y = 0.0
        handle = self._crop_drag_handle

        left_margin = float(crop_rect["left"])
        right_margin = max(0.0, vw - float(crop_rect["right"]))
        top_margin = float(crop_rect["top"])
        bottom_margin = max(0.0, vh - float(crop_rect["bottom"]))

        if handle in (CropHandle.LEFT, CropHandle.TOP_LEFT, CropHandle.BOTTOM_LEFT):
            if delta_device.x() < 0.0 and left_margin < threshold:
                p = (threshold - left_margin) / threshold
                pressure = max(pressure, p)
                offset_x = max(offset_x, -float(delta_image.x()) * p)

        if handle in (CropHandle.RIGHT, CropHandle.TOP_RIGHT, CropHandle.BOTTOM_RIGHT):
            if delta_device.x() > 0.0 and right_margin < threshold:
                p = (threshold - right_margin) / threshold
                pressure = max(pressure, p)
                offset_x = min(offset_x, -float(delta_image.x()) * p)

        if handle in (CropHandle.TOP, CropHandle.TOP_LEFT, CropHandle.TOP_RIGHT):
            if delta_device.y() < 0.0 and top_margin < threshold:
                p = (threshold - top_margin) / threshold
                pressure = max(pressure, p)
                offset_y = max(offset_y, -float(delta_image.y()) * p)

        if handle in (CropHandle.BOTTOM, CropHandle.BOTTOM_LEFT, CropHandle.BOTTOM_RIGHT):
            if delta_device.y() > 0.0 and bottom_margin < threshold:
                p = (threshold - bottom_margin) / threshold
                pressure = max(pressure, p)
                offset_y = min(offset_y, -float(delta_image.y()) * p)

        if pressure <= 0.0:
            return

        eased_pressure = ease_in_quad(min(1.0, pressure))

        texture_size = (tex_w, tex_h)
        base_scale = compute_fit_to_view_scale(texture_size, vw, vh)
        min_scale = max(base_scale, 1e-6)
        max_scale = base_scale * self._transform_controller.maximum_zoom()

        shrink_strength = 0.05
        new_scale_raw = view_scale * (1.0 - shrink_strength * eased_pressure)
        new_scale = max(min_scale, min(max_scale, new_scale_raw))

        crop_state = self._model.get_crop_state()
        crop_center = crop_state.center_pixels(tex_w, tex_h)
        crop_center_view = self._transform_controller.convert_image_to_viewport(
            crop_center.x(), crop_center.y()
        )
        base_scale_safe = max(base_scale, 1e-6)
        target_zoom = new_scale / base_scale_safe
        self._transform_controller.set_zoom(target_zoom, anchor=crop_center_view)

        pan_gain = 0.75 + 0.25 * eased_pressure
        offset_delta = QPointF(offset_x * pan_gain, offset_y * pan_gain)
        if abs(offset_delta.x()) < 1e-6 and abs(offset_delta.y()) < 1e-6:
            return

        current_center = self._transform_controller.get_image_center_pixels()
        target_center = QPointF(
            current_center.x() + offset_delta.x(),
            current_center.y() + offset_delta.y(),
        )
        effective_scale = self._transform_controller.get_effective_scale()
        clamped_center = self._clamp_image_center_to_crop(target_center, effective_scale)
        self._transform_controller.apply_image_center_pixels(clamped_center, effective_scale)
