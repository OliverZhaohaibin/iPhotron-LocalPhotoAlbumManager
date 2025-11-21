"""
GPU-accelerated image viewer (pure OpenGL texture upload; pixel-accurate zoom/pan).
- Ensures magnification samples the ORIGINAL pixels (no Qt/FBO resampling).
- Uses GL 3.3 Core, VAO/VBO, and a raw glTexImage2D + glTexSubImage2D upload path.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping

from OpenGL import GL as gl
from PySide6.QtCore import QPointF, QSize, Qt, Signal, QRectF
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QPixmap,
    QSurfaceFormat,
    QWheelEvent,
)
from PySide6.QtOpenGL import (
    QOpenGLDebugLogger,
    QOpenGLFunctions_3_3_Core,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from ..gl_crop_controller import CropInteractionController
from ..gl_renderer import GLRenderer
from ..view_transform_controller import (
    ViewTransformController,
    compute_fit_to_view_scale,
    compute_rotation_cover_scale,
)

from . import crop_logic
from . import geometry
from .components import LoadingOverlay
from .input_handler import InputEventHandler
from .offscreen import OffscreenRenderer
from .resources import TextureResourceManager
from .utils import normalise_colour
from .view_helpers import clamp_center_to_texture_bounds

_LOGGER = logging.getLogger(__name__)

# 如果你的工程没有这个函数，可以改成固定背景色
try:
    from ...palette import viewer_surface_color  # type: ignore
except Exception:
    def viewer_surface_color(_):  # fallback
        return QColor(0, 0, 0)


class GLImageViewer(QOpenGLWidget):
    """A QWidget that displays GPU-rendered images with pixel-accurate zoom."""

    # Signals（保持与旧版一致）
    replayRequested = Signal()
    zoomChanged = Signal(float)
    nextItemRequested = Signal()
    prevItemRequested = Signal()
    fullscreenExitRequested = Signal()
    fullscreenToggleRequested = Signal()
    cropChanged = Signal(float, float, float, float)

    def __init__(self, parent: QOpenGLWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)

        # 强制 3.3 Core
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        self.setFormat(fmt)
        self._gl_funcs: QOpenGLFunctions_3_3_Core | None = None
        self._renderer: GLRenderer | None = None
        self._logger: QOpenGLDebugLogger | None = None

        # 状态
        self._image: QImage | None = None
        self._adjustments: dict[str, float] = {}
        
        # Texture resource manager
        self._texture_manager = TextureResourceManager(
            renderer_provider=lambda: self._renderer,
            context_provider=lambda: self.context(),
            make_current=self.makeCurrent,
            done_current=self.doneCurrent,
        )

        # Track the viewer surface colour so immersive mode can temporarily
        # switch to a pure black canvas.  ``viewer_surface_color`` returns a
        # palette-derived colour string, which we normalise to ``QColor`` for
        # reliable comparisons and GL clear colour conversion.
        self._default_surface_color = normalise_colour(viewer_surface_color(self))
        self._surface_override: QColor | None = None
        self._backdrop_color: QColor = QColor(self._default_surface_color)
        self._apply_surface_color()

        # ``_time_base`` anchors the monotonic clock used by the shader grain generator.  Resetting
        # the start time keeps the uniform values numerically small even after long application
        # sessions.
        self._time_base = time.monotonic()

        # Loading overlay component
        self._loading_overlay = LoadingOverlay(self)
        self._transform_controller = ViewTransformController(
            self,
            texture_size_provider=self._display_texture_dimensions,
            on_zoom_changed=self.zoomChanged.emit,
            on_next_item=self.nextItemRequested.emit,
            on_prev_item=self.prevItemRequested.emit,
            display_texture_size_provider=self._display_texture_dimensions,
        )
        self._transform_controller.reset_zoom()

        # Crop interaction controller
        self._crop_controller = CropInteractionController(
            texture_size_provider=self._display_texture_dimensions,
            clamp_image_center_to_crop=self._create_clamp_function(),
            transform_controller=self._transform_controller,
            on_crop_changed=self._handle_crop_interaction_changed,
            on_cursor_change=self._handle_cursor_change,
            on_request_update=self.update,
            timer_parent=self,
        )
        self._auto_crop_view_locked: bool = False
        self._update_crop_perspective_state()
        
        # Input event handler
        self._input_handler = InputEventHandler(
            crop_controller=self._crop_controller,
            transform_controller=self._transform_controller,
            on_replay_requested=self.replayRequested.emit,
            on_fullscreen_exit=self.fullscreenExitRequested.emit,
            on_fullscreen_toggle=self.fullscreenToggleRequested.emit,
            on_cancel_auto_crop_lock=self._cancel_auto_crop_lock,
        )

    # --------------------------- Public API ---------------------------

    def shutdown(self) -> None:
        """Clean up GL resources."""
        self.makeCurrent()
        try:
            if self._renderer is not None:
                self._renderer.destroy_resources()
        finally:
            self.doneCurrent()

    def set_image(
        self,
        image: QImage | None,
        adjustments: Mapping[str, float] | None = None,
        *,
        image_source: object | None = None,
        reset_view: bool = True,
    ) -> None:
        """Display *image* together with optional colour *adjustments*.

        Parameters
        ----------
        image:
            ``QImage`` backing the GL texture. ``None`` clears the viewer.
        adjustments:
            Mapping of Photos-style adjustment values to apply in the shader.
        image_source:
            Stable identifier describing where *image* originated.  When the
            identifier matches the one from the previous call the viewer keeps
            the existing GPU texture, avoiding redundant uploads during view
            transitions.
        reset_view:
            ``True`` preserves the historic behaviour of resetting the zoom and
            pan state.  Passing ``False`` keeps the current transform so edit
            mode can reuse the detail view framing without a visible jump.
        """

        # Check if we can reuse the existing texture
        if self._texture_manager.should_reuse_texture(image_source):
            if image is not None and not image.isNull():
                # Skip texture re-upload, only update adjustments
                self.set_adjustments(adjustments)
                if reset_view:
                    self.reset_zoom()
                return

        # Update texture resource tracking
        self._texture_manager.set_image(image, image_source)
        self._image = image
        self._adjustments = dict(adjustments or {})
        self._update_crop_perspective_state()
        self._loading_overlay.hide()
        self._time_base = time.monotonic()

        if image is None or image.isNull():
            # Clear resources and reset state
            self._texture_manager.clear_image()
            self._auto_crop_view_locked = False
            self._transform_controller.set_image_cover_scale(1.0)

        if reset_view:
            # Reset the interactive transform so every new asset begins in the
            # same fit-to-window baseline that the QWidget-based viewer
            # exposes.  ``reset_view`` lets callers preserve the zoom when the
            # user toggles between detail and edit modes.
            self.reset_zoom()
    def set_placeholder(self, pixmap: QPixmap | None) -> None:
        """Display *pixmap* without changing the tracked image source."""

        if pixmap and not pixmap.isNull():
            self.set_image(pixmap.toImage(), {}, image_source=self.current_image_source())
        else:
            self.set_image(None, {}, image_source=None)

    def set_pixmap(
        self,
        pixmap: QPixmap | None,
        image_source: object | None = None,
        *,
        reset_view: bool = True,
    ) -> None:
        """Compatibility wrapper mirroring :class:`ImageViewer`.

        The optional *image_source* is forwarded to :meth:`set_image` so callers
        can keep the existing texture alive when reusing the same asset.
        """

        if pixmap is None or pixmap.isNull():
            self.set_image(None, {}, image_source=None, reset_view=reset_view)
            return
        self.set_image(
            pixmap.toImage(),
            {},
            image_source=image_source if image_source is not None else self.current_image_source(),
            reset_view=reset_view,
        )

    def clear(self) -> None:
        """Reset the viewer to an empty state."""

        self.set_image(None, {}, image_source=None)

    def set_adjustments(self, adjustments: Mapping[str, float] | None = None) -> None:
        """Update the active adjustment uniforms without replacing the texture."""

        mapped_adjustments = dict(adjustments or {})
        self._adjustments = mapped_adjustments
        self._update_crop_perspective_state()
        if self._crop_controller.is_active():
            # Refresh the crop overlay in logical space so it stays aligned when rotation
            # or perspective adjustments change while the interaction mode is active.
            logical_values = geometry.logical_crop_mapping_from_texture(mapped_adjustments)
            self._crop_controller.set_active(True, logical_values)
        if self._auto_crop_view_locked and not self._crop_controller.is_active():
            self._reapply_locked_crop_view()
        self.update()

    def current_image_source(self) -> object | None:
        """Return the identifier describing the currently displayed image."""

        return self._texture_manager.get_current_image_source()

    def pixmap(self) -> QPixmap | None:
        """Return a defensive copy of the currently displayed frame."""

        if self._image is None or self._image.isNull():
            return None
        return QPixmap.fromImage(self._image)

    def set_loading(self, loading: bool) -> None:
        """Toggle the translucent loading overlay."""

        if loading:
            self._loading_overlay.show()
            self._loading_overlay.update_geometry(self.size())
        else:
            self._loading_overlay.hide()

    def viewport_widget(self) -> GLImageViewer:
        """Expose the drawable widget for API parity with :class:`ImageViewer`."""

        return self

    def set_live_replay_enabled(self, enabled: bool) -> None:
        self._input_handler.set_live_replay_enabled(enabled)

    def set_wheel_action(self, action: str) -> None:
        self._transform_controller.set_wheel_action(action)

    def set_surface_color_override(self, colour: str | None) -> None:
        """Override the viewer backdrop with *colour* or restore the default."""

        if colour is None:
            self._surface_override = None
        else:
            self._surface_override = normalise_colour(colour)
        self._apply_surface_color()

    def set_immersive_background(self, immersive: bool) -> None:
        """Toggle the pure black immersive backdrop used in immersive mode."""

        self.set_surface_color_override("#000000" if immersive else None)

    def rotate_image_ccw(self) -> dict[str, float]:
        """Rotate the photo 90° counter-clockwise without mutating crop geometry.

        The crop box remains defined in texture space so the rotation merely updates the
        quarter-turn counter.  The zoom stack is reset so the fit-to-view baseline adapts
        to the swapped logical dimensions after the aspect ratio flips.
        """

        rotated_steps = (geometry.get_rotate_steps(self._adjustments) - 1) % 4

        # Remap perspective sliders into the rotated coordinate frame so that the visual
        # effect stays consistent with what the user saw pre-rotation.  Perspective
        # values are expressed as a 2D vector aligned to the on-screen axes; rotating the
        # image 90° counter-clockwise corresponds to rotating this vector 90° clockwise
        # (swap axes and invert the previous vertical component).  If the image is
        # horizontally flipped, the horizontal axis is mirrored, so we also invert the
        # remapped horizontal component to preserve the perceived direction.
        old_v = float(self._adjustments.get("Perspective_Vertical", 0.0))
        old_h = float(self._adjustments.get("Perspective_Horizontal", 0.0))
        old_flip = bool(self._adjustments.get("Crop_FlipH", False))

        new_v = old_h
        new_h = -old_v
        if old_flip:
            new_h = -new_h

        updates: dict[str, float] = {
            "Crop_Rotate90": float(rotated_steps),
            "Perspective_Vertical": new_v,
            "Perspective_Horizontal": new_h,
        }

        # Apply the rotation locally so the viewer updates immediately even before the
        # session broadcasts the new adjustment mapping.
        self.set_adjustments({**self._adjustments, **updates})

        # Refresh the transform baseline to mirror the demo's post-rotation framing.
        self.reset_zoom()

        return updates

    def set_zoom(self, factor: float, anchor: QPointF | None = None) -> None:
        """Adjust the zoom while preserving the requested *anchor* pixel."""

        self._cancel_auto_crop_lock()
        anchor_point = anchor or self.viewport_center()
        self._transform_controller.set_zoom(float(factor), anchor_point)

    def reset_zoom(self) -> None:
        if self._crop_controller.is_active():
            self._transform_controller.reset_zoom()
            return
        if not self._frame_crop_if_available():
            self._auto_crop_view_locked = False
            self._transform_controller.reset_zoom()

    def zoom_in(self) -> None:
        current = self._transform_controller.get_zoom_factor()
        self.set_zoom(current * 1.1, anchor=self.viewport_center())

    def zoom_out(self) -> None:
        current = self._transform_controller.get_zoom_factor()
        self.set_zoom(current / 1.1, anchor=self.viewport_center())

    def viewport_center(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2)

    # --------------------------- Off-screen rendering ---------------------------

    def render_offscreen_image(
        self,
        target_size: QSize,
        adjustments: Mapping[str, float] | None = None,
    ) -> QImage:
        """Render the current texture into an off-screen framebuffer.

        Parameters
        ----------
        target_size:
            Final size of the rendered preview.
        adjustments:
            Mapping of shader uniform values to apply during rendering.  Passing
            ``None`` renders the frame using the viewer's current adjustment state.

        Returns
        -------
        QImage
            CPU-side image containing the rendered frame. The image is always
            in Format_ARGB32 for downstream consumers.

        Notes
        -----
        The width and height of the rendered image are clamped to at least one pixel
        to avoid driver errors. The returned image is always in Format_ARGB32 format.
        """
        return OffscreenRenderer.render(
            renderer=self._renderer,
            context=self.context(),
            make_current=self.makeCurrent,
            done_current=self.doneCurrent,
            image=self._image,
            adjustments=adjustments or self._adjustments,
            target_size=target_size,
            time_base=self._time_base,
        )

    # --------------------------- GL lifecycle ---------------------------

    def initializeGL(self) -> None:
        self._gl_funcs = QOpenGLFunctions_3_3_Core()
        self._gl_funcs.initializeOpenGLFunctions()
        gf = self._gl_funcs

        try:
            self._logger = QOpenGLDebugLogger(self)
            if self._logger.initialize():
                self._logger.messageLogged.connect(
                    lambda m: print(f"[GLDBG] {m.source().name}: {m.message()}")
                )
                self._logger.startLogging(QOpenGLDebugLogger.SynchronousLogging)
                print("[GLDBG] DebugLogger initialized.")
            else:
                print("[GLDBG] DebugLogger not available.")
        except Exception as exc:
            print(f"[GLDBG] Logger init failed: {exc}")

        if self._renderer is not None:
            self._renderer.destroy_resources()

        self._renderer = GLRenderer(gf, parent=self)
        self._renderer.initialize_resources()

        dpr = self.devicePixelRatioF()
        gf.glViewport(0, 0, int(self.width() * dpr), int(self.height() * dpr))
        print("[GL INIT] initializeGL completed.")

    def paintGL(self) -> None:
        gf = self._gl_funcs
        if gf is None or self._renderer is None:
            return

        dpr = self.devicePixelRatioF()
        vw = max(1, int(round(self.width() * dpr)))
        vh = max(1, int(round(self.height() * dpr)))
        gf.glViewport(0, 0, vw, vh)
        bg = self._backdrop_color
        gf.glClearColor(bg.redF(), bg.greenF(), bg.blueF(), 1.0)
        gf.glClear(gl.GL_COLOR_BUFFER_BIT)

        if (
            self._image is not None
            and not self._image.isNull()
            and not self._renderer.has_texture()
        ):
            self._renderer.upload_texture(self._image)
            straighten, rotate_steps, _ = self._rotation_parameters()
            self._update_cover_scale(straighten, rotate_steps)
        if not self._renderer.has_texture():
            return

        effective_scale = self._transform_controller.get_effective_scale()
        cover_scale = self._transform_controller.get_image_cover_scale()

        time_value = time.monotonic() - self._time_base
        
        view_pan = self._transform_controller.get_pan_pixels()

        effective_adjustments: dict[str, float] | Mapping[str, float]
        if self._crop_controller.is_active():
            effective_adjustments = dict(self._adjustments)
            # During crop interactions we want to preview the entire photo with
            # a translucent overlay.  The fragment shader drives the crop
            # window entirely from the ``Crop_*`` uniforms, therefore we
            # override those values on-the-fly instead of mutating
            # ``self._adjustments`` (which stores the persisted edit state).
            effective_adjustments.update(
                {
                    "Crop_CX": 0.5,
                    "Crop_CY": 0.5,
                    "Crop_W": 1.0,
                    "Crop_H": 1.0,
                }
            )
        else:
            effective_adjustments = self._adjustments

        logical_tex_w, logical_tex_h = self._display_texture_dimensions()

        self._renderer.render(
            view_width=float(vw),
            view_height=float(vh),
            scale=effective_scale,
            pan=view_pan,
            adjustments=effective_adjustments,
            time_value=time_value,
            img_scale=cover_scale,
            logical_tex_size=(float(logical_tex_w), float(logical_tex_h)),
        )

        if self._crop_controller.is_active():
            crop_rect = self._crop_controller.current_crop_rect_pixels()
            if crop_rect is not None:
                self._renderer.draw_crop_overlay(
                    view_width=float(vw),
                    view_height=float(vh),
                    crop_rect=crop_rect,
                    faded=self._crop_controller.is_faded_out(),
                )

    # --------------------------- Crop helpers ---------------------------

    def setCropMode(self, enabled: bool, values: Mapping[str, float] | None = None) -> None:
        was_active = self._crop_controller.is_active()
        source_values = values if values is not None else self._adjustments
        logical_values = geometry.logical_crop_mapping_from_texture(source_values)
        self._crop_controller.set_active(enabled, logical_values)
        if enabled and not was_active:
            self._cancel_auto_crop_lock()
            self._transform_controller.reset_zoom()
        elif not enabled and was_active:
            self.reset_zoom()

    def crop_values(self) -> dict[str, float]:
        return self._crop_controller.get_crop_values()

    def start_perspective_interaction(self) -> None:
        """Snapshot the crop before a perspective slider drag begins."""

        self._crop_controller.start_perspective_interaction()

    def end_perspective_interaction(self) -> None:
        """Clear the cached baseline crop captured for perspective drags."""

        self._crop_controller.end_perspective_interaction()

    def _update_crop_perspective_state(self) -> None:
        """Forward the latest perspective sliders to the crop controller."""

        if not hasattr(self, "_crop_controller") or self._crop_controller is None:
            return
        vertical = float(self._adjustments.get("Perspective_Vertical", 0.0))
        horizontal = float(self._adjustments.get("Perspective_Horizontal", 0.0))
        straighten, rotate_steps, flip = self._rotation_parameters()
        self._crop_controller.update_perspective(
            vertical,
            horizontal,
            straighten,
            rotate_steps,
            flip,
        )
        self._update_cover_scale(straighten, rotate_steps)

    def _rotation_parameters(self) -> tuple[float, int, bool]:
        """Return the straighten angle, rotate steps, and flip toggle."""

        straighten = float(self._adjustments.get("Crop_Straighten", 0.0))
        rotate_steps = int(float(self._adjustments.get("Crop_Rotate90", 0.0)))
        flip = bool(self._adjustments.get("Crop_FlipH", False))
        return straighten, rotate_steps, flip

    def _update_cover_scale(self, straighten_deg: float, rotate_steps: int) -> None:
        """Compute the rotation cover scale and forward it to the transform controller."""

        if not self._renderer or not self._renderer.has_texture():
            self._transform_controller.set_image_cover_scale(1.0)
            return
        
        # When rotation is handled in the shader (current implementation), cover_scale
        # only needs to account for straighten angle, not the 90° discrete rotations.
        # Since logical dimensions are already used in ViewTransformController, and
        # shader rotation maps logical→physical, we can simplify:
        if abs(straighten_deg) <= 1e-5:
            # No straighten angle: no cover scale needed
            self._transform_controller.set_image_cover_scale(1.0)
            return
            
        # If there's a straighten angle, we still need cover scale calculation
        tex_w, tex_h = self._texture_dimensions()
        if tex_w <= 0 or tex_h <= 0:
            self._transform_controller.set_image_cover_scale(1.0)
            return
            
        display_w, display_h = self._display_texture_dimensions()
        view_width, view_height = self._view_dimensions_device_px()
        
        base_scale = compute_fit_to_view_scale(
            (display_w, display_h), float(view_width), float(view_height)
        )
        
        # For straighten, use logical dims with physical bounds checking
        cover_scale = compute_rotation_cover_scale(
            (display_w, display_h),
            base_scale,
            straighten_deg,
            rotate_steps,
            physical_texture_size=(tex_w, tex_h),
        )
        
        self._transform_controller.set_image_cover_scale(cover_scale)





    # --------------------------- Coordinate transformations ---------------------------

    def _view_dimensions_device_px(self) -> tuple[float, float]:
        return self._transform_controller._get_view_dimensions_device_px()

    def _screen_to_world(self, screen_pt: QPointF) -> QPointF:
        """Map a Qt screen coordinate to the GL view's centre-origin space."""
        return self._transform_controller.convert_screen_to_world(screen_pt)

    def _world_to_screen(self, world_vec: QPointF) -> QPointF:
        """Convert a GL centre-origin vector into a Qt screen coordinate."""
        return self._transform_controller.convert_world_to_screen(world_vec)

    def _effective_scale(self) -> float:
        if not self._renderer or not self._renderer.has_texture():
            return 1.0
        return self._transform_controller.get_effective_scale()

    def _image_center_pixels(self) -> QPointF:
        if not self._renderer or not self._renderer.has_texture():
            return QPointF(0.0, 0.0)
        return self._transform_controller.get_image_center_pixels()

    def _set_image_center_pixels(self, center: QPointF, *, scale: float | None = None) -> None:
        if not self._renderer or not self._renderer.has_texture():
            return
        self._transform_controller.apply_image_center_pixels(center, scale)

    def _image_to_viewport(self, x: float, y: float) -> QPointF:
        if not self._renderer or not self._renderer.has_texture():
            return QPointF()
        return self._transform_controller.convert_image_to_viewport(x, y)

    def _viewport_to_image(self, point: QPointF) -> QPointF:
        if not self._renderer or not self._renderer.has_texture():
            return QPointF()
        return self._transform_controller.convert_viewport_to_image(point)

    def _create_clamp_function(self):
        """Create a clamp function with access to viewer state."""
        def clamp_fn(center: QPointF, scale: float) -> QPointF:
            return clamp_center_to_texture_bounds(
                center=center,
                scale=scale,
                texture_dimensions=self._display_texture_dimensions(),
                view_dimensions=self._view_dimensions_device_px(),
                has_texture=bool(self._renderer and self._renderer.has_texture()),
            )
        return clamp_fn

    # --------------------------- Viewport helpers ---------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        handled = self._input_handler.handle_mouse_press(event)
        if not handled:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        handled = self._input_handler.handle_mouse_move(event)
        if not handled:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        handled = self._input_handler.handle_mouse_release(event)
        if not handled:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        handled = self._input_handler.handle_double_click_with_window(event, self.window())
        if handled:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._input_handler.handle_wheel(event)

    def resizeGL(self, w: int, h: int) -> None:
        gf = self._gl_funcs
        if not gf:
            return
        dpr = self.devicePixelRatioF()
        gf.glViewport(0, 0, max(1, int(round(w * dpr))), max(1, int(round(h * dpr))))

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._loading_overlay.update_geometry(self.size())
        if self._auto_crop_view_locked and not self._crop_controller.is_active():
            self._reapply_locked_crop_view()
        straighten, rotate_steps, _ = self._rotation_parameters()
        self._update_cover_scale(straighten, rotate_steps)

    # --------------------------- Cursor management and helpers ---------------------------

    def _handle_cursor_change(self, cursor: Qt.CursorShape | None) -> None:
        """Handle cursor change request from controllers."""
        if cursor is None:
            self.unsetCursor()
        else:
            self.setCursor(cursor)

    def _texture_dimensions(self) -> tuple[int, int]:
        """Return the current texture size or ``(0, 0)`` when unavailable."""

        if self._renderer is not None and self._renderer.has_texture():
            return self._renderer.texture_size()
        if self._image is not None and not self._image.isNull():
            return (self._image.width(), self._image.height())
        return (0, 0)

    def _display_texture_dimensions(self) -> tuple[int, int]:
        """Return the logical texture dimensions used for fit-to-view math."""

        tex_w, tex_h = self._texture_dimensions()
        if tex_w <= 0 or tex_h <= 0:
            return (tex_w, tex_h)
        rotate_steps = int(float(self._adjustments.get("Crop_Rotate90", 0.0)))
        if rotate_steps % 2:
            # When the user rotates the photo by 90° or 270° the shader renders a
            # portrait-aligned frame even though the underlying texture upload
            # remains landscape.  Swapping the logical dimensions keeps the
            # transform controller's fit-to-view baseline consistent with the
            # rendered orientation so we no longer squish the frame into the
            # previous aspect ratio.
            return (tex_h, tex_w)
        return (tex_w, tex_h)

    def _frame_crop_if_available(self) -> bool:
        """Frame the active crop rectangle if the adjustments define one."""

        if self._crop_controller.is_active():
            return False
        crop_rect = self._compute_crop_rect_pixels()
        if crop_rect is None:
            self._auto_crop_view_locked = False
            return False
        if self._transform_controller.frame_texture_rect(crop_rect):
            self._auto_crop_view_locked = True
            return True
        return False

    def _reapply_locked_crop_view(self) -> None:
        """Re-apply the stored crop framing after resizes or adjustment edits."""

        if not self._auto_crop_view_locked:
            return
        crop_rect = self._compute_crop_rect_pixels()
        if crop_rect is None:
            self._auto_crop_view_locked = False
            return
        if not self._transform_controller.frame_texture_rect(crop_rect):
            self._auto_crop_view_locked = False
            return

    def _cancel_auto_crop_lock(self) -> None:
        """Disable auto-crop framing so manual gestures stay respected."""

        self._auto_crop_view_locked = False

    def _compute_crop_rect_pixels(self) -> QRectF | None:
        """Return the crop rectangle expressed in texture pixels."""

        texture_size = self._display_texture_dimensions()
        tex_w, tex_h = texture_size

        # Convert the session's texture-space crop tuple into the logical/display space so the
        # overlay and auto-framing routines mirror the visual orientation on screen.  The
        # conversion swaps axes when the photo is rotated by 90°/270° while keeping the stored
        # crop coordinates anchored to the unrotated texture, matching the "data stays still,
        # view moves" policy from the demo reference.
        crop_cx, crop_cy, crop_w, crop_h = geometry.logical_crop_from_texture(self._adjustments)
        return crop_logic.compute_crop_rect_pixels(crop_cx, crop_cy, crop_w, crop_h, tex_w, tex_h)

    def _handle_crop_interaction_changed(
        self, cx: float, cy: float, width: float, height: float
    ) -> None:
        """Convert logical crop updates back to texture space before emitting."""

        rotate_steps = geometry.get_rotate_steps(self._adjustments)
        tex_cx, tex_cy, tex_w, tex_h = geometry.logical_crop_to_texture(
            (float(cx), float(cy), float(width), float(height)),
            rotate_steps,
        )
        self.cropChanged.emit(tex_cx, tex_cy, tex_w, tex_h)

    def _fit_to_view_scale(self, view_width: float, view_height: float) -> float:
        """Return the baseline scale that fits the texture within the viewport."""

        texture_size = self._display_texture_dimensions()
        return compute_fit_to_view_scale(texture_size, view_width, view_height)

    def _apply_surface_color(self) -> None:
        """Synchronise the widget stylesheet and GL clear colour backdrop."""

        target = self._surface_override or self._default_surface_color
        self.setStyleSheet(f"background-color: {target.name()}; border: none;")
        self._backdrop_color = QColor(target)
        self.update()
