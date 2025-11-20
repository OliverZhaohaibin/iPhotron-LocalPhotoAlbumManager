"""
Crop interaction controller for the GL image viewer.

This module encapsulates all crop-related interaction logic, state management,
and animation handling, keeping the main viewer focused on coordination.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping

from PySide6.QtCore import QPointF, Qt, QTimer, QObject
from PySide6.QtGui import QMouseEvent, QWheelEvent

from .gl_crop_utils import (
    CropBoxState,
    CropHandle,
    cursor_for_handle,
    ease_in_quad,
    ease_out_cubic,
)
from .view_transform_controller import compute_fit_to_view_scale
from .perspective_math import (
    NormalisedRect,
    build_perspective_matrix,
    calculate_min_zoom_to_fit,
    compute_projected_quad,
    point_in_convex_polygon,
    quad_centroid,
    rect_inside_quad,
    unit_quad,
)

_LOGGER = logging.getLogger(__name__)


class CropInteractionController:
    """Manages all crop mode interactions, animations, and state."""

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
        self._on_crop_changed = on_crop_changed
        self._on_cursor_change = on_cursor_change
        self._on_request_update = on_request_update

        # Crop state
        self._active: bool = False
        self._crop_state = CropBoxState()
        self._crop_drag_handle: CropHandle = CropHandle.NONE
        self._crop_dragging: bool = False
        self._crop_last_pos = QPointF()
        self._crop_hit_padding: float = 12.0
        self._crop_edge_threshold: float = 48.0

        # Animation state
        self._crop_idle_timer = QTimer(timer_parent)
        self._crop_idle_timer.setInterval(1000)
        self._crop_idle_timer.timeout.connect(self._on_crop_idle_timeout)
        self._crop_anim_timer = QTimer(timer_parent)
        self._crop_anim_timer.setInterval(16)
        self._crop_anim_timer.timeout.connect(self._on_crop_anim_tick)
        self._crop_anim_active: bool = False
        self._crop_anim_start_time: float = 0.0
        self._crop_anim_duration: float = 0.3
        self._crop_anim_start_scale: float = 1.0
        self._crop_anim_target_scale: float = 1.0
        self._crop_anim_start_center = QPointF()
        self._crop_anim_target_center = QPointF()
        self._crop_faded_out: bool = False
        self._perspective_vertical: float = 0.0
        self._perspective_horizontal: float = 0.0
        self._straighten_degrees: float = 0.0
        self._rotate_steps: int = 0
        self._flip_horizontal: bool = False
        self._perspective_quad: list[tuple[float, float]] = unit_quad()
        self._baseline_crop_state: tuple[float, float, float, float] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def is_active(self) -> bool:
        """Return True if crop mode is currently active."""
        return self._active

    def get_crop_values(self) -> dict[str, float]:
        """Return the current crop state as a mapping."""
        return self._crop_state.as_mapping()

    def get_crop_state(self) -> CropBoxState:
        """Return the current crop state object."""
        return self._crop_state

    def is_faded_out(self) -> bool:
        """Return True if the crop overlay is currently faded out."""
        return self._crop_faded_out

    def start_perspective_interaction(self) -> None:
        """Snapshot the crop and reveal the translucent preview for sliders."""

        # Cache the pre-interaction rectangle so perspective drags always reference
        # the untouched crop, allowing the box to expand back to its original size
        # when distortion is reduced.
        self._baseline_crop_state = self._snapshot_crop_state()

        # Perspective sliders share the same immersive preview rules as direct crop
        # drags: halt the pending fade-out, stop any running fit-to-view animation,
        # and force the overlay to render in its semi-transparent state.
        self._stop_crop_idle()
        self._stop_crop_animation()
        if self._active:
            self._crop_faded_out = False
            self._on_request_update()

    def end_perspective_interaction(self) -> None:
        """Clear the cached baseline crop once the slider interaction ends."""

        self._baseline_crop_state = None
        # Restart the idle timer so the controller keeps the translucent preview
        # visible for one second before replaying the fade-to-black animation and
        # the automatic fit-to-view transition.
        self._restart_crop_idle()

    def update_perspective(
        self,
        vertical: float,
        horizontal: float,
        straighten: float = 0.0,
        rotate_steps: int = 0,
        flip_horizontal: bool = False,
    ) -> None:
        """Refresh the cached perspective quad and enforce crop constraints."""

        new_vertical = float(vertical)
        new_horizontal = float(horizontal)
        new_straighten = float(straighten)
        new_rotate = int(rotate_steps)
        new_flip = bool(flip_horizontal)
        if (
            abs(new_vertical - self._perspective_vertical) <= 1e-6
            and abs(new_horizontal - self._perspective_horizontal) <= 1e-6
            and abs(new_straighten - self._straighten_degrees) <= 1e-6
            and new_rotate == self._rotate_steps
            and new_flip is self._flip_horizontal
            and self._baseline_crop_state is None
        ):
            return

        tex_w, tex_h = self._texture_size_provider()
        aspect_ratio = 1.0
        if tex_w > 0 and tex_h > 0:
            aspect_ratio = float(tex_w) / float(tex_h)

        self._perspective_vertical = new_vertical
        self._perspective_horizontal = new_horizontal
        self._straighten_degrees = new_straighten
        self._rotate_steps = new_rotate
        self._flip_horizontal = new_flip

        # When the viewer swaps texture width/height for 90°/270° rotations we
        # keep rotate_steps=0 while building the matrix to avoid a secondary
        # rotation in the shader. This coordinate system swap implicitly flips
        # the sign of rotation and perspective controls, so we need to adjust
        # the values up front to preserve a consistent, view-based experience.
        calc_straighten = new_straighten
        calc_vertical = new_vertical
        calc_horizontal = new_horizontal
        if new_rotate % 2 != 0:
            # Odd rotate steps mean the logical axes are swapped; negate values
            # so that the Straighten slider still performs a pure rotation (no
            # unintended shear) and vertical/horizontal perspective sliders
            # continue to lean in the same visual direction as the unrotated
            # view.
            calc_straighten = -new_straighten
            calc_vertical = -new_vertical
            calc_horizontal = -new_horizontal
        # GLImageViewer already swaps texture dimensions for 90°/270° rotations,
        # so the aspect_ratio here reflects the rotated display space. Passing a
        # non-zero rotate_steps would rotate the perspective quad a second time
        # and incorrectly shrink the crop box; therefore we force zero rotation
        # when building the matrix while still caching _rotate_steps for drag
        # handle mapping.
        matrix = build_perspective_matrix(
            calc_vertical,
            calc_horizontal,
            image_aspect_ratio=aspect_ratio,
            straighten_degrees=calc_straighten,
            rotate_steps=0,
            flip_horizontal=new_flip,
        )
        self._perspective_quad = compute_projected_quad(matrix)

        if self._baseline_crop_state is not None:
            changed = self._apply_baseline_perspective_fit()
        else:
            changed = self._ensure_crop_center_inside_quad()
            if not self._is_crop_inside_perspective_quad():
                changed = self._auto_scale_crop_to_quad() or changed

        if changed:
            self._crop_state.clamp()
            self._emit_crop_changed()
            self._on_request_update()

    def current_crop_rect_pixels(self) -> dict[str, float] | None:
        """Return the crop rectangle in viewport device pixels."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return None
        rect = self._crop_state.to_pixel_rect(tex_w, tex_h)
        top_left = self._transform_controller.convert_image_to_viewport(rect["left"], rect["top"])
        bottom_right = self._transform_controller.convert_image_to_viewport(rect["right"], rect["bottom"])
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
            self._stop_crop_animation()
            self._crop_idle_timer.stop()
            self._crop_drag_handle = CropHandle.NONE
            self._crop_dragging = False
            self._crop_faded_out = False
            self._baseline_crop_state = None
            self._on_cursor_change(None)
            self._on_request_update()
            return

        # Load the stored crop rectangle when entering crop mode
        self._apply_crop_values(values)
        self._crop_faded_out = False
        self._crop_drag_handle = CropHandle.NONE
        self._crop_dragging = False
        self._stop_crop_animation()
        self._restart_crop_idle()
        self._on_request_update()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def handle_mouse_press(self, event: QMouseEvent) -> None:
        """Handle mouse press events in crop mode."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return

        self._stop_crop_animation()
        self._stop_crop_idle()
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

        if handle == CropHandle.INSIDE:
            self._on_cursor_change(Qt.CursorShape.ClosedHandCursor)
        else:
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

        if self._crop_drag_handle == CropHandle.INSIDE:
            view_scale = self._transform_controller.get_effective_scale()
            if view_scale <= 1e-6:
                return

            dpr = self._transform_controller._get_dpr()
            delta_device_x = float(delta_view.x()) * dpr
            delta_device_y = float(delta_view.y()) * dpr
            delta_image = QPointF(delta_device_x / view_scale, delta_device_y / view_scale)

            snapshot = self._snapshot_crop_state()
            self._crop_state.translate_pixels(delta_image, (tex_w, tex_h))
            if not self._ensure_crop_valid_or_revert(snapshot, allow_shrink=False):
                return
            if self._has_crop_state_changed(snapshot):
                self._emit_crop_changed()
        else:
            # Dragging edge/corner: resize the crop using world-space deltas so the
            # drag feels identical regardless of crop zoom level, mirroring the
            # demo implementation.
            view_scale = self._transform_controller.get_effective_scale()
            if view_scale <= 1e-6:
                return

            snapshot = self._snapshot_crop_state()
            dpr = self._transform_controller._get_dpr()
            delta_world = QPointF(
                float(delta_view.x()) * dpr / view_scale,
                -float(delta_view.y()) * dpr / view_scale,
            )

            # -----------------------------------------------------------------
            # Crop box definition (System A) must be constrained by the original
            # texture bounds (scale=1, offset=0), not by the view transform state
            # The saved crop data must remain consistent regardless of
            # temporary pan/zoom operations in the crop interface.
            # -----------------------------------------------------------------
            half_width_orig = tex_w * 0.5
            half_height_orig = tex_h * 0.5
            img_bounds_world = {
                "left": -half_width_orig,
                "right": half_width_orig,
                "bottom": -half_height_orig,
                "top": half_height_orig,
            }

            # Convert the current crop rectangle into world coordinates so we can
            # manipulate each edge directly and perform clamping in the same space
            # as the image bounds computed above.
            crop_rect_px = self._crop_state.to_pixel_rect(tex_w, tex_h)
            crop_world = {
                "left": crop_rect_px["left"] - half_width_orig,
                "right": crop_rect_px["right"] - half_width_orig,
                "top": half_height_orig - crop_rect_px["top"],
                "bottom": half_height_orig - crop_rect_px["bottom"],
            }

            # Translate the minimum crop dimensions from normalised space to the
            # current texture space. One pixel is enforced to avoid degeneracy when
            # the stored minimum ends up smaller than a device pixel.
            min_width_px = max(1.0, self._crop_state.min_width * tex_w)
            min_height_px = max(1.0, self._crop_state.min_height * tex_h)

            # _crop_hit_test returns TEXTURE-SPACE handles, so we use them directly  
            # without additional rotation transformation.
            texture_handle = self._crop_drag_handle
            
            # Convert delta to texture space (currently no-op, see method docs)
            delta_x, delta_y = self._rotate_delta_to_texture_space(
                delta_world.x(), delta_world.y(), self._rotate_steps
            )


            if texture_handle in (CropHandle.LEFT, CropHandle.TOP_LEFT, CropHandle.BOTTOM_LEFT):
                new_left = crop_world["left"] + delta_x
                new_left = min(new_left, crop_world["right"] - min_width_px)
                new_left = max(new_left, img_bounds_world["left"])
                crop_world["left"] = new_left

            if texture_handle in (CropHandle.RIGHT, CropHandle.TOP_RIGHT, CropHandle.BOTTOM_RIGHT):
                new_right = crop_world["right"] + delta_x
                new_right = max(new_right, crop_world["left"] + min_width_px)
                new_right = min(new_right, img_bounds_world["right"])
                crop_world["right"] = new_right

            if texture_handle in (CropHandle.BOTTOM, CropHandle.BOTTOM_LEFT, CropHandle.BOTTOM_RIGHT):
                new_bottom = crop_world["bottom"] + delta_y
                new_bottom = min(new_bottom, crop_world["top"] - min_height_px)
                new_bottom = max(new_bottom, img_bounds_world["bottom"])
                crop_world["bottom"] = new_bottom

            if texture_handle in (CropHandle.TOP, CropHandle.TOP_LEFT, CropHandle.TOP_RIGHT):
                new_top = crop_world["top"] + delta_y
                new_top = max(new_top, crop_world["bottom"] + min_height_px)
                new_top = min(new_top, img_bounds_world["top"])
                crop_world["top"] = new_top

            # Convert the updated world-space rectangle back into normalised crop
            # values so the rest of the controller continues to operate on the
            # canonical representation.
            new_px_left = crop_world["left"] + half_width_orig
            new_px_right = crop_world["right"] + half_width_orig
            new_px_top = half_height_orig - crop_world["top"]
            new_px_bottom = half_height_orig - crop_world["bottom"]

            if tex_w > 0 and tex_h > 0:
                new_width = new_px_right - new_px_left
                new_height = new_px_bottom - new_px_top
                if new_width > 0.0 and new_height > 0.0:
                    self._crop_state.cx = (new_px_left + new_px_right) * 0.5 / tex_w
                    self._crop_state.cy = (new_px_top + new_px_bottom) * 0.5 / tex_h
                    self._crop_state.width = new_width / tex_w
                    self._crop_state.height = new_height / tex_h
                    self._crop_state.clamp()

            if not self._ensure_crop_valid_or_revert(snapshot, allow_shrink=False):
                return
            self._emit_crop_changed()
            self._apply_edge_push_auto_zoom(delta_view)

        self._restart_crop_idle()
        self._on_request_update()

    def handle_mouse_release(self, event: QMouseEvent) -> None:
        """Handle mouse release events in crop mode."""
        del event  # unused
        self._crop_dragging = False
        self._crop_drag_handle = CropHandle.NONE
        self._on_cursor_change(None)
        self._restart_crop_idle()

    def handle_wheel(self, event: QWheelEvent) -> None:
        """Handle wheel events in crop mode for zooming."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return

        self._stop_crop_animation()
        self._crop_faded_out = False
        self._stop_crop_idle()

        angle = event.angleDelta().y()
        if angle == 0:
            self._restart_crop_idle()
            return

        # Guard against devices that emit unusually large wheel deltas
        angle = max(-480, min(480, angle))

        factor = math.pow(1.0015, angle)
        if abs(factor - 1.0) <= 1e-6:
            self._restart_crop_idle()
            event.accept()
            return
        anchor_image = self._transform_controller.convert_viewport_to_image(event.position())
        anchor_norm_x = max(0.0, min(1.0, float(anchor_image.x()) / float(tex_w)))
        anchor_norm_y = max(0.0, min(1.0, float(anchor_image.y()) / float(tex_h)))

        snapshot = self._snapshot_crop_state()
        self._crop_state.zoom_about_point(anchor_norm_x, anchor_norm_y, factor)
        if not self._ensure_crop_valid_or_revert(snapshot, allow_shrink=False):
            self._on_request_update()
            self._restart_crop_idle()
            event.accept()
            return
        if self._has_crop_state_changed(snapshot):
            self._emit_crop_changed()
        self._on_request_update()
        self._restart_crop_idle()
        event.accept()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _apply_crop_values(self, values: Mapping[str, float] | None) -> None:
        """Apply crop values to the crop state."""
        if values:
            self._crop_state.set_from_mapping(values)
        else:
            self._crop_state.set_full()

        changed = self._ensure_crop_center_inside_quad()
        if not self._is_crop_inside_perspective_quad():
            changed = self._auto_scale_crop_to_quad() or changed

        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return

        center = self._crop_state.center_pixels(tex_w, tex_h)
        scale = self._transform_controller.get_effective_scale()
        clamped_center = self._clamp_image_center_to_crop(center, scale)
        self._transform_controller.apply_image_center_pixels(clamped_center, scale)
        if changed:
            self._emit_crop_changed()

    def _crop_center_viewport_point(self) -> QPointF:
        """Return the crop center in viewport coordinates."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            # Fallback to viewport center
            view_width, view_height = self._transform_controller._get_view_dimensions_logical()
            return QPointF(view_width / 2, view_height / 2)
        center = self._crop_state.center_pixels(tex_w, tex_h)
        return self._transform_controller.convert_image_to_viewport(center.x(), center.y())

    @staticmethod
    def _rotate_handle_to_texture_space(handle: CropHandle, rotate_steps: int) -> CropHandle:
        """Map a logical-space crop handle to its texture-space equivalent.
        
        When the image is rotated, the user drags edges/corners in the rotated
        (logical) coordinate system, but the crop state is stored in the original
        texture coordinate system. This method transforms the handle from logical
        space back to texture space so the correct texture edges are modified.
        
        IMPORTANT: This is the INVERSE mapping of the rotation. If the image is
        rotated 90° CCW (step=1), then logical LEFT corresponds to texture TOP,
        because texture TOP rotated 90° CCW ends up at the left side visually.
        
        Parameters
        ----------
        handle:
            The handle in logical/display space (what the user sees).
        rotate_steps:
            Number of 90° CCW rotations (0-3).
            
        Returns
        -------
        CropHandle:
            The equivalent handle in texture space.
        """
        if handle in (CropHandle.NONE, CropHandle.INSIDE):
            return handle
            
        # Normalize to 0-3 range
        steps = int(rotate_steps) % 4
        if steps == 0:
            return handle
        
        # CRITICAL: In this application, step DECREASES as rotation INCREASES.
        # The actual CCW rotation amount is (4 - step):
        # - step=3: one click → 90° CCW → actual_steps=1
        #   Visual LEFT = Texture TOP (because TOP rotated 90° CCW lands at LEFT)
        #   Mapping: LEFT → (1 CCW step) → TOP ✓
        # - step=2: two clicks → 180° → actual_steps=2
        #   Visual LEFT = Texture RIGHT
        #   Mapping: LEFT → (2 CCW steps) → TOP → RIGHT ✓
        # - step=1: three clicks → 270° CCW → actual_steps=3
        #   Visual LEFT = Texture BOTTOM
        #   Mapping: LEFT → (3 CCW steps) → TOP → RIGHT → BOTTOM ✓
        
        actual_steps = (4 - steps) % 4
        if actual_steps == 0:
            return handle
        
        # Map single edges using CCW rotation: LEFT→TOP→RIGHT→BOTTOM→LEFT
        edge_map_90ccw = {
            CropHandle.LEFT: CropHandle.TOP,
            CropHandle.TOP: CropHandle.RIGHT,
            CropHandle.RIGHT: CropHandle.BOTTOM,
            CropHandle.BOTTOM: CropHandle.LEFT,
        }
        
        corner_map_90ccw = {
            CropHandle.TOP_LEFT: CropHandle.TOP_RIGHT,
            CropHandle.TOP_RIGHT: CropHandle.BOTTOM_RIGHT,
            CropHandle.BOTTOM_RIGHT: CropHandle.BOTTOM_LEFT,
            CropHandle.BOTTOM_LEFT: CropHandle.TOP_LEFT,
        }
        
        # Apply CCW rotation 'actual_steps' times
        current = handle
        for _ in range(actual_steps):
            if current in edge_map_90ccw:
                current = edge_map_90ccw[current]
            elif current in corner_map_90ccw:
                current = corner_map_90ccw[current]
                
        return current

    @staticmethod
    def _rotate_delta_to_texture_space(delta_x: float, delta_y: float, rotate_steps: int) -> tuple[float, float]:
        """Convert delta vector from world space to texture space.
        
        IMPORTANT: Delta vectors do NOT need rotation transformation because:
        1. The delta is already in world coordinate space (Y-up)
        2. The world coordinate system is shared between texture and viewport
        3. The viewport transformation (_convert_image_to_viewport) already
           handles the rotation when mapping positions
        4. For edge dragging, we work directly with world-space crop_world dict
           which uses the same coordinate system as delta
        
        Therefore, we simply return the delta as-is. Any rotation would
        cause the drag direction to be mapped incorrectly.
        
        Parameters
        ----------
        delta_x:
            Horizontal delta in world space.
        delta_y:
            Vertical delta in world space (positive = up).
        rotate_steps:
            Number of rotation steps (0-3), unused but kept for API consistency.
            
        Returns
        -------
        tuple[float, float]:
            (delta_x, delta_y) unchanged.
        """
        return (delta_x, delta_y)

    @staticmethod
    def _distance_to_segment(point: QPointF, start: QPointF, end: QPointF) -> float:
        """Calculate distance from point to line segment."""
        px, py = point.x(), point.y()
        ax, ay = start.x(), start.y()
        bx, by = end.x(), end.y()
        vx = bx - ax
        vy = by - ay
        if abs(vx) < 1e-6 and abs(vy) < 1e-6:
            return math.hypot(px - ax, py - ay)
        t = ((px - ax) * vx + (py - ay) * vy) / (vx * vx + vy * vy)
        t = max(0.0, min(1.0, t))
        qx = ax + t * vx
        qy = ay + t * vy
        return math.hypot(px - qx, py - qy)

    def _crop_hit_test(self, point: QPointF) -> CropHandle:
        """Determine which crop handle (if any) is under the cursor.
        
        IMPORTANT: This returns TEXTURE-SPACE handles, not logical handles.
        The handle names (TOP, LEFT, etc.) refer to the texture coordinate system,
        not the visual/rotated display.
        """
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return CropHandle.NONE

        rect = self._crop_state.to_pixel_rect(tex_w, tex_h)
        top_left = self._transform_controller.convert_image_to_viewport(rect["left"], rect["top"])
        top_right = self._transform_controller.convert_image_to_viewport(rect["right"], rect["top"])
        bottom_right = self._transform_controller.convert_image_to_viewport(rect["right"], rect["bottom"])
        bottom_left = self._transform_controller.convert_image_to_viewport(rect["left"], rect["bottom"])

        # Check corners first - these are TEXTURE-SPACE handles
        corners = [
            (CropHandle.TOP_LEFT, top_left),
            (CropHandle.TOP_RIGHT, top_right),
            (CropHandle.BOTTOM_RIGHT, bottom_right),
            (CropHandle.BOTTOM_LEFT, bottom_left),
        ]
        for handle, corner in corners:
            if math.hypot(point.x() - corner.x(), point.y() - corner.y()) <= self._crop_hit_padding:
                return handle

        # Check edges - these are TEXTURE-SPACE handles  
        edges = [
            (CropHandle.TOP, top_left, top_right),
            (CropHandle.RIGHT, top_right, bottom_right),
            (CropHandle.BOTTOM, bottom_left, bottom_right),
            (CropHandle.LEFT, top_left, bottom_left),
        ]
        for handle, start, end in edges:
            if self._distance_to_segment(point, start, end) <= self._crop_hit_padding:
                return handle

        # Check if inside
        left = min(top_left.x(), bottom_left.x())
        right = max(top_right.x(), bottom_right.x())
        top = min(top_left.y(), top_right.y())
        bottom = max(bottom_left.y(), bottom_right.y())
        if left <= point.x() <= right and top <= point.y() <= bottom:
            return CropHandle.INSIDE

        return CropHandle.NONE

    def _restart_crop_idle(self) -> None:
        """Restart the idle timer for crop fade-out animation."""
        if self._active:
            self._crop_idle_timer.start()

    def _stop_crop_idle(self) -> None:
        """Stop the idle timer."""
        self._crop_idle_timer.stop()

    def _stop_crop_animation(self) -> None:
        """Stop the crop fade-out animation."""
        if self._crop_anim_active:
            self._crop_anim_active = False
            self._crop_anim_timer.stop()

    def _on_crop_idle_timeout(self) -> None:
        """Handle idle timeout - start fade-out animation."""
        self._crop_idle_timer.stop()
        self._start_crop_animation()

    def _start_crop_animation(self) -> None:
        """Start the crop fade-out animation."""
        tex_w, tex_h = self._texture_size_provider()
        if not self._active or tex_w <= 0 or tex_h <= 0:
            return

        target_scale = self._target_scale_for_crop()
        target_center = self._crop_state.center_pixels(tex_w, tex_h)
        # We intentionally avoid clamping the target centre; the animation must match the
        # crop rectangle's true centre exactly to reproduce the demo behaviour.

        self._crop_anim_active = True
        self._crop_anim_start_time = time.monotonic()
        self._crop_anim_start_scale = self._transform_controller.get_effective_scale()
        self._crop_anim_target_scale = target_scale
        self._crop_anim_start_center = self._transform_controller.get_image_center_pixels()
        self._crop_anim_target_center = target_center
        self._crop_anim_timer.start()
        self._crop_faded_out = False

    def _on_crop_anim_tick(self) -> None:
        """Handle animation tick - update crop animation state."""
        if not self._crop_anim_active:
            self._crop_anim_timer.stop()
            return

        elapsed = time.monotonic() - self._crop_anim_start_time
        if elapsed >= self._crop_anim_duration:
            scale = self._crop_anim_target_scale
            centre = self._crop_anim_target_center
            self._apply_crop_animation_state(scale, centre)
            self._crop_anim_active = False
            self._crop_anim_timer.stop()
            self._crop_faded_out = True
            self._on_request_update()
            return

        progress = max(0.0, min(1.0, elapsed / self._crop_anim_duration))
        eased = ease_out_cubic(progress)
        scale = self._crop_anim_start_scale + (
            (self._crop_anim_target_scale - self._crop_anim_start_scale) * eased
        )
        centre_x = self._crop_anim_start_center.x() + (
            (self._crop_anim_target_center.x() - self._crop_anim_start_center.x()) * eased
        )
        centre_y = self._crop_anim_start_center.y() + (
            (self._crop_anim_target_center.y() - self._crop_anim_start_center.y()) * eased
        )
        self._apply_crop_animation_state(scale, QPointF(centre_x, centre_y))
        self._on_request_update()

    def _apply_crop_animation_state(self, scale: float, centre: QPointF) -> None:
        """Apply animation state to the viewer."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return

        vw, vh = self._transform_controller._get_view_dimensions_device_px()
        
        # CRITICAL: Must use logical (rotation-aware) dimensions for base_scale calculation
        # to match ViewTransformController.effective_scale behavior. Using physical dimensions
        # causes zoom factor mismatch at 90°/270° rotations.
        fit_w, fit_h = self._transform_controller._get_fit_texture_size()
        tex_size = (int(fit_w), int(fit_h))
            
        base_scale = compute_fit_to_view_scale(tex_size, vw, vh)
        min_zoom = self._transform_controller.minimum_zoom()
        max_zoom = self._transform_controller.maximum_zoom()
        zoom_factor = max(min_zoom, min(max_zoom, scale / max(base_scale, 1e-6)))
        self._transform_controller.set_zoom_factor_direct(zoom_factor)
        actual_scale = self._transform_controller.get_effective_scale()
        # The interpolated centre is already expressed in image-space pixels, so we apply it
        # directly without clamping to preserve the smooth easing trajectory.
        self._transform_controller.apply_image_center_pixels(centre, actual_scale)

    def _target_scale_for_crop(self) -> float:
        """Calculate the target scale for crop fade-out animation."""
        tex_w, tex_h = self._texture_size_provider()
        if tex_w <= 0 or tex_h <= 0:
            return self._transform_controller.get_effective_scale()

        vw, vh = self._transform_controller._get_view_dimensions_device_px()
        crop_rect = self._crop_state.to_pixel_rect(tex_w, tex_h)
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

    def _emit_crop_changed(self) -> None:
        """Emit the crop changed signal."""
        state = self._crop_state
        self._on_crop_changed(
            float(state.cx), float(state.cy), float(state.width), float(state.height)
        )

    def _snapshot_crop_state(self) -> tuple[float, float, float, float]:
        """Return a tuple describing the current crop rectangle."""

        state = self._crop_state
        return (float(state.cx), float(state.cy), float(state.width), float(state.height))

    def _has_crop_state_changed(self, snapshot: tuple[float, float, float, float]) -> bool:
        """Return ``True`` when the current crop differs from *snapshot*."""

        current = self._snapshot_crop_state()
        return any(abs(a - b) > 1e-6 for a, b in zip(snapshot, current))

    def _restore_crop_snapshot(self, snapshot: tuple[float, float, float, float]) -> None:
        """Restore the crop rectangle from *snapshot*."""

        self._crop_state.cx, self._crop_state.cy, self._crop_state.width, self._crop_state.height = snapshot
        self._crop_state.clamp()

    def _current_normalised_rect(self) -> NormalisedRect:
        left, top, right, bottom = self._crop_state.bounds_normalised()
        return NormalisedRect(left, top, right, bottom)

    def _is_crop_inside_perspective_quad(self) -> bool:
        quad = self._perspective_quad or unit_quad()
        return rect_inside_quad(self._current_normalised_rect(), quad)

    def _ensure_crop_center_inside_quad(self) -> bool:
        """Reposition the crop centre when perspective squeezes the valid quad."""

        quad = self._perspective_quad or unit_quad()
        center = (float(self._crop_state.cx), float(self._crop_state.cy))
        if point_in_convex_polygon(center, quad):
            return False
        centroid = quad_centroid(quad)
        self._crop_state.cx = max(0.0, min(1.0, centroid[0]))
        self._crop_state.cy = max(0.0, min(1.0, centroid[1]))
        self._crop_state.clamp()
        return True

    def _auto_scale_crop_to_quad(self) -> bool:
        """Shrink the crop uniformly so it sits entirely inside the quad."""

        quad = self._perspective_quad or unit_quad()
        rect = self._current_normalised_rect()
        scale = calculate_min_zoom_to_fit(rect, quad)
        if not math.isfinite(scale) or scale <= 1.0 + 1e-4:
            return False
        self._crop_state.width = max(self._crop_state.min_width, self._crop_state.width / scale)
        self._crop_state.height = max(self._crop_state.min_height, self._crop_state.height / scale)
        self._crop_state.clamp()
        return True

    def _apply_baseline_perspective_fit(self) -> bool:
        """Fit the stored baseline crop into the current perspective quad."""

        if self._baseline_crop_state is None:
            return False
        snapshot = self._snapshot_crop_state()
        quad = self._perspective_quad or unit_quad()
        base_cx, base_cy, base_width, base_height = self._baseline_crop_state
        center = (float(base_cx), float(base_cy))
        if not point_in_convex_polygon(center, quad):
            centroid = quad_centroid(quad)
            center = (
                max(0.0, min(1.0, float(centroid[0]))),
                max(0.0, min(1.0, float(centroid[1]))),
            )

        half_w = max(0.0, float(base_width) * 0.5)
        half_h = max(0.0, float(base_height) * 0.5)
        rect = NormalisedRect(
            center[0] - half_w,
            center[1] - half_h,
            center[0] + half_w,
            center[1] + half_h,
        )
        scale = calculate_min_zoom_to_fit(rect, quad)
        if not math.isfinite(scale) or scale < 1.0:
            scale = 1.0

        new_width = max(self._crop_state.min_width, float(base_width) / scale)
        new_height = max(self._crop_state.min_height, float(base_height) / scale)
        self._crop_state.width = min(1.0, new_width)
        self._crop_state.height = min(1.0, new_height)
        self._crop_state.cx = max(0.0, min(1.0, center[0]))
        self._crop_state.cy = max(0.0, min(1.0, center[1]))
        self._crop_state.clamp()
        return self._has_crop_state_changed(snapshot)

    def _ensure_crop_valid_or_revert(
        self,
        snapshot: tuple[float, float, float, float],
        *,
        allow_shrink: bool,
    ) -> bool:
        """Keep the crop within the perspective quad or restore *snapshot*."""

        if self._is_crop_inside_perspective_quad():
            return True
        if allow_shrink and self._auto_scale_crop_to_quad():
            return True
        self._restore_crop_snapshot(snapshot)
        return False

    # ------------------------------------------------------------------
    # Edge-push auto zoom helpers
    # ------------------------------------------------------------------
    def _apply_edge_push_auto_zoom(self, delta_view: QPointF) -> None:
        """Shrink and pan automatically when a handle pushes against the viewport.

        The behaviour mirrors the reference demo: when the user drags an edge or
        corner towards the viewport boundary we gradually zoom out and pan in the
        opposite direction so new image content flows into view without forcing
        the gesture to pause.  All calculations are performed in device pixels to
        avoid precision loss on high-DPI screens, after which the resulting
        offsets are converted back into image-space pixels for the transform
        controller.
        """

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

        # ``delta_image`` lives in the conventional image pixel space (top-left
        # origin) so we can reuse it directly when nudging the image centre.
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

        crop_center = self._crop_state.center_pixels(tex_w, tex_h)
        crop_center_view = self._transform_controller.convert_image_to_viewport(
            crop_center.x(), crop_center.y()
        )
        base_scale_safe = max(base_scale, 1e-6)
        target_zoom = new_scale / base_scale_safe
        self._transform_controller.set_zoom(target_zoom, anchor=crop_center_view)

        # Translate opposite to the drag direction.  ``pan_gain`` amplifies the
        # offset slightly when the pressure approaches 1.0 to mimic the demo's
        # "push against the wall" feel.
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
