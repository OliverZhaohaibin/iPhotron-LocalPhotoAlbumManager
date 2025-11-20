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
from PySide6.QtWidgets import QLabel

from .gl_crop_controller import CropInteractionController
from .gl_renderer import GLRenderer
from .view_transform_controller import (
    ViewTransformController,
    compute_fit_to_view_scale,
    compute_rotation_cover_scale,
)

_LOGGER = logging.getLogger(__name__)

# 如果你的工程没有这个函数，可以改成固定背景色
try:
    from ..palette import viewer_surface_color  # type: ignore
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
        self._current_image_source: object | None = None
        self._live_replay_enabled: bool = False

        # Track the viewer surface colour so immersive mode can temporarily
        # switch to a pure black canvas.  ``viewer_surface_color`` returns a
        # palette-derived colour string, which we normalise to ``QColor`` for
        # reliable comparisons and GL clear colour conversion.
        self._default_surface_color = self._normalise_colour(viewer_surface_color(self))
        self._surface_override: QColor | None = None
        self._backdrop_color: QColor = QColor(self._default_surface_color)
        self._apply_surface_color()

        # ``_time_base`` anchors the monotonic clock used by the shader grain generator.  Resetting
        # the start time keeps the uniform values numerically small even after long application
        # sessions.
        self._time_base = time.monotonic()

        self._loading_overlay = QLabel("Loading…", self)
        self._loading_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self._loading_overlay.setStyleSheet(
            "background-color: rgba(0, 0, 0, 128); color: white; font-size: 18px;"
        )
        self._loading_overlay.hide()
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
            clamp_image_center_to_crop=self._clamp_image_center_to_crop,
            transform_controller=self._transform_controller,
            on_crop_changed=self._handle_crop_interaction_changed,
            on_cursor_change=self._handle_cursor_change,
            on_request_update=self.update,
            timer_parent=self,
        )
        self._auto_crop_view_locked: bool = False
        self._update_crop_perspective_state()

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

        reuse_existing_texture = (
            image_source is not None and image_source == getattr(self, "_current_image_source", None)
        )

        if reuse_existing_texture and image is not None and not image.isNull():
            # Skip the heavy texture re-upload when the caller explicitly
            # reports that the source asset is unchanged.  Only the adjustment
            # uniforms need to be refreshed in this scenario.
            self.set_adjustments(adjustments)
            if reset_view:
                self.reset_zoom()
            return

        self._current_image_source = image_source
        self._image = image
        self._adjustments = dict(adjustments or {})
        self._update_crop_perspective_state()
        self._loading_overlay.hide()
        self._time_base = time.monotonic()

        if image is None or image.isNull():
            self._current_image_source = None
            self._auto_crop_view_locked = False
            self._transform_controller.set_image_cover_scale(1.0)
            renderer = self._renderer
            if renderer is not None:
                gl_context = self.context()
                if gl_context is not None:
                    # ``set_image(None)`` is frequently triggered while the widget is
                    # still hidden, meaning the GL context (and therefore the
                    # renderer) may not have been created yet.  Guard the cleanup so
                    # we only touch GPU state when a live context is bound.
                    self.makeCurrent()
                    try:
                        renderer.delete_texture()
                    finally:
                        self.doneCurrent()

        if reset_view:
            # Reset the interactive transform so every new asset begins in the
            # same fit-to-window baseline that the QWidget-based viewer
            # exposes.  ``reset_view`` lets callers preserve the zoom when the
            # user toggles between detail and edit modes.
            self.reset_zoom()
    def set_placeholder(self, pixmap) -> None:
        """Display *pixmap* without changing the tracked image source."""

        if pixmap and not pixmap.isNull():
            self.set_image(pixmap.toImage(), {}, image_source=self._current_image_source)
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
            image_source=image_source if image_source is not None else self._current_image_source,
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
            logical_values = self._logical_crop_mapping_from_texture(mapped_adjustments)
            self._crop_controller.set_active(True, logical_values)
        if self._auto_crop_view_locked and not self._crop_controller.is_active():
            self._reapply_locked_crop_view()
        self.update()

    def current_image_source(self) -> object | None:
        """Return the identifier describing the currently displayed image."""

        return getattr(self, "_current_image_source", None)

    def pixmap(self) -> QPixmap | None:
        """Return a defensive copy of the currently displayed frame."""

        if self._image is None or self._image.isNull():
            return None
        return QPixmap.fromImage(self._image)

    def set_loading(self, loading: bool) -> None:
        """Toggle the translucent loading overlay."""

        if loading:
            self._loading_overlay.setVisible(True)
            self._loading_overlay.raise_()
            self._loading_overlay.resize(self.size())
        else:
            self._loading_overlay.hide()

    def viewport_widget(self) -> GLImageViewer:
        """Expose the drawable widget for API parity with :class:`ImageViewer`."""

        return self

    def set_live_replay_enabled(self, enabled: bool) -> None:
        self._live_replay_enabled = bool(enabled)

    def set_wheel_action(self, action: str) -> None:
        self._transform_controller.set_wheel_action(action)

    def set_surface_color_override(self, colour: str | None) -> None:
        """Override the viewer backdrop with *colour* or restore the default."""

        if colour is None:
            self._surface_override = None
        else:
            self._surface_override = self._normalise_colour(colour)
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

        rotated_steps = (self._current_rotate_steps() - 1) % 4

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
        if target_size.isEmpty():
            _LOGGER.warning("render_offscreen_image: target size was empty")
            return QImage()

        if self.context() is None:
            _LOGGER.warning("render_offscreen_image: no OpenGL context available")
            return QImage()

        if self._image is None or self._image.isNull():
            _LOGGER.warning("render_offscreen_image: no source image bound to the viewer")
            return QImage()

        if self._renderer is None:
            _LOGGER.warning("render_offscreen_image: renderer not initialized")
            return QImage()

        self.makeCurrent()
        try:
            return self._renderer.render_offscreen_image(
                self._image,
                adjustments or self._adjustments,
                target_size,
                time_base=self._time_base,
            )
        finally:
            self.doneCurrent()

        return QImage()

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

        if self._image is not None and not self._image.isNull() and not self._renderer.has_texture():
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
        logical_values = self._logical_crop_mapping_from_texture(values)
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
        
        base_scale = compute_fit_to_view_scale((display_w, display_h), float(view_width), float(view_height))
        
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

    def _clamp_image_center_to_crop(self, center: QPointF, scale: float) -> QPointF:
        """Return *center* limited so the viewport never exposes empty pixels.

        The demo reference keeps the camera free to roam while the crop-specific
        model transform guarantees that the crop rectangle only samples valid
        pixels.  To mirror that behaviour we clamp the *viewport* centre purely
        against the texture bounds.  As soon as the viewport half extents exceed
        the texture half extents we collapse the permissible interval to the
        image midpoint, matching the demo's behaviour once the frame is larger
        than the source.
        """

        if (
            not self._renderer
            or not self._renderer.has_texture()
            or scale <= 1e-9
        ):
            return center

        # Use the rotation-aware logical dimensions so the clamp aligns with the visible frame
        # after 90°/270° rotations. Physical texture sizes ignore the width/height swap and would
        # produce asymmetric bounds (e.g. allowing black bars on one axis and over-clamping on the
        # other).
        tex_w, tex_h = self._display_texture_dimensions()
        vw, vh = self._view_dimensions_device_px()

        half_view_w = (float(vw) / float(scale)) * 0.5
        half_view_h = (float(vh) / float(scale)) * 0.5

        tex_half_w = float(tex_w) * 0.5
        tex_half_h = float(tex_h) * 0.5

        min_center_x = half_view_w
        max_center_x = float(tex_w) - half_view_w

        if min_center_x > max_center_x:
            min_center_x = tex_half_w
            max_center_x = tex_half_w

        min_center_y = half_view_h
        max_center_y = float(tex_h) - half_view_h

        if min_center_y > max_center_y:
            min_center_y = tex_half_h
            max_center_y = tex_half_h

        clamped_x = max(min_center_x, min(max_center_x, float(center.x())))
        clamped_y = max(min_center_y, min(max_center_y, float(center.y())))

        clamped_x = max(0.0, min(float(tex_w), clamped_x))
        clamped_y = max(0.0, min(float(tex_h), clamped_y))
        return QPointF(clamped_x, clamped_y)

    # --------------------------- Viewport helpers ---------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._crop_controller.is_active() and event.button() == Qt.LeftButton:
            self._crop_controller.handle_mouse_press(event)
            return
        if event.button() == Qt.LeftButton:
            if self._live_replay_enabled:
                self.replayRequested.emit()
            else:
                self._cancel_auto_crop_lock()
                self._transform_controller.handle_mouse_press(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._crop_controller.is_active():
            self._crop_controller.handle_mouse_move(event)
            return
        if not self._live_replay_enabled:
            self._transform_controller.handle_mouse_move(event)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._crop_controller.is_active() and event.button() == Qt.LeftButton:
            self._crop_controller.handle_mouse_release(event)
            return
        if not self._live_replay_enabled:
            self._transform_controller.handle_mouse_release(event)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            top_level = self.window()
            # Toggle immersive mode depending on the top-level window state.
            if top_level is not None and top_level.isFullScreen():
                self.fullscreenExitRequested.emit()
            else:
                self.fullscreenToggleRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._crop_controller.is_active():
            self._crop_controller.handle_wheel(event)
            return
        self._cancel_auto_crop_lock()
        self._transform_controller.handle_wheel(event)

    def resizeGL(self, w: int, h: int) -> None:
        gf = self._gl_funcs
        if not gf:
            return
        dpr = self.devicePixelRatioF()
        gf.glViewport(0, 0, max(1, int(round(w * dpr))), max(1, int(round(h * dpr))))

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._loading_overlay is not None:
            self._loading_overlay.resize(self.size())
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
        if tex_w <= 0 or tex_h <= 0:
            return None

        # Convert the session's texture-space crop tuple into the logical/display space so the
        # overlay and auto-framing routines mirror the visual orientation on screen.  The
        # conversion swaps axes when the photo is rotated by 90°/270° while keeping the stored
        # crop coordinates anchored to the unrotated texture, matching the "data stays still,
        # view moves" policy from the demo reference.
        crop_cx, crop_cy, crop_w, crop_h = self._logical_crop_from_texture()
        if not self._has_valid_crop(crop_w, crop_h):
            return None

        tex_w_f = float(tex_w)
        tex_h_f = float(tex_h)
        width_px = max(1.0, min(tex_w_f, crop_w * tex_w_f))
        height_px = max(1.0, min(tex_h_f, crop_h * tex_h_f))

        center_x = max(0.0, min(tex_w_f, crop_cx * tex_w_f))
        center_y = max(0.0, min(tex_h_f, crop_cy * tex_h_f))

        half_w = width_px * 0.5
        half_h = height_px * 0.5

        left = max(0.0, center_x - half_w)
        top = max(0.0, center_y - half_h)
        right = min(tex_w_f, center_x + half_w)
        bottom = min(tex_h_f, center_y + half_h)

        rect_width = max(1.0, right - left)
        rect_height = max(1.0, bottom - top)
        epsilon = 1e-6
        if rect_width >= tex_w_f - epsilon and rect_height >= tex_h_f - epsilon:
            return None
        return QRectF(left, top, rect_width, rect_height)

    @staticmethod
    def _has_valid_crop(crop_w: float, crop_h: float) -> bool:
        """Return ``True`` when the adjustments describe a cropped image."""

        epsilon = 1e-3
        return (crop_w < 1.0 - epsilon or crop_h < 1.0 - epsilon) and crop_w > 0.0 and crop_h > 0.0

    def _handle_crop_interaction_changed(
        self, cx: float, cy: float, width: float, height: float
    ) -> None:
        """Convert logical crop updates back to texture space before emitting."""

        tex_cx, tex_cy, tex_w, tex_h = self._logical_crop_to_texture(
            (float(cx), float(cy), float(width), float(height)),
            self._current_rotate_steps(),
        )
        self.cropChanged.emit(tex_cx, tex_cy, tex_w, tex_h)

    def _current_rotate_steps(self, values: Mapping[str, float] | None = None) -> int:
        """Return the normalised quarter-turn rotation counter."""

        source = values or self._adjustments
        return int(float(source.get("Crop_Rotate90", 0.0))) % 4

    @staticmethod
    def _clamp_unit(value: float) -> float:
        """Clamp *value* into the ``[0, 1]`` interval."""

        return max(0.0, min(1.0, float(value)))

    def _normalised_crop_from_mapping(
        self, values: Mapping[str, float] | None = None
    ) -> tuple[float, float, float, float]:
        """Extract a normalised crop tuple from the provided mapping."""

        source = values or self._adjustments
        cx = self._clamp_unit(source.get("Crop_CX", 0.5))
        cy = self._clamp_unit(source.get("Crop_CY", 0.5))
        width = self._clamp_unit(source.get("Crop_W", 1.0))
        height = self._clamp_unit(source.get("Crop_H", 1.0))
        return (cx, cy, width, height)

    def _logical_crop_from_texture(
        self, values: Mapping[str, float] | None = None
    ) -> tuple[float, float, float, float]:
        """Convert texture-space crop values into the rotation-aware logical space."""

        cx, cy, width, height = self._normalised_crop_from_mapping(values)
        return self._texture_crop_to_logical(
            (cx, cy, width, height),
            self._current_rotate_steps(values),
        )

    def _logical_crop_mapping_from_texture(
        self, values: Mapping[str, float] | None = None
    ) -> dict[str, float]:
        """Return a mapping of logical crop values derived from texture space."""

        logical_cx, logical_cy, logical_w, logical_h = self._logical_crop_from_texture(values)
        return {
            "Crop_CX": logical_cx,
            "Crop_CY": logical_cy,
            "Crop_W": logical_w,
            "Crop_H": logical_h,
        }

    def _texture_crop_to_logical(
        self, crop: tuple[float, float, float, float], rotate_steps: int
    ) -> tuple[float, float, float, float]:
        """Map texture-space crop values into logical space for UI rendering.

        Texture coordinates remain the canonical storage format (``Crop_*`` in the session),
        while logical coordinates mirror whatever orientation is currently visible to the user.
        The mapping therefore rotates the centre and swaps the width/height whenever the image is
        turned by 90° increments so that overlays and zoom-to-crop computations operate in the same
        frame as the on-screen preview.
        """

        tcx, tcy, tw, th = crop
        if rotate_steps == 0:
            return (tcx, tcy, tw, th)
        if rotate_steps == 1:
            # Step 1: 90° CW (270° CCW) - texture TOP becomes visual RIGHT
            # Transformation: (x', y') = (1-y, x)
            return (
                self._clamp_unit(1.0 - tcy),
                self._clamp_unit(tcx),
                self._clamp_unit(th),
                self._clamp_unit(tw),
            )
        if rotate_steps == 2:
            return (
                self._clamp_unit(1.0 - tcx),
                self._clamp_unit(1.0 - tcy),
                self._clamp_unit(tw),
                self._clamp_unit(th),
            )
        # Step 3: 90° CCW (270° CW) - texture TOP becomes visual LEFT  
        # Transformation: (x', y') = (y, 1-x)
        return (
            self._clamp_unit(tcy),
            self._clamp_unit(1.0 - tcx),
            self._clamp_unit(th),
            self._clamp_unit(tw),
        )

    def _logical_crop_to_texture(
        self, crop: tuple[float, float, float, float], rotate_steps: int
    ) -> tuple[float, float, float, float]:
        """Convert logical crop values back into the invariant texture-space frame.

        This is the inverse of :meth:`_texture_crop_to_logical`.  Interaction handlers edit crops in
        logical space (matching the rotated display), so we rotate the updated rectangle back into
        the texture frame before persisting it.  Keeping the stored data immutable with respect to
        rotation prevents accumulation of floating-point error across repeated 90° turns and keeps
        the controller logic aligned with the shader's texture-space crop uniforms.
        """

        lcx, lcy, lw, lh = crop
        if rotate_steps == 0:
            return (
                self._clamp_unit(lcx),
                self._clamp_unit(lcy),
                self._clamp_unit(lw),
                self._clamp_unit(lh),
            )
        if rotate_steps == 1:
            # Step 1 inverse: (x, y) = (y', 1-x') 
            # (reverse of the forward 90° CW transformation)
            return (
                self._clamp_unit(lcy),
                self._clamp_unit(1.0 - lcx),
                self._clamp_unit(lh),
                self._clamp_unit(lw),
            )
        if rotate_steps == 2:
            return (
                self._clamp_unit(1.0 - lcx),
                self._clamp_unit(1.0 - lcy),
                self._clamp_unit(lw),
                self._clamp_unit(lh),
            )
        # Step 3 inverse: (x, y) = (1-y', x')
        # (reverse of the forward 90° CCW transformation)
        return (
            self._clamp_unit(1.0 - lcy),
            self._clamp_unit(lcx),
            self._clamp_unit(lh),
            self._clamp_unit(lw),
        )

    def _fit_to_view_scale(self, view_width: float, view_height: float) -> float:
        """Return the baseline scale that fits the texture within the viewport."""

        texture_size = self._display_texture_dimensions()
        return compute_fit_to_view_scale(texture_size, view_width, view_height)

    @staticmethod
    def _normalise_colour(value: QColor | str) -> QColor:
        """Return a valid ``QColor`` derived from *value* (defaulting to black)."""

        colour = QColor(value)
        if not colour.isValid():
            colour = QColor("#000000")
        return colour

    def _apply_surface_color(self) -> None:
        """Synchronise the widget stylesheet and GL clear colour backdrop."""

        target = self._surface_override or self._default_surface_color
        self.setStyleSheet(f"background-color: {target.name()}; border: none;")
        self._backdrop_color = QColor(target)
        self.update()
