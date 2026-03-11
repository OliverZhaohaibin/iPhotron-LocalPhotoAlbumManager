"""
GPU-accelerated image viewer (pure OpenGL texture upload; pixel-accurate zoom/pan).
- Ensures magnification samples the ORIGINAL pixels (no Qt/FBO resampling).
- Uses GL 3.3 Core, VAO/VBO, and a raw glTexImage2D + glTexSubImage2D upload path.
- Rendered inside a QRhiWidget via beginExternal()/endExternal() so that both the
  image viewer and the QRhiWidget-based video renderer share a unified rendering
  backend, eliminating the intermittent GPU state corruption (花屏) that occurred
  when mixing QOpenGLWidget and QRhiWidget in the same QStackedWidget.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from OpenGL import GL as gl
from PySide6.QtCore import QPointF, QSize, Qt, Signal, QRectF
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QPixmap,
    QRhiDepthStencilClearValue,
    QWheelEvent,
)
from PySide6.QtOpenGL import (
    QOpenGLFunctions_3_3_Core,
)
from PySide6.QtWidgets import QRhiWidget

from ..gl_crop_controller import CropInteractionController
from ..gl_renderer import GLRenderer
from ..view_transform_controller import ViewTransformController

from . import crop_viewport
from . import geometry
from .adjustment_applicator import AdjustmentApplicator
from .components import LoadingOverlay
from .fullscreen_handler import FullscreenHandler
from .input_handler import InputEventHandler
from .offscreen import OffscreenRenderer
from .resources import TextureResourceManager
from .utils import normalise_colour
from .zoom_controller import ZoomController

_LOGGER = logging.getLogger(__name__)

# 如果你的工程没有这个函数，可以改成固定背景色
try:
    from ...palette import viewer_surface_color  # type: ignore
except Exception:
    def viewer_surface_color(_):  # fallback
        return QColor(0, 0, 0)


class GLImageViewer(QRhiWidget):
    """A QWidget that displays GPU-rendered images with pixel-accurate zoom.

    Internally uses raw OpenGL 3.3 Core via ``beginExternal()`` /
    ``endExternal()`` within the QRhi render pass.  This makes it a
    QRhiWidget, the same base class as the video renderer, so that both
    widgets share a single rendering backend inside the ``QStackedWidget``
    and avoid the intermittent GPU state corruption that occurred when
    mixing ``QOpenGLWidget`` and ``QRhiWidget``.
    """

    # Signals（保持与旧版一致）
    replayRequested = Signal()
    zoomChanged = Signal(float)
    nextItemRequested = Signal()
    prevItemRequested = Signal()
    fullscreenExitRequested = Signal()
    fullscreenToggleRequested = Signal()
    cropChanged = Signal(float, float, float, float)
    cropInteractionStarted = Signal()
    cropInteractionFinished = Signal()
    colorPicked = Signal(float, float, float)
    firstFrameReady = Signal()
    """Emitted once after the first opaque frame has been rendered."""

    def __init__(self, parent: QRhiWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)

        # Use the same OpenGL backend as the QRhiWidget-based video renderer
        # so that both widgets share a single rendering infrastructure.
        # Must be called in the constructor — Qt docs state that calling
        # setApi() after the widget is shown may have no effect.
        self.setApi(QRhiWidget.Api.OpenGL)

        # Declare that this widget always produces fully opaque output so
        # the compositor never expects transparency from the first paint.
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        # Prevent the main window's WA_TranslucentBackground from cascading
        # into this widget and causing transparent first-frame flashes.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self._gl_funcs: QOpenGLFunctions_3_3_Core | None = None
        self._renderer: GLRenderer | None = None
        self._gl_initialized = False
        self._first_render_done = False

        # 状态
        self._image: QImage | None = None
        self._adjustments: dict[str, Any] = {}
        self._eyedropper_active = False

        # Texture resource manager
        self._texture_manager = TextureResourceManager(
            renderer_provider=lambda: self._renderer,
            context_provider=lambda: self.rhi(),
            make_current=self._make_gl_current,
            done_current=self._done_gl_current,
        )

        # Adjustment LUT applicator
        self._adjustment_applicator = AdjustmentApplicator(
            renderer_provider=lambda: self._renderer,
            make_current=self._make_gl_current,
            done_current=self._done_gl_current,
        )

        # Surface colour / fullscreen handler
        self._fullscreen_handler = FullscreenHandler(
            default_color=normalise_colour(viewer_surface_color(self)),
            set_stylesheet=self.setStyleSheet,
            request_update=self.update,
        )
        self._fullscreen_handler._apply()

        # ``_time_base`` anchors the monotonic clock used by the shader grain generator.
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

        # Coordinate-transform helper
        self._zoom_ctrl = ZoomController(
            transform_controller=self._transform_controller,
            renderer_provider=lambda: self._renderer,
            display_texture_dimensions=self._display_texture_dimensions,
        )

        # Crop interaction controller
        self._crop_controller = CropInteractionController(
            texture_size_provider=self._display_texture_dimensions,
            clamp_image_center_to_crop=self._zoom_ctrl.create_clamp_function(),
            transform_controller=self._transform_controller,
            on_crop_changed=self._handle_crop_interaction_changed,
            on_cursor_change=self._handle_cursor_change,
            on_request_update=self.update,
            timer_parent=self,
            on_interaction_started=self.cropInteractionStarted.emit,
            on_interaction_finished=self.cropInteractionFinished.emit,
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

    # ------------------------------------------------------------------
    # GL context helpers (replace QOpenGLWidget.makeCurrent/doneCurrent)
    # ------------------------------------------------------------------
    def _make_gl_current(self) -> None:
        """Make the underlying OpenGL context current for raw GL calls.

        Used by helpers that need to issue GL calls outside the
        ``initialize()``/``render()`` cycle (e.g. texture deletion,
        LUT upload, offscreen render).
        """
        rhi = self.rhi()
        if rhi is not None:
            rhi.makeThreadLocalNativeContextCurrent()

    @staticmethod
    def _done_gl_current() -> None:
        """Release the GL context after out-of-render-cycle GL work.

        With ``QRhiWidget`` / ``QRhi`` the context lifetime is managed by
        the framework, so this is intentionally a no-op.  It exists solely
        to satisfy the callback signature expected by
        ``TextureResourceManager``, ``AdjustmentApplicator`` and
        ``OffscreenRenderer``.
        """

    # --------------------------- Public API ---------------------------

    def shutdown(self) -> None:
        """Clean up GL resources."""
        self._make_gl_current()
        try:
            if self._renderer is not None:
                self._renderer.destroy_resources()
        finally:
            self._done_gl_current()

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
                # Skip texture re-upload, only update adjustments. Preserve the
                # current zoom/pan state so adjustment previews stay anchored
                # to the user's active viewport.
                self.set_adjustments(adjustments)
                return

        # Update texture resource tracking
        self._texture_manager.set_image(image, image_source)
        self._image = image
        self._adjustments = dict(adjustments or {})
        self._update_crop_perspective_state()
        self._adjustment_applicator.update_curve_lut_if_needed(self._adjustments)
        self._adjustment_applicator.update_levels_lut_if_needed(self._adjustments)
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

    def set_adjustments(self, adjustments: Mapping[str, Any] | None = None) -> None:
        """Update the active adjustment uniforms without replacing the texture."""

        mapped_adjustments = dict(adjustments or {})
        self._adjustments = mapped_adjustments
        self._update_crop_perspective_state()

        # Handle curve LUT update if curve data changed
        self._adjustment_applicator.update_curve_lut_if_needed(mapped_adjustments)

        # Handle levels LUT update if levels data changed
        self._adjustment_applicator.update_levels_lut_if_needed(mapped_adjustments)

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

    def set_eyedropper_mode(self, active: bool) -> None:
        """Enable or disable eyedropper picking mode."""

        self._eyedropper_active = bool(active)
        if self._eyedropper_active:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.unsetCursor()

    def set_wheel_action(self, action: str) -> None:
        self._transform_controller.set_wheel_action(action)

    def set_surface_color_override(self, colour: str | None) -> None:
        """Override the viewer backdrop with *colour* or restore the default."""
        self._fullscreen_handler.set_surface_color_override(colour)

    def set_immersive_background(self, immersive: bool) -> None:
        """Toggle the pure black immersive backdrop used in immersive mode."""
        self._fullscreen_handler.set_immersive_background(immersive)

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
            context=self.rhi(),
            make_current=self._make_gl_current,
            done_current=self._done_gl_current,
            image=self._image,
            adjustments=adjustments or self._adjustments,
            target_size=target_size,
            time_base=self._time_base,
        )

    # --------------------------- GL lifecycle ---------------------------

    def initialize(self, cb) -> None:  # type: ignore[override]
        """QRhiWidget override: initialise raw GL resources once."""
        if self._gl_initialized:
            return
        rhi = self.rhi()
        if rhi is None:
            _LOGGER.warning("QRhi not available — image rendering disabled")
            return
        # Make the underlying OpenGL context current so we can issue raw GL
        # calls (create shaders, VAO, VBO, textures, …).
        rhi.makeThreadLocalNativeContextCurrent()
        self._gl_funcs = QOpenGLFunctions_3_3_Core()
        self._gl_funcs.initializeOpenGLFunctions()
        gf = self._gl_funcs

        if self._renderer is not None:
            self._renderer.destroy_resources()

        self._renderer = GLRenderer(gf, parent=self)
        self._renderer.initialize_resources()
        self._adjustment_applicator.update_curve_lut_if_needed(self._adjustments)
        self._adjustment_applicator.update_levels_lut_if_needed(self._adjustments)

        dpr = self.devicePixelRatioF()
        gf.glViewport(0, 0, int(self.width() * dpr), int(self.height() * dpr))
        self._gl_initialized = True

    def releaseResources(self) -> None:  # type: ignore[override]
        """QRhiWidget override: release GL resources."""
        self._gl_initialized = False
        if self._renderer is not None:
            rhi = self.rhi()
            if rhi is not None:
                # Ensure the underlying OpenGL context is current before
                # issuing raw GL deletes in GLRenderer.destroy_resources().
                rhi.makeThreadLocalNativeContextCurrent()
            self._renderer.destroy_resources()

    def render(self, cb) -> None:  # type: ignore[override]
        """QRhiWidget override: render the current image via raw OpenGL."""
        if not self._gl_initialized:
            # GL resources are not yet available but we MUST still clear the
            # render target with an opaque colour so the surface is never
            # transparent.  An early bare return would leave the texture
            # uninitialised, compositing as transparent under the main
            # window's WA_TranslucentBackground.
            bg = self._fullscreen_handler.backdrop_color
            cb.beginPass(
                self.renderTarget(),
                QColor.fromRgbF(bg.redF(), bg.greenF(), bg.blueF(), 1.0),
                QRhiDepthStencilClearValue(),
            )
            cb.endPass()
            self._emit_first_frame_ready()
            return
        gf = self._gl_funcs
        if gf is None or self._renderer is None:
            bg = self._fullscreen_handler.backdrop_color
            cb.beginPass(
                self.renderTarget(),
                QColor.fromRgbF(bg.redF(), bg.greenF(), bg.blueF(), 1.0),
                QRhiDepthStencilClearValue(),
            )
            cb.endPass()
            self._emit_first_frame_ready()
            return

        output_size = self.renderTarget().pixelSize()
        if output_size.isEmpty():
            return

        # Start a QRhi render pass (required by QRhiWidget) then immediately
        # switch to raw OpenGL via beginExternal()/endExternal().  This lets
        # us keep all existing GL 3.3 shader code unchanged while both
        # widgets share the same QRhi rendering infrastructure.
        cb.beginPass(
            self.renderTarget(),
            QColor(0, 0, 0, 255),
            QRhiDepthStencilClearValue(),
        )
        cb.beginExternal()

        # --- All raw OpenGL calls happen between beginExternal/endExternal ---
        vw = max(1, output_size.width())
        vh = max(1, output_size.height())
        gf.glViewport(0, 0, vw, vh)
        bg = self._fullscreen_handler.backdrop_color
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
            cb.endExternal()
            cb.endPass()
            self._emit_first_frame_ready()
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
            # Convert texture-space crop to logical-space for shader
            # Shader tests crop in pre-rotation space (uv_perspective),
            # so it needs logical-space crop parameters
            effective_adjustments = dict(self._adjustments)
            logical_crop = geometry.logical_crop_mapping_from_texture(self._adjustments)
            effective_adjustments.update(logical_crop)


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

        # --- End raw OpenGL block ---
        cb.endExternal()
        cb.endPass()
        self._emit_first_frame_ready()

    def _emit_first_frame_ready(self) -> None:
        """Notify listeners that the first opaque frame has been rendered."""
        if not self._first_render_done:
            self._first_render_done = True
            self.firstFrameReady.emit()

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
        logical_map = self._crop_controller.get_crop_values()
        logical_tuple = geometry.normalised_crop_from_mapping(logical_map)
        rotate_steps = geometry.get_rotate_steps(self._adjustments)

        tex_cx, tex_cy, tex_w, tex_h = geometry.logical_crop_to_texture(
            logical_tuple, rotate_steps
        )
        return {
            "Crop_CX": tex_cx,
            "Crop_CY": tex_cy,
            "Crop_W": tex_w,
            "Crop_H": tex_h,
        }

    def start_perspective_interaction(self) -> None:
        """Snapshot the crop before a perspective slider drag begins."""
        self._crop_controller.start_perspective_interaction()

    def end_perspective_interaction(self) -> None:
        """Clear the cached baseline crop captured for perspective drags."""
        self._crop_controller.end_perspective_interaction()

    def set_crop_aspect_ratio(self, ratio: float) -> None:
        """Forward the selected crop aspect-ratio constraint to the controller.

        Parameters
        ----------
        ratio:
            ``0.0`` for freeform, ``-1.0`` for *original* (uses the current
            image's native ratio), or a positive ``w/h`` value.
        """
        if ratio < 0:
            # "Original" – compute from the loaded texture
            tex_w, tex_h = self._display_texture_dimensions()
            if tex_w > 0 and tex_h > 0:
                ratio = float(tex_w) / float(tex_h)
            else:
                ratio = 0.0
        self._crop_controller.set_locked_aspect_ratio(ratio)

    def _update_crop_perspective_state(self) -> None:
        crop_viewport.update_crop_perspective_state(self)

    def _rotation_parameters(self) -> tuple[float, int, bool]:
        return crop_viewport.rotation_parameters(self)

    def _update_cover_scale(self, straighten_deg: float, rotate_steps: int) -> None:
        crop_viewport.update_cover_scale(self, straighten_deg, rotate_steps)


    # --------------------------- Viewport helpers ---------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._eyedropper_active:
            if self._handle_eyedropper_pick(event.position()):
                event.accept()
                return
        handled = self._input_handler.handle_mouse_press(event)
        if not handled:
            super().mousePressEvent(event)

    def _handle_eyedropper_pick(self, position: QPointF) -> bool:
        return crop_viewport.handle_eyedropper_pick(self, position)

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

    # QRhiWidget does not have a resizeGL callback.  The viewport is set
    # dynamically at the start of each render() call using
    # ``self.renderTarget().pixelSize()``, which automatically accounts for
    # DPR and window resizing.

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._loading_overlay.update_geometry(self.size())
        if self._auto_crop_view_locked and not self._crop_controller.is_active():
            self._reapply_locked_crop_view()
        straighten, rotate_steps, _ = self._rotation_parameters()
        self._update_cover_scale(straighten, rotate_steps)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # Request a fresh render when the widget becomes visible again
        # (e.g. after switching back from the video surface).
        self.update()

    # --------------------------- Cursor management and helpers ---------------------------

    def _handle_cursor_change(self, cursor: Qt.CursorShape | None) -> None:
        crop_viewport.handle_cursor_change(self, cursor)

    def _texture_dimensions(self) -> tuple[int, int]:
        return crop_viewport.texture_dimensions(self)

    def _display_texture_dimensions(self) -> tuple[int, int]:
        return crop_viewport.display_texture_dimensions(self)

    def _frame_crop_if_available(self) -> bool:
        return crop_viewport.frame_crop_if_available(self)

    def _reapply_locked_crop_view(self) -> None:
        crop_viewport.reapply_locked_crop_view(self)

    def _cancel_auto_crop_lock(self) -> None:
        crop_viewport.cancel_auto_crop_lock(self)

    def _compute_crop_rect_pixels(self) -> QRectF | None:
        return crop_viewport.compute_crop_rect_pixels(self)

    def _handle_crop_interaction_changed(
        self, cx: float, cy: float, width: float, height: float
    ) -> None:
        crop_viewport.handle_crop_interaction_changed(self, cx, cy, width, height)
