"""Crop-viewport helpers extracted from ``widget.py``.

Every function takes the *viewer* (``GLImageViewer`` instance) as its first
argument so that it can access internal state without being a method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt

from . import crop_logic
from . import geometry
from ..view_transform_controller import (
    compute_fit_to_view_scale,
    compute_rotation_cover_scale,
)

if TYPE_CHECKING:
    from .widget import GLImageViewer


# ── Texture dimension helpers ──────────────────────────────────────────

def texture_dimensions(viewer: GLImageViewer) -> tuple[int, int]:
    """Return the current texture size or ``(0, 0)`` when unavailable."""
    if viewer._renderer is not None and viewer._renderer.has_texture():
        return viewer._renderer.texture_size()
    if viewer._image is not None and not viewer._image.isNull():
        return (viewer._image.width(), viewer._image.height())
    return (0, 0)


def display_texture_dimensions(viewer: GLImageViewer) -> tuple[int, int]:
    """Return the logical texture dimensions used for fit-to-view math."""
    tex_w, tex_h = texture_dimensions(viewer)
    if tex_w <= 0 or tex_h <= 0:
        return (tex_w, tex_h)
    rotate_steps = viewer._display_rotate_steps()
    if rotate_steps % 2:
        return (tex_h, tex_w)
    return (tex_w, tex_h)


# ── Rotation / cover-scale helpers ─────────────────────────────────────

def rotation_parameters(viewer: GLImageViewer) -> tuple[float, int, bool]:
    """Return the straighten angle, rotate steps, and flip toggle."""
    straighten = float(viewer._adjustments.get("Crop_Straighten", 0.0))
    rotate_steps = viewer._display_rotate_steps()
    flip = bool(viewer._adjustments.get("Crop_FlipH", False))
    return straighten, rotate_steps, flip


def update_cover_scale(
    viewer: GLImageViewer, straighten_deg: float, rotate_steps: int
) -> None:
    """Compute the rotation cover scale and forward it to the transform controller."""
    if not viewer._renderer or not viewer._renderer.has_texture():
        viewer._transform_controller.set_image_cover_scale(1.0)
        return

    if abs(straighten_deg) <= 1e-5:
        viewer._transform_controller.set_image_cover_scale(1.0)
        return

    tex_w, tex_h = texture_dimensions(viewer)
    if tex_w <= 0 or tex_h <= 0:
        viewer._transform_controller.set_image_cover_scale(1.0)
        return

    display_w, display_h = display_texture_dimensions(viewer)
    view_width, view_height = viewer._zoom_ctrl.view_dimensions_device_px()

    base_scale = compute_fit_to_view_scale(
        (display_w, display_h), float(view_width), float(view_height)
    )

    rotation_cover_scale = compute_rotation_cover_scale(
        (display_w, display_h),
        base_scale,
        straighten_deg,
        rotate_steps,
        physical_texture_size=(tex_w, tex_h),
    )

    viewer._transform_controller.set_image_cover_scale(rotation_cover_scale)


# ── Crop-perspective state ─────────────────────────────────────────────

def update_crop_perspective_state(viewer: GLImageViewer) -> None:
    """Forward the latest perspective sliders to the crop controller."""
    if not hasattr(viewer, "_crop_controller") or viewer._crop_controller is None:
        return
    vertical = float(viewer._adjustments.get("Perspective_Vertical", 0.0))
    horizontal = float(viewer._adjustments.get("Perspective_Horizontal", 0.0))
    straighten, rotate_steps, flip = rotation_parameters(viewer)
    logical_values = viewer._logical_crop_values()
    viewer._crop_controller.update_perspective(
        vertical,
        horizontal,
        straighten,
        rotate_steps,
        flip,
        new_crop_values=logical_values,
    )
    update_cover_scale(viewer, straighten, rotate_steps)


# ── Crop framing helpers ───────────────────────────────────────────────

def compute_crop_rect_pixels(viewer: GLImageViewer) -> QRectF | None:
    """Return the crop rectangle expressed in texture pixels."""
    tex_w, tex_h = display_texture_dimensions(viewer)
    crop_cx, crop_cy, crop_w, crop_h = geometry.logical_crop_from_texture(
        viewer._display_adjustments()
    )
    return crop_logic.compute_crop_rect_pixels(
        crop_cx, crop_cy, crop_w, crop_h, tex_w, tex_h
    )


def frame_crop_if_available(viewer: GLImageViewer) -> bool:
    """Frame the active crop rectangle if the adjustments define one."""
    if viewer._crop_controller.is_active():
        return False
    crop_rect = compute_crop_rect_pixels(viewer)
    if crop_rect is None:
        viewer._auto_crop_view_locked = False
        return False
    if viewer._transform_controller.frame_texture_rect(crop_rect):
        viewer._auto_crop_view_locked = True
        return True
    return False


def center_crop_if_available(viewer: GLImageViewer) -> bool:
    """Recenter the viewport on the stored crop while keeping fit-to-view zoom."""
    if viewer._crop_controller.is_active():
        return False
    crop_rect = compute_crop_rect_pixels(viewer)
    if crop_rect is None:
        viewer._auto_crop_center_locked = False
        return False
    viewer._transform_controller.reset_zoom()
    fit_result = viewer._transform_controller.compute_texture_rect_fit(crop_rect)
    if fit_result is not None:
        target_zoom, _ = fit_result
        strength = max(0.0, min(1.0, viewer.crop_center_zoom_strength()))
        if target_zoom > 1.0 and strength > 0.0:
            # Interpolate between full-frame fit and crop-fill fit.  Using the
            # geometric mean keeps playback closer to the v4.6.0 video layout:
            # the crop is clearly emphasised without jumping all the way to the
            # edit-mode "fill the crop" presentation.
            partial_zoom = target_zoom ** strength
            if partial_zoom > 1.0 + 1e-6:
                viewer._transform_controller.set_zoom_factor_direct(partial_zoom)
    viewer._transform_controller.apply_image_center_pixels(crop_rect.center())
    viewer._auto_crop_center_locked = True
    return True


def reapply_locked_crop_view(viewer: GLImageViewer) -> None:
    """Re-apply the stored crop framing after resizes or adjustment edits."""
    if not viewer._auto_crop_view_locked:
        return
    crop_rect = compute_crop_rect_pixels(viewer)
    if crop_rect is None:
        viewer._auto_crop_view_locked = False
        return
    if not viewer._transform_controller.frame_texture_rect(crop_rect):
        viewer._auto_crop_view_locked = False


def reapply_locked_crop_center(viewer: GLImageViewer) -> None:
    """Recenter the crop after resizes without changing the fit baseline."""
    if not viewer._auto_crop_center_locked:
        return
    if not center_crop_if_available(viewer):
        viewer._auto_crop_center_locked = False


def cancel_auto_crop_lock(viewer: GLImageViewer) -> None:
    """Disable auto-crop framing so manual gestures stay respected."""
    viewer._auto_crop_view_locked = False
    viewer._auto_crop_center_locked = False


# ── Crop interaction callback ──────────────────────────────────────────

def handle_crop_interaction_changed(
    viewer: GLImageViewer,
    cx: float,
    cy: float,
    width: float,
    height: float,
) -> None:
    """Convert logical crop updates back to texture space before emitting."""
    rotate_steps = viewer._display_rotate_steps()
    tex_cx, tex_cy, tex_w, tex_h = geometry.logical_crop_to_texture(
        (float(cx), float(cy), float(width), float(height)),
        rotate_steps,
    )
    viewer.cropChanged.emit(tex_cx, tex_cy, tex_w, tex_h)


# ── Cursor / eyedropper ───────────────────────────────────────────────

def handle_cursor_change(
    viewer: GLImageViewer, cursor: Qt.CursorShape | None
) -> None:
    """Handle cursor change request from controllers."""
    if cursor is None:
        viewer.unsetCursor()
    else:
        viewer.setCursor(cursor)


def handle_eyedropper_pick(viewer: GLImageViewer, position: QPointF) -> bool:
    """Sample the image under *position* and emit a ``colorPicked`` signal."""
    if viewer._image is None or viewer._image.isNull():
        return False

    logical_w, logical_h = display_texture_dimensions(viewer)
    if logical_w <= 0 or logical_h <= 0:
        return False

    image_point = viewer._zoom_ctrl.viewport_to_image(position)
    if image_point.isNull():
        return False

    lx = max(0.0, min(1.0, image_point.x() / float(logical_w)))
    ly = max(0.0, min(1.0, image_point.y() / float(logical_h)))

    rotate_steps = viewer._display_rotate_steps()
    if rotate_steps == 1:
        tx, ty = ly, 1.0 - lx
    elif rotate_steps == 2:
        tx, ty = 1.0 - lx, 1.0 - ly
    elif rotate_steps == 3:
        tx, ty = 1.0 - ly, lx
    else:
        tx, ty = lx, ly

    tex_w = viewer._image.width()
    tex_h = viewer._image.height()
    if tex_w <= 0 or tex_h <= 0:
        return False

    px = int(round(tx * (tex_w - 1)))
    py = int(round(ty * (tex_h - 1)))
    px = max(0, min(tex_w - 1, px))
    py = max(0, min(tex_h - 1, py))

    color = viewer._image.pixelColor(px, py)
    viewer.colorPicked.emit(color.redF(), color.greenF(), color.blueF())
    viewer.set_eyedropper_mode(False)
    return True
