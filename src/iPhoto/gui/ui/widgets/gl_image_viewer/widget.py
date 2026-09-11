"""
GPU-accelerated image viewer with platform-selected QRhi rendering.

Windows/Linux keep the existing raw OpenGL texture path inside QRhiWidget.
macOS uses a pure QRhi path so photo and adjusted-video previews render on
Metal without ``beginExternal()`` raw GL interop.
"""

from __future__ import annotations

import logging
import sys
import time
import weakref
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QOpenGLContext,
    QPixmap,
    QRhi,
    QRhiCommandBuffer,
    QRhiDepthStencilClearValue,
    QWheelEvent,
)
from PySide6.QtWidgets import QRhiWidget

from iPhoto.gui.detail_profile import emit_detail_event, log_detail_profile

from ..render_backend import is_opengl_api, qrhi_api_name, select_qrhi_widget_api

QVideoFrame = None  # type: ignore[assignment, misc]
QVideoFrameFormat = None  # type: ignore[assignment, misc]
DecodedSurface = None
SurfaceByteBreakdown = None
SurfaceResidencyTracker = None
surface_resource_id = None
CropInteractionController = None
RhiImageRenderer = None
ViewTransformController = None
crop_viewport = None
geometry = None
AdjustmentApplicator = None
LoadingOverlay = None
FullscreenHandler = None
InputEventHandler = None
OffscreenRenderer = None
TextureResourceManager = None
normalise_colour = None
ZoomController = None

_LOGGER = logging.getLogger(__name__)


def _load_viewer_runtime_dependencies() -> None:
    """Load NumPy-backed viewer services only after the first shell paint."""

    global AdjustmentApplicator, CropInteractionController, DecodedSurface
    global FullscreenHandler, InputEventHandler, LoadingOverlay, OffscreenRenderer
    global RhiImageRenderer, SurfaceByteBreakdown, SurfaceResidencyTracker
    global TextureResourceManager, ViewTransformController, ZoomController
    global crop_viewport, geometry, normalise_colour, surface_resource_id
    if TextureResourceManager is not None:
        return

    from iPhoto.gui.detail_decode_backend import DecodedSurface as _DecodedSurface
    from iPhoto.gui.detail_surface_residency import (
        SurfaceByteBreakdown as _SurfaceByteBreakdown,
    )
    from iPhoto.gui.detail_surface_residency import (
        SurfaceResidencyTracker as _SurfaceResidencyTracker,
    )
    from iPhoto.gui.detail_surface_residency import (
        surface_resource_id as _surface_resource_id,
    )

    from ..gl_crop_controller import CropInteractionController as _CropInteractionController
    from ..rhi_image_renderer import RhiImageRenderer as _RhiImageRenderer
    from ..view_transform_controller import ViewTransformController as _ViewTransformController
    from . import crop_viewport as _crop_viewport
    from . import geometry as _geometry
    from .adjustment_applicator import AdjustmentApplicator as _AdjustmentApplicator
    from .components import LoadingOverlay as _LoadingOverlay
    from .fullscreen_handler import FullscreenHandler as _FullscreenHandler
    from .input_handler import InputEventHandler as _InputEventHandler
    from .offscreen import OffscreenRenderer as _OffscreenRenderer
    from .resources import TextureResourceManager as _TextureResourceManager
    from .utils import normalise_colour as _normalise_colour
    from .zoom_controller import ZoomController as _ZoomController

    DecodedSurface = _DecodedSurface
    SurfaceByteBreakdown = _SurfaceByteBreakdown
    SurfaceResidencyTracker = _SurfaceResidencyTracker
    surface_resource_id = _surface_resource_id
    CropInteractionController = _CropInteractionController
    RhiImageRenderer = _RhiImageRenderer
    ViewTransformController = _ViewTransformController
    crop_viewport = _crop_viewport
    geometry = _geometry
    AdjustmentApplicator = _AdjustmentApplicator
    LoadingOverlay = _LoadingOverlay
    FullscreenHandler = _FullscreenHandler
    InputEventHandler = _InputEventHandler
    OffscreenRenderer = _OffscreenRenderer
    TextureResourceManager = _TextureResourceManager
    normalise_colour = _normalise_colour
    ZoomController = _ZoomController


def _load_video_frame_types() -> None:
    """Load QtMultimedia frame types only when a decoded frame arrives."""

    global QVideoFrame, QVideoFrameFormat
    if QVideoFrame is not None and QVideoFrameFormat is not None:
        return
    try:
        from PySide6.QtMultimedia import (
            QVideoFrame as _QVideoFrame,
        )
        from PySide6.QtMultimedia import (
            QVideoFrameFormat as _QVideoFrameFormat,
        )
    except (ModuleNotFoundError, ImportError):  # pragma: no cover
        return
    QVideoFrame = _QVideoFrame
    QVideoFrameFormat = _QVideoFrameFormat

# Crop preview must not reuse the persisted [0, 1] crop mask.  Straightened or
# perspective-corrected source pixels can project outside that logical square;
# a deliberately oversized mask leaves the yellow overlay as the only crop
# boundary while the shader still rejects samples outside the real texture.
_CROP_PREVIEW_MASK_SIZE = 1_000_000.0
gl: Any | None = None
GLRenderer: Any | None = None


@dataclass(frozen=True, slots=True)
class _PendingVideoUploadState:
    pre_rotated: bool
    final_rotation: int


@dataclass(slots=True)
class _StillLodPromotion:
    surface: Any
    adjustments: dict[str, Any]
    generation: int
    phase: str
    previous_key: object | None
    activation_held: bool = False

    @property
    def key(self) -> object:
        return self.surface.decode_key


@dataclass(slots=True)
class _PendingStillActivation:
    key: object
    generation: int
    surface: Any
    purpose: Literal["presentation", "promotion", "rollback"]


@dataclass(frozen=True, slots=True)
class _StillFirstFrameTransformSnapshot:
    generation: int
    cover_scale: float
    effective_scale: float
    zoom_factor: float
    pan_x: float
    pan_y: float


def _crop_preview_adjustments(adjustments: Mapping[str, float]) -> dict[str, float]:
    """Return adjustments that expose the full transformed source in Crop mode."""

    preview = dict(adjustments)
    preview.update(
        {
            "Crop_CX": 0.5,
            "Crop_CY": 0.5,
            "Crop_W": _CROP_PREVIEW_MASK_SIZE,
            "Crop_H": _CROP_PREVIEW_MASK_SIZE,
        }
    )
    return preview


def _load_gl_module():
    global gl
    if gl is None:
        from OpenGL import GL as _gl

        gl = _gl
    return gl


def _load_gl_renderer_class():
    global GLRenderer
    if GLRenderer is None:
        from ..gl_renderer import GLRenderer as _GLRenderer

        GLRenderer = _GLRenderer
    return GLRenderer


def preload_opengl_python_runtime() -> None:
    """Import raw-OpenGL helpers without creating Qt or GPU resources.

    The interaction-triggered Detail warm-up runs this on a worker thread.  It
    must stay limited to Python imports: QRhi/context access and renderer
    resource allocation remain in ``initialize()`` on the GUI/render path.
    """

    started = time.perf_counter()
    _load_gl_module()
    _load_gl_renderer_class()
    emit_detail_event(
        "gl_runtime_preloaded",
        generation=0,
        duration_ms=(time.perf_counter() - started) * 1000.0,
    )

# 如果你的工程没有这个函数，可以改成固定背景色
try:
    from ...palette import viewer_surface_color  # type: ignore
except Exception:
    def viewer_surface_color(_):  # fallback
        return QColor(0, 0, 0)


class GLImageViewer(QRhiWidget):
    """A QWidget that displays GPU-rendered images with pixel-accurate zoom.

    Internally selects either the legacy raw OpenGL path or the Metal-capable
    QRhi path at construction time.  The class name and public API remain
    stable for controllers that still refer to ``GLImageViewer``.
    """

    # Signals（保持与旧版一致）
    replayRequested = Signal()
    zoomChanged = Signal(float)
    viewTransformChanged = Signal()
    viewportMetricsChanged = Signal()
    nextItemRequested = Signal()
    prevItemRequested = Signal()
    fullscreenExitRequested = Signal()
    fullscreenToggleRequested = Signal()
    cropChanged = Signal(float, float, float, float)
    cropInteractionStarted = Signal()
    cropInteractionFinished = Signal()
    colorPicked = Signal(float, float, float)
    firstFrameReady = Signal()
    """Emitted after the first opaque frame has been submitted for composition."""

    renderResourcesInvalidated = Signal()
    """Emitted when the active QRhi resource generation is released."""

    stillFramePresented = Signal(object)
    """Emitted after a newly uploaded full-resolution still has been drawn."""

    stillFrameSubmitted = Signal(object, int)
    """Emitted with content identity after a still reaches window submission."""

    stillTextureAllocationFailed = Signal(object, int, str)
    """Emitted when a queued foreground still cannot become resident."""

    stillLodRollbackSubmitted = Signal(object, int)
    """Emitted after a non-presentation committed-LOD restore is composed."""

    stillLodRollbackFailed = Signal(object, int)
    """Emitted when a committed-LOD restore cannot activate its resident key."""

    videoFramePresented = Signal(int, int)
    """Emitted after a newly uploaded video frame is submitted for composition."""

    def __init__(
        self,
        parent: QRhiWidget | None = None,
        *,
        staged: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)

        # Use the same platform-selected QRhi backend as the video renderer.
        # macOS defaults to Metal; Windows/Linux keep the current OpenGL path.
        # Must be called in the constructor — Qt docs state that calling
        # setApi() after the widget is shown may have no effect.
        self._rhi_api = select_qrhi_widget_api()
        self._uses_raw_gl = is_opengl_api(self._rhi_api)
        self.setApi(self._rhi_api)

        # Declare that this widget always produces fully opaque output so
        # the compositor never expects transparency from the first paint.
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        # Prevent the main window's WA_TranslucentBackground from cascading
        # into this widget and causing transparent first-frame flashes.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self._gl_funcs: Any | None = None
        self._renderer: Any | RhiImageRenderer | None = None
        self._gl_initialized = False
        self._first_render_done = False
        self._first_render_submission_pending = False

        # 状态
        self._image: QImage | None = None
        self._surface_residency_tracker: SurfaceResidencyTracker | None = None
        self._tracked_surface_resources: dict[object, object] = {}
        self._tracked_staging_resources: dict[object, object] = {}
        self._tracked_gpu_resources: dict[object, object] = {}
        self._still_surface_refs: OrderedDict[object, DecodedSurface] = OrderedDict()
        self._pending_warm_surfaces: list[DecodedSurface] = []
        self._still_lod_promotion: _StillLodPromotion | None = None
        self._still_generation_by_key: dict[object, int] = {}
        self._pending_still_activation: _PendingStillActivation | None = None
        self._rollback_submission_pending: tuple[object, int] | None = None
        self._pending_first_frame_transform: (
            _StillFirstFrameTransformSnapshot | None
        ) = None
        self._still_presentation_pending = False
        self._content_revision = 0
        self._rendered_content_identity: tuple[str, object, int, int] | None = None
        self._last_composed_content_identity: tuple[str, object, int, int] | None = None
        self._presentation_suppressed_generation: int | None = None
        self._source_image_dimensions: tuple[int, int] | None = None
        self._video_frame = None
        self._pending_video_image: QImage | None = None
        self._pending_video_image_pre_rotated = False
        self._video_frame_dirty = False
        self._video_frame_presentation_pending = False
        self._video_frame_content_generation = 0
        self._video_frame_content_serial = 0
        self._using_video_frame_source = False
        self._pending_video_reset_view = False
        self._reset_zoom_frames_crop = True
        self._crop_center_zoom_strength = 0.5
        self._fill_viewport_enabled = False
        self._transparent_rounded_clip_enabled = False
        self._rounded_clip_radius = 0.0
        self._source_rotate90_steps = 0
        self._pending_source_rotate90_steps: int | None = None
        self._last_render_target_size = QSize()
        # Crop framing must be derived from the QRhi render target that will
        # consume it. QWidget resize events can arrive before QRhi replaces
        # its target, so keep a separate dirty bit and synchronised target
        # size instead of calculating pan against the previous frame here.
        self._viewport_relayout_pending = False
        self._viewport_reset_pending = False
        self._last_layout_target_size = QSize()
        self._diag_video_frame_set_count = 0
        self._diag_video_render_count = 0
        self._adjustments: dict[str, Any] = {}
        self._eyedropper_active = False
        self._auto_crop_view_locked = False
        self._auto_crop_center_locked = False
        self._time_base = time.monotonic()
        self._runtime_ready = False
        self._texture_manager = None
        self._adjustment_applicator = None
        self._fullscreen_handler = None
        self._loading_overlay = None
        self._transform_controller = None
        self._zoom_ctrl = None
        self._crop_controller = None
        self._input_handler = None
        self._pending_surface_color_override: str | None = None

        # Presentation signals must describe a composed window frame, not merely
        # a draw command recorded by ``render()``.
        owner_ref = weakref.ref(self)

        def _handle_frame_submitted() -> None:
            owner = owner_ref()
            if owner is not None:
                owner._on_frame_submitted()

        self._frame_submitted_handler = _handle_frame_submitted
        self.frameSubmitted.connect(_handle_frame_submitted)

        if not staged:
            self.complete_runtime()

    def complete_runtime(self) -> None:
        """Create NumPy-backed viewer services without replacing the QRhi widget."""

        if self._runtime_ready:
            return
        _load_viewer_runtime_dependencies()

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
        if self._pending_surface_color_override is not None:
            self._fullscreen_handler.set_surface_color_override(
                self._pending_surface_color_override
            )
        self._fullscreen_handler._apply()

        # Loading overlay component
        self._loading_overlay = LoadingOverlay(self)
        self._transform_controller = ViewTransformController(
            self,
            texture_size_provider=self._display_texture_dimensions,
            on_zoom_changed=self.zoomChanged.emit,
            on_view_transform_changed=self.viewTransformChanged.emit,
            on_next_item=self.nextItemRequested.emit,
            on_prev_item=self.prevItemRequested.emit,
            display_texture_size_provider=self._display_texture_dimensions,
            device_view_size_provider=self._render_target_device_size,
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
        self._runtime_ready = True

    def render_backend_name(self) -> str:
        """Return the active QRhi backend name for diagnostics/tests."""

        return qrhi_api_name(self._rhi_api)

    def begin_presentation_transition(self, generation: int) -> None:
        """Suppress media draws while preserving all resident GPU resources."""

        generation = int(generation)
        if generation <= 0:
            raise ValueError("presentation transition generation must be positive")
        self._presentation_suppressed_generation = generation
        self._still_presentation_pending = False
        self._video_frame_presentation_pending = False
        self._rendered_content_identity = None
        emit_detail_event(
            "presentation_suppressed",
            generation=generation,
            renderer="gl_image_viewer",
        )

    def complete_presentation_transition(self, generation: int) -> bool:
        """Resume media draws only for the transition that owns suppression."""

        if int(generation) != self._presentation_suppressed_generation:
            return False
        self._presentation_suppressed_generation = None
        emit_detail_event(
            "presentation_resumed",
            generation=int(generation),
            renderer="gl_image_viewer",
        )
        return True

    def presentation_transition_active(self, generation: int) -> bool:
        """Return whether this generation still owns presentation suppression."""

        return int(generation) == self._presentation_suppressed_generation

    def cancel_presentation_transition(self) -> None:
        """Drop presentation suppression without changing texture residency."""

        generation = self._presentation_suppressed_generation
        self._presentation_suppressed_generation = None
        self._still_presentation_pending = False
        self._video_frame_presentation_pending = False
        self._rendered_content_identity = None
        if generation is not None:
            emit_detail_event(
                "presentation_suppression_cancelled",
                generation=generation,
                renderer="gl_image_viewer",
            )

    def _presentation_is_suppressed(self) -> bool:
        return getattr(self, "_presentation_suppressed_generation", None) is not None

    def _suppressed_video_upload_generation(self) -> int | None:
        generation = getattr(self, "_presentation_suppressed_generation", None)
        if (
            generation is None
            or not self._using_video_frame_source
            or not self._video_frame_dirty
            or self._video_frame_content_generation != generation
            or (self._video_frame is None and self._pending_video_image is None)
        ):
            return None
        return int(generation)

    def _emit_video_gpu_upload_retry(self, generation: int, stage: str) -> None:
        emit_detail_event(
            "video_gpu_upload_retry",
            generation=int(generation),
            renderer="gl_image_viewer",
            backend=self.render_backend_name(),
            failure_stage=stage,
        )

    def render_device_name(self) -> str:
        """Return the QRhi adapter name used to reject software benchmark runs."""

        rhi = self.rhi()
        if rhi is None:
            return "unknown"
        try:
            value = rhi.driverInfo().deviceName()
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            data = getattr(value, "data", None)
            if callable(data):
                return bytes(data()).decode("utf-8", errors="replace")
            return str(value)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return "unknown"

    def maximum_texture_size(self) -> int:
        """Return the active backend texture limit with a safe cold fallback."""

        rhi = self.rhi()
        if rhi is None:
            return 8192
        try:
            return max(
                1,
                int(rhi.resourceLimit(QRhi.ResourceLimit.TextureSizeMax)),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return 8192

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
        if self._uses_raw_gl and rhi is not None:
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

    def _render_target_device_size(self) -> tuple[float, float] | None:
        """Return the latest QRhi render-target size in device pixels."""

        if self._last_render_target_size.isEmpty():
            return None
        return (
            float(self._last_render_target_size.width()),
            float(self._last_render_target_size.height()),
        )

    def request_viewport_relayout(self, *, reset_view: bool = False) -> None:
        """Rebuild media framing against the next real render target.

        ``reset_view`` is sticky until the request is consumed so a normal
        resize event cannot downgrade a fullscreen-exit reset to a transform-
        preserving relayout.
        """

        self._viewport_relayout_pending = True
        self._viewport_reset_pending = (
            self._viewport_reset_pending or bool(reset_view)
        )
        self.update()

    def _sync_view_transform_for_render_target(self, output_size: QSize) -> None:
        """Synchronise crop-aware zoom and pan with *output_size* once.

        ``resizeEvent`` cannot safely do this work because
        :meth:`_render_target_device_size` may still describe the previous
        fullscreen/windowed frame. Both render backends call this helper only
        after publishing their current target size, keeping fit scale and crop
        pan in one coordinate space.
        """

        target_size = QSize(output_size)
        if target_size.isEmpty():
            return
        if (
            not self._viewport_relayout_pending
            and target_size == self._last_layout_target_size
        ):
            return

        reset_view = self._viewport_reset_pending
        if reset_view and not self._using_video_frame_source:
            image = self._image
            if image is None or image.isNull():
                # A reset belongs to the incoming still geometry. Keep it
                # pending rather than consuming it against an empty/old GPU
                # texture; set_image() will schedule the next render.
                return
        self._viewport_relayout_pending = False
        self._viewport_reset_pending = False
        self._last_layout_target_size = target_size

        with self._transform_controller.transform_transaction(
            emit_zoom=False,
            force_notify=True,
        ):
            straighten, _, _ = self._rotation_parameters()
            self._update_cover_scale(straighten)

            if reset_view:
                self.reset_zoom()
            elif not self._crop_controller.is_active():
                if self._auto_crop_view_locked:
                    self._reapply_locked_crop_view()
                elif self._auto_crop_center_locked:
                    self._reapply_locked_crop_center()

        pending_activation = self._pending_still_activation
        geometry_generation = (
            pending_activation.generation
            if pending_activation is not None
            and pending_activation.purpose == "presentation"
            else self._still_generation_by_key.get(self.current_image_source(), 0)
        )
        emit_detail_event(
            "transform_transaction_committed",
            generation=geometry_generation,
            reason="viewport_relayout",
            emit_zoom=False,
            target_width=target_size.width(),
            target_height=target_size.height(),
        )
        if reset_view and not self._using_video_frame_source:
            cover_scale = self._transform_controller.get_image_cover_scale()
            effective_scale = self._transform_controller.get_effective_scale()
            zoom_factor = self._transform_controller.get_zoom_factor()
            pan = self._transform_controller.get_pan_pixels()
            crop_rect = self._compute_crop_rect_pixels()
            center_error_x = 0.0
            center_error_y = 0.0
            if crop_rect is not None:
                viewport_center = self._transform_controller.convert_image_to_viewport(
                    crop_rect.center().x(),
                    crop_rect.center().y(),
                )
                center_error_x = viewport_center.x() - self.width() * 0.5
                center_error_y = viewport_center.y() - self.height() * 0.5
            if (
                pending_activation is not None
                and pending_activation.purpose == "presentation"
            ):
                gpu_residency_state = "resident_activation_pending"
            elif self._texture_manager.needs_texture_upload():
                gpu_residency_state = "cold_upload_pending"
            elif self._renderer is not None and self._renderer.has_texture():
                gpu_residency_state = "active"
            else:
                gpu_residency_state = "unavailable"
            self._pending_first_frame_transform = _StillFirstFrameTransformSnapshot(
                generation=geometry_generation,
                cover_scale=cover_scale,
                effective_scale=effective_scale,
                zoom_factor=zoom_factor,
                pan_x=pan.x(),
                pan_y=pan.y(),
            )
            emit_detail_event(
                "still_first_frame_transform_committed",
                generation=geometry_generation,
                target_width=target_size.width(),
                target_height=target_size.height(),
                geometry_source="viewer_image",
                gpu_residency_state=gpu_residency_state,
                cover_scale=cover_scale,
                effective_scale=effective_scale,
                zoom_factor=zoom_factor,
                pan_x=pan.x(),
                pan_y=pan.y(),
                center_error_x=center_error_x,
                center_error_y=center_error_y,
            )
        self._emit_crop_viewport_relayout(target_size)

        # Viewport-coordinate consumers (for example face annotations) must
        # only observe the transform after the authoritative QRhi target and
        # every target-dependent crop/cover update agree.
        self.viewportMetricsChanged.emit()

    def _emit_crop_viewport_relayout(self, target_size: QSize) -> None:
        """Record the final automatic crop framing against one real target."""

        if self._crop_controller.is_active():
            return
        mode = (
            "frame"
            if self._auto_crop_view_locked
            else "center"
            if self._auto_crop_center_locked
            else None
        )
        if mode is None:
            return
        crop_rect = self._compute_crop_rect_pixels()
        if crop_rect is None:
            return
        viewport_center = self._transform_controller.convert_image_to_viewport(
            crop_rect.center().x(),
            crop_rect.center().y(),
        )
        expected_center = QPointF(self.width() * 0.5, self.height() * 0.5)
        pan = self._transform_controller.get_pan_pixels()
        emit_detail_event(
            "crop_viewport_relayout",
            generation=self._still_generation_by_key.get(self.current_image_source(), 0),
            framing_mode=mode,
            target_width=target_size.width(),
            target_height=target_size.height(),
            cover_scale=self._transform_controller.get_image_cover_scale(),
            effective_scale=self._transform_controller.get_effective_scale(),
            zoom_factor=self._transform_controller.get_zoom_factor(),
            pan_x=pan.x(),
            pan_y=pan.y(),
            center_error_x=viewport_center.x() - expected_center.x(),
            center_error_y=viewport_center.y() - expected_center.y(),
        )

    @staticmethod
    def _should_log_diag_frame(index: int) -> bool:
        """Throttle noisy Linux playback diagnostics."""

        return index <= 12 or index % 30 == 0

    def _diag_video_frame_summary(self, frame) -> str:
        """Return a compact summary of a pending QVideoFrame."""

        if QVideoFrame is None or frame is None:
            return "none"
        try:
            size = frame.size()
            width = size.width()
            height = size.height()
        except Exception:  # pragma: no cover - defensive
            width = -1
            height = -1
        try:
            pixel_format = int(frame.pixelFormat())
        except Exception:  # pragma: no cover - defensive
            pixel_format = -1
        return f"valid={frame.isValid()} size={width}x{height} fmt={pixel_format}"

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
        source_size: tuple[int, int] | None = None,
        reset_view: bool = True,
        force_texture_refresh: bool = False,
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
        source_size:
            Original oriented-pixel dimensions represented by a downsampled
            viewport surface. Coordinate mapping defaults to this size while
            rendering and uploads continue to use the actual texture size.
        reset_view:
            ``True`` preserves the historic behaviour of resetting the zoom and
            pan state.  Passing ``False`` keeps the current transform so edit
            mode can reuse the detail view framing without a visible jump.
        """
        promotion = self._still_lod_promotion
        if image_source != self.current_image_source():
            self._pending_first_frame_transform = None
        if promotion is not None and image_source != promotion.key:
            self.cancel_still_lod_promotion(reason="asset_change")
        self._video_frame = None
        self._pending_video_image = None
        self._pending_video_image_pre_rotated = False
        self._video_frame_dirty = False
        self._video_frame_presentation_pending = False
        self._video_frame_content_generation = 0
        self._video_frame_content_serial = 0
        self._using_video_frame_source = False
        self._pending_video_reset_view = False
        self._pending_source_rotate90_steps = None
        if image is None or image.isNull():
            self._source_image_dimensions = None
            self._still_presentation_pending = False
            self._rendered_content_identity = None
        elif source_size is not None and min(source_size) > 0:
            self._source_image_dimensions = (int(source_size[0]), int(source_size[1]))
        elif not self._texture_manager.should_reuse_texture(image_source):
            self._source_image_dimensions = (int(image.width()), int(image.height()))

        if image is not None and not image.isNull() and image_source is not None:
            self._still_presentation_pending = True

        # Check if we can reuse the existing texture
        if (
            not force_texture_refresh
            and self._texture_manager.should_reuse_texture(image_source)
        ):
            if image is not None and not image.isNull():
                # Skip texture re-upload, only update adjustments. Preserve the
                # current zoom/pan state so adjustment previews stay anchored
                # to the user's active viewport.
                self.set_adjustments(adjustments)
                return

        # Update texture resource tracking
        self._texture_manager.set_image(
            image,
            image_source,
            force_upload=force_texture_refresh,
        )
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
            self._auto_crop_center_locked = False
            self._transform_controller.set_image_cover_scale(1.0)

        if reset_view:
            # New still geometry is now authoritative, but the QRhi target may
            # still describe the previous layout turn. Consume the reset in
            # the next render so the first submitted frame is already framed
            # against the real target and no post-submit correction is needed.
            self.request_viewport_relayout(reset_view=True)

    def set_still_surface(
        self,
        surface: DecodedSurface,
        adjustments: Mapping[str, float] | None = None,
        *,
        reset_view: bool = True,
        generation: int = 0,
    ) -> None:
        """Queue one neutral surface as the atomically presented current still."""

        self._remember_still_surface(surface)
        self._still_generation_by_key[surface.decode_key] = max(0, int(generation))
        self.set_image(
            surface.image,
            adjustments,
            image_source=surface.decode_key,
            source_size=surface.source_size,
            reset_view=reset_view,
        )

    def warm_still_surface(
        self,
        surface: DecodedSurface,
        _adjustments: Mapping[str, float] | None = None,
        *,
        residency_slot: str | None = None,
        window_generation: int = 0,
    ) -> None:
        """Queue a previous/next texture upload without changing presentation."""

        del residency_slot
        self._remember_still_surface(surface)
        self._still_generation_by_key[surface.decode_key] = max(0, int(window_generation))
        if self._texture_manager.has_resident_texture(surface.decode_key):
            self._texture_manager.touch_resident_texture(surface.decode_key)
        else:
            self._pending_warm_surfaces = [
                pending for pending in self._pending_warm_surfaces
                if pending.decode_key != surface.decode_key
            ]
            self._pending_warm_surfaces.append(surface)
            self._pending_warm_surfaces = self._pending_warm_surfaces[-2:]
            self.update()

    def promote_still_surface(
        self,
        surface: DecodedSurface,
        adjustments: Mapping[str, Any] | None = None,
        *,
        generation: int,
    ) -> None:
        """Stage a same-asset LOD while the active texture keeps rendering."""

        previous_key = self._committed_still_key()
        self.cancel_still_lod_promotion(reason="superseded")
        self._remember_still_surface(surface)
        generation = max(0, int(generation))
        self._still_generation_by_key[surface.decode_key] = generation
        phase = (
            "resident"
            if self._texture_manager.has_resident_texture(surface.decode_key)
            else "queued"
        )
        self._still_lod_promotion = _StillLodPromotion(
            surface=surface,
            adjustments=dict(adjustments or {}),
            generation=generation,
            phase=phase,
            previous_key=previous_key,
        )
        emit_detail_event(
            "lod_upgrade_resident" if phase == "resident" else "lod_upgrade_staging",
            generation=generation,
            decode_level=surface.decode_level,
            gpu_cache_hit=phase == "resident",
        )
        self.update()

    def cancel_still_lod_promotion(self, *, reason: str = "superseded") -> None:
        """Cancel pending promotion ownership without evicting resident textures."""

        if reason in {"asset_change", "resource_release"}:
            pending = self._pending_still_activation
            if pending is not None and pending.purpose == "rollback":
                self._pending_still_activation = None
            self._rollback_submission_pending = None
        promotion = self._still_lod_promotion
        if promotion is None:
            return
        cancel_upload = getattr(
            self._texture_manager,
            "cancel_pending_still_upload",
            None,
        )
        if callable(cancel_upload) and cancel_upload(
            promotion.key,
            purpose="lod_promotion",
        ):
            self._release_upload_staging(promotion.key)
        pending_activation = self._pending_still_activation
        if (
            pending_activation is not None
            and pending_activation.purpose == "promotion"
            and pending_activation.key == promotion.key
        ):
            self._pending_still_activation = None
        if (
            promotion.phase == "activating"
            and self._texture_manager.get_current_image_source() == promotion.key
        ):
            self._still_presentation_pending = False
        rendered = self._rendered_content_identity
        if (
            rendered is not None
            and rendered[0] == "still"
            and rendered[1] == promotion.key
            and rendered[2] == promotion.generation
        ):
            self._rendered_content_identity = None
        if (
            reason not in {"asset_change", "resource_release"}
            and promotion.phase == "activating"
            and self._texture_manager.get_current_image_source() == promotion.key
        ):
            self._queue_committed_lod_restore(promotion)
        emit_detail_event(
            "lod_upgrade_cancelled",
            generation=promotion.generation,
            phase=promotion.phase,
            reason=str(reason),
        )
        self._still_lod_promotion = None

    def pending_still_lod_key(self) -> object | None:
        promotion = self._still_lod_promotion
        return promotion.key if promotion is not None else None

    def still_lod_promotion_snapshot(self) -> tuple[object, int, str, bool] | None:
        """Return a privacy-safe snapshot for controller-side LOD planning."""

        promotion = self._still_lod_promotion
        if promotion is None:
            return None
        return (
            promotion.key,
            promotion.generation,
            promotion.phase,
            promotion.activation_held,
        )

    def hold_still_lod_activation(self) -> str | None:
        """Hold an activation that has not yet drawn; return its current phase."""

        promotion = self._still_lod_promotion
        if promotion is None:
            return None
        if promotion.phase in {"queued", "staging", "resident"}:
            already_held = promotion.activation_held
            promotion.activation_held = True
            pending = self._pending_still_activation
            if (
                pending is not None
                and pending.purpose == "promotion"
                and pending.key == promotion.key
            ):
                self._pending_still_activation = None
            if not already_held:
                emit_detail_event(
                    "lod_activation_held",
                    generation=promotion.generation,
                    phase=promotion.phase,
                )
        return promotion.phase

    def release_still_lod_activation(self, key: object, generation: int) -> bool:
        promotion = self._still_lod_promotion
        if (
            promotion is None
            or promotion.key != key
            or promotion.generation != int(generation)
        ):
            return False
        if not promotion.activation_held:
            return True
        promotion.activation_held = False
        emit_detail_event(
            "lod_activation_released",
            generation=promotion.generation,
            phase=promotion.phase,
        )
        self.update()
        return True

    def _committed_still_key(self) -> object | None:
        composed = self._last_composed_content_identity
        if composed is not None and composed[0] == "still":
            return composed[1]
        return self._texture_manager.get_current_image_source()

    def _protected_still_keys(self) -> frozenset[object]:
        keys: set[object] = set()
        for key in (
            self._texture_manager.get_current_image_source(),
            self._committed_still_key(),
        ):
            if key is not None:
                keys.add(key)
        pending = self._pending_still_activation
        if pending is not None:
            keys.add(pending.key)
        promotion = self._still_lod_promotion
        if promotion is not None:
            keys.add(promotion.key)
            if promotion.previous_key is not None:
                keys.add(promotion.previous_key)
        return frozenset(keys)

    def _queue_committed_lod_restore(self, promotion: _StillLodPromotion) -> bool:
        previous_key = promotion.previous_key
        previous_surface = self._still_surface_refs.get(previous_key)
        if (
            previous_key is None
            or previous_key == promotion.key
            or previous_surface is None
            or not self._texture_manager.has_resident_texture(previous_key)
        ):
            emit_detail_event(
                "lod_upgrade_rollback_failed",
                generation=promotion.generation,
                phase=promotion.phase,
                reason="previous_not_resident",
            )
            self.stillLodRollbackFailed.emit(
                previous_key,
                promotion.generation,
            )
            return False
        self._pending_still_activation = _PendingStillActivation(
            key=previous_key,
            generation=promotion.generation,
            surface=previous_surface,
            purpose="rollback",
        )
        self._still_presentation_pending = False
        self.update()
        return True

    def activate_resident_surface(
        self,
        key: object,
        adjustments: Mapping[str, float] | None = None,
        *,
        source_size: tuple[int, int] | None = None,
        reset_view: bool = True,
        generation: int = 0,
    ) -> bool:
        """Queue a render-thread activation when *key* is already on the GPU."""

        surface = self._still_surface_refs.get(key)
        if surface is None or not self._texture_manager.has_resident_texture(key):
            emit_detail_event("gpu_cache_miss", generation=generation, key=str(key))
            return False
        self._pending_still_activation = _PendingStillActivation(
            key=key,
            generation=max(0, int(generation)),
            surface=surface,
            purpose="presentation",
        )
        self._still_generation_by_key[key] = max(0, int(generation))
        self._image = surface.image
        self._source_image_dimensions = source_size or surface.source_size
        self._adjustments = dict(adjustments or {})
        self._update_crop_perspective_state()
        self._adjustment_applicator.update_curve_lut_if_needed(self._adjustments)
        self._adjustment_applicator.update_levels_lut_if_needed(self._adjustments)
        self._still_presentation_pending = True
        if reset_view:
            self.request_viewport_relayout(reset_view=True)
        emit_detail_event("gpu_cache_hit", generation=generation, key=str(key))
        self.update()
        return True

    def clear_still_residency(self) -> None:
        self.cancel_still_lod_promotion(reason="resource_release")
        self._pending_warm_surfaces.clear()
        self._pending_still_activation = None
        self._rollback_submission_pending = None
        self._pending_first_frame_transform = None
        self._still_surface_refs.clear()
        self._still_generation_by_key.clear()
        tracker = self._surface_residency_tracker
        if tracker is not None:
            tracker.release("detail-viewer-surfaces")
            tracker.release("detail-upload-staging")
        self._tracked_surface_resources.clear()
        self._tracked_staging_resources.clear()
        self._texture_manager.clear_still_residency()
        self._sync_gpu_residency()

    def trim_still_residency(self) -> None:
        self._pending_warm_surfaces.clear()
        self._texture_manager.trim_still_residency(
            protected_keys=self._protected_still_keys()
        )
        self._sync_gpu_residency()

    def zoom_factor(self) -> float:
        return float(self._transform_controller.get_zoom_factor())

    def _remember_still_surface(self, surface: DecodedSurface) -> None:
        previous = self._still_surface_refs.pop(surface.decode_key, None)
        tracker = self._surface_residency_tracker
        if previous is not None and tracker is not None:
            previous_resource = self._tracked_surface_resources.pop(
                surface.decode_key,
                surface_resource_id(previous),
            )
            tracker.release("detail-viewer-surfaces", previous_resource)
        self._still_surface_refs[surface.decode_key] = surface
        if tracker is not None:
            self._tracked_surface_resources[surface.decode_key] = tracker.retain_surface(
                "detail-viewer-surfaces",
                "presentation_prefetch",
                surface,
                generation=self._still_generation_by_key.get(surface.decode_key, 0),
            )
        self._trim_still_surface_refs()

    def _trim_still_surface_refs(self) -> None:
        tracker = self._surface_residency_tracker
        while len(self._still_surface_refs) > 3:
            protected = self._protected_still_keys()
            evicted_key = next(
                (key for key in self._still_surface_refs if key not in protected),
                None,
            )
            if evicted_key is None:
                break
            evicted = self._still_surface_refs.pop(evicted_key)
            if tracker is not None:
                evicted_resource = self._tracked_surface_resources.pop(
                    evicted_key,
                    surface_resource_id(evicted),
                )
                tracker.release("detail-viewer-surfaces", evicted_resource)

    def set_surface_residency_tracker(
        self,
        tracker: SurfaceResidencyTracker | None,
    ) -> None:
        """Bind the diagnostic-only tracker used by the owning player."""

        previous = self._surface_residency_tracker
        if previous is tracker:
            return
        if previous is not None:
            previous.release("detail-viewer-surfaces")
            previous.release("detail-upload-staging")
            previous.release("detail-gpu-residency")
        self._surface_residency_tracker = tracker
        self._tracked_surface_resources.clear()
        self._tracked_staging_resources.clear()
        self._tracked_gpu_resources.clear()
        if tracker is not None:
            for surface in self._still_surface_refs.values():
                self._tracked_surface_resources[surface.decode_key] = tracker.retain_surface(
                    "detail-viewer-surfaces",
                    "presentation_prefetch",
                    surface,
                )
            self._sync_gpu_residency()

    def set_video_frame(
        self,
        frame,
        adjustments: Mapping[str, float] | None = None,
        *,
        reset_view: bool = True,
        content_generation: int = 0,
        content_serial: int = 0,
    ) -> None:
        """Display *frame* directly through the OpenGL shader pipeline."""

        _load_video_frame_types()
        if QVideoFrame is None or frame is None or not frame.isValid():
            return

        starting_video_source = not self._using_video_frame_source
        if starting_video_source:
            self.cancel_still_lod_promotion(reason="asset_change")
            self._texture_manager.clear_image()
            self._source_image_dimensions = None
            self._still_presentation_pending = False
            self._rendered_content_identity = None
        self._using_video_frame_source = True
        self._video_frame_content_generation = max(0, int(content_generation))
        self._video_frame_content_serial = max(0, int(content_serial))
        self._image = None
        self._video_frame = frame
        self._pending_video_image = None
        self._pending_video_image_pre_rotated = False
        self._video_frame_dirty = True
        if (
            sys.platform.startswith("linux")
            and QVideoFrameFormat is not None
            and self._should_snapshot_video_frame_as_image(frame)
        ):
            snapshot = frame.toImage()
            if not snapshot.isNull():
                self._pending_video_image = snapshot
                self._pending_video_image_pre_rotated = self._is_image_snapshot_prerotated(frame, snapshot)
                self._video_frame = None
        if adjustments is not None and adjustments != self._adjustments:
            self.set_adjustments(dict(adjustments))
        self._loading_overlay.hide()
        if starting_video_source:
            self._time_base = time.monotonic()

        if reset_view:
            self._pending_video_reset_view = True
        self._diag_video_frame_set_count += 1
        if sys.platform.startswith("linux") and self._should_log_diag_frame(self._diag_video_frame_set_count):
            _LOGGER.warning(
                "[diag][gl_viewer] set_video_frame #%s reset_view=%s visible=%s dirty=%s pending_reset=%s widget=%sx%s rt=%sx%s frame=%s",
                self._diag_video_frame_set_count,
                reset_view,
                self.isVisible(),
                self._video_frame_dirty,
                self._pending_video_reset_view,
                self.width(),
                self.height(),
                self._last_render_target_size.width(),
                self._last_render_target_size.height(),
                self._diag_video_frame_summary(frame),
            )
        self._upload_video_frame_immediately_if_possible()
        self.update()

    @staticmethod
    def _should_snapshot_video_frame_as_image(frame) -> bool:
        """Return whether Linux should snapshot *frame* into ``QImage`` immediately."""

        if QVideoFrameFormat is None:
            return False
        try:
            pixel_format = frame.pixelFormat()
            pixel_enum = QVideoFrameFormat.PixelFormat
        except (AttributeError, RuntimeError, TypeError) as exc:
            _LOGGER.debug(
                "Falling back to snapshot upload due to pixel-format probe failure: %s",
                type(exc).__name__,
            )
            return True
        packed_names = (
            "Format_RGBA8888",
            "Format_BGRA8888",
            "Format_RGBX8888",
            "Format_BGRX8888",
        )
        for name in packed_names:
            value = getattr(pixel_enum, name, None)
            if value is not None and pixel_format == value:
                return False
        return True

    @staticmethod
    def _is_image_snapshot_prerotated(frame, image: QImage) -> bool:
        """Return ``True`` when ``frame.toImage()`` dimensions already include rotation."""

        if image.isNull():
            return False
        try:
            fmt = frame.surfaceFormat()
        except (AttributeError, RuntimeError, TypeError) as exc:
            _LOGGER.debug(
                "Could not determine whether frame snapshot is pre-rotated: %s",
                type(exc).__name__,
            )
            return False
        width = int(fmt.frameWidth())
        height = int(fmt.frameHeight())
        return width > 0 and height > 0 and image.width() == height and image.height() == width

    def _stage_pending_video_source(self) -> _PendingVideoUploadState | None:
        """Upload/stage a video source while retaining retryable input state."""

        if self._renderer is None:
            return None

        pending_rotation = self._pending_source_rotate90_steps
        if pending_rotation is None:
            pending_rotation = self._source_rotate90_steps

        pre_rotated = False
        if self._pending_video_image is not None:
            self._renderer.upload_texture(self._pending_video_image)
            pre_rotated = self._pending_video_image_pre_rotated
        elif self._video_frame is not None:
            self._renderer.upload_video_frame(self._video_frame)
            pre_rotated = self._renderer.last_video_upload_pre_rotated()
        else:
            return None

        final_rotation = 0 if pre_rotated else pending_rotation
        self._apply_video_source_rotation_steps(
            final_rotation,
            request_update=False,
        )
        straighten, _, _ = self._rotation_parameters()
        self._update_cover_scale(straighten)
        if self._pending_video_reset_view:
            self.reset_zoom()
        return _PendingVideoUploadState(
            pre_rotated=pre_rotated,
            final_rotation=final_rotation,
        )

    def _commit_pending_video_source(
        self,
        state: _PendingVideoUploadState,
    ) -> None:
        """Consume staged input only after its new texture was drawn successfully."""

        del state
        self._pending_video_image = None
        self._pending_video_image_pre_rotated = False
        self._pending_source_rotate90_steps = None
        self._video_frame = None
        self._video_frame_dirty = False
        self._video_frame_presentation_pending = True
        self._pending_video_reset_view = False

    def _upload_pending_video_source(self) -> bool:
        """Upload and consume a video source outside a suppressed transition."""

        state = self._stage_pending_video_source()
        if state is None:
            return False
        self._commit_pending_video_source(state)
        return state.pre_rotated

    def _upload_video_frame_immediately_if_possible(self) -> None:
        """Best-effort immediate Linux upload for edit-preview video frames.

        Some Linux backends expose short-lived mapped frame handles. Uploading
        only in ``render()`` may happen too late and produce intermittent black
        output in edit preview while gallery playback remains correct. This
        helper opportunistically uploads right after ``set_video_frame`` when
        GL resources are ready, then falls back to normal ``render()`` upload.
        """

        if not sys.platform.startswith("linux"):
            return
        if GLImageViewer._presentation_is_suppressed(self):
            return
        if not self._using_video_frame_source or not self._video_frame_dirty:
            return
        if (
            (self._video_frame is None and self._pending_video_image is None)
            or self._renderer is None
            or not self._gl_initialized
        ):
            return

        self._make_gl_current()
        try:
            self._upload_pending_video_source()
        except (AttributeError, RuntimeError, ValueError, TypeError):
            _LOGGER.exception("Failed immediate Linux video-frame upload in GLImageViewer")
        finally:
            self._done_gl_current()

    def set_placeholder(self, pixmap: QPixmap | None) -> None:
        """Display *pixmap* without changing the tracked image source."""

        if pixmap and not pixmap.isNull():
            self.set_image(
                pixmap.toImage(),
                {},
                image_source=self.current_image_source(),
                source_size=self._source_image_dimensions,
            )
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
        promotion = self._still_lod_promotion
        if promotion is not None:
            # LOD surfaces are neutral. Keep the promotion aligned with the
            # newest live shader state instead of restoring its creation-time
            # snapshot when the resident texture is activated.
            promotion.adjustments = dict(mapped_adjustments)
        self._update_crop_perspective_state()

        # Handle curve LUT update if curve data changed
        self._adjustment_applicator.update_curve_lut_if_needed(mapped_adjustments)

        # Handle levels LUT update if levels data changed
        self._adjustment_applicator.update_levels_lut_if_needed(mapped_adjustments)

        if self._crop_controller.is_active():
            # Refresh the crop overlay in logical space so it stays aligned when rotation
            # or perspective adjustments change while the interaction mode is active.
            self._crop_controller.set_active(True, self._logical_crop_values(mapped_adjustments))
        if self._auto_crop_view_locked and not self._crop_controller.is_active():
            self._reapply_locked_crop_view()
        elif self._auto_crop_center_locked and not self._crop_controller.is_active():
            self._reapply_locked_crop_center()
        self.update()
        self.viewTransformChanged.emit()

    def current_image_source(self) -> object | None:
        """Return the identifier describing the currently displayed image."""

        return self._texture_manager.get_current_image_source()

    def has_image_content(self) -> bool:
        """Return whether a still image is currently loaded into the viewer."""

        image = self._image
        return image is not None and not image.isNull()

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

    def image_to_viewport(
        self,
        x: float,
        y: float,
        *,
        image_width: float | None = None,
        image_height: float | None = None,
    ) -> QPointF:
        """Map original image-space coordinates into the current viewport."""

        actual_width, actual_height = self._texture_dimensions()
        texture_width = float(actual_width)
        texture_height = float(actual_height)
        source_width, source_height = self._source_image_dimensions or (
            actual_width,
            actual_height,
        )
        coordinate_width = float(image_width if image_width is not None else source_width)
        coordinate_height = float(image_height if image_height is not None else source_height)
        if min(texture_width, texture_height, coordinate_width, coordinate_height) <= 0.0:
            return QPointF()
        texture_x = x * texture_width / coordinate_width
        texture_y = y * texture_height / coordinate_height
        _, rotate_steps, flip_horizontal = self._rotation_parameters()
        logical_x, logical_y = geometry.texture_point_to_logical(
            texture_x,
            texture_y,
            texture_width=texture_width,
            texture_height=texture_height,
            rotate_steps=rotate_steps,
            flip_horizontal=flip_horizontal,
        )
        return self._zoom_ctrl.image_to_viewport(logical_x, logical_y)

    def viewport_to_image(
        self,
        point: QPointF,
        *,
        image_width: float | None = None,
        image_height: float | None = None,
    ) -> QPointF:
        """Map a viewport-space point back into original image coordinates."""

        logical_point = self._zoom_ctrl.viewport_to_image(point)
        actual_width, actual_height = self._texture_dimensions()
        texture_width = float(actual_width)
        texture_height = float(actual_height)
        source_width, source_height = self._source_image_dimensions or (
            actual_width,
            actual_height,
        )
        coordinate_width = float(image_width if image_width is not None else source_width)
        coordinate_height = float(image_height if image_height is not None else source_height)
        if min(texture_width, texture_height, coordinate_width, coordinate_height) <= 0.0:
            return QPointF()
        _, rotate_steps, flip_horizontal = self._rotation_parameters()
        image_x, image_y = geometry.logical_point_to_texture(
            logical_point.x(),
            logical_point.y(),
            texture_width=texture_width,
            texture_height=texture_height,
            rotate_steps=rotate_steps,
            flip_horizontal=flip_horizontal,
        )
        return QPointF(
            image_x * coordinate_width / texture_width,
            image_y * coordinate_height / texture_height,
        )

    def image_rect_to_viewport(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        image_width: float | None = None,
        image_height: float | None = None,
    ) -> QRectF:
        """Map an original image-space rectangle into the current viewport."""

        actual_width, actual_height = self._texture_dimensions()
        texture_width = float(actual_width)
        texture_height = float(actual_height)
        source_width, source_height = self._source_image_dimensions or (
            actual_width,
            actual_height,
        )
        coordinate_width = float(image_width if image_width is not None else source_width)
        coordinate_height = float(image_height if image_height is not None else source_height)
        if (
            min(texture_width, texture_height, coordinate_width, coordinate_height) <= 0.0
            or width <= 0.0
            or height <= 0.0
        ):
            return QRectF()
        texture_x = x * texture_width / coordinate_width
        texture_y = y * texture_height / coordinate_height
        texture_rect_width = width * texture_width / coordinate_width
        texture_rect_height = height * texture_height / coordinate_height
        _, rotate_steps, flip_horizontal = self._rotation_parameters()
        logical_x, logical_y, logical_w, logical_h = geometry.texture_rect_to_logical(
            texture_x,
            texture_y,
            texture_rect_width,
            texture_rect_height,
            texture_width=texture_width,
            texture_height=texture_height,
            rotate_steps=rotate_steps,
            flip_horizontal=flip_horizontal,
        )
        top_left = self._zoom_ctrl.image_to_viewport(logical_x, logical_y)
        bottom_right = self._zoom_ctrl.image_to_viewport(logical_x + logical_w, logical_y + logical_h)
        left = min(top_left.x(), bottom_right.x())
        top = min(top_left.y(), bottom_right.y())
        right = max(top_left.x(), bottom_right.x())
        bottom = max(top_left.y(), bottom_right.y())
        return QRectF(left, top, right - left, bottom - top)

    def set_surface_color_override(self, colour: str | None) -> None:
        """Override the viewer backdrop with *colour* or restore the default."""
        self._pending_surface_color_override = colour
        if not self._runtime_ready:
            return
        self._fullscreen_handler.set_surface_color_override(colour)

    def set_crop_framing_enabled(self, enabled: bool) -> None:
        """Control whether ``reset_zoom()`` frames the stored crop region."""

        target = bool(enabled)
        if self._reset_zoom_frames_crop == target:
            return
        self._reset_zoom_frames_crop = target
        if not target:
            self._auto_crop_view_locked = False
        else:
            self._auto_crop_center_locked = False

    def crop_framing_enabled(self) -> bool:
        """Return whether ``reset_zoom()`` currently frames the crop region."""

        return self._reset_zoom_frames_crop

    def set_crop_center_zoom_strength(self, strength: float) -> None:
        """Tune how strongly playback follows the crop fit when framing is off."""

        self._crop_center_zoom_strength = max(0.0, min(1.0, float(strength)))

    def crop_center_zoom_strength(self) -> float:
        """Return the partial crop-fit strength used outside crop framing mode."""

        return self._crop_center_zoom_strength

    def set_video_source_rotation(self, cw_degrees: int) -> None:
        """Apply the resolved container rotation for streamed video frames."""

        rotate_steps = (int(cw_degrees) // 90) % 4
        self._pending_source_rotate90_steps = None
        self._apply_video_source_rotation_steps(rotate_steps)

    def set_pending_video_source_rotation(self, cw_degrees: int) -> None:
        """Queue the container rotation for the next uploaded video frame."""

        self._pending_source_rotate90_steps = (int(cw_degrees) // 90) % 4

    def _apply_video_source_rotation_steps(
        self,
        rotate_steps: int,
        *,
        request_update: bool = True,
    ) -> None:
        if self._source_rotate90_steps == rotate_steps:
            return
        self._source_rotate90_steps = rotate_steps
        self._update_crop_perspective_state()
        if self._crop_controller.is_active():
            self._crop_controller.set_active(True, self._logical_crop_values())
        if self._auto_crop_view_locked and not self._crop_controller.is_active():
            self._reapply_locked_crop_view()
        elif self._auto_crop_center_locked and not self._crop_controller.is_active():
            self._reapply_locked_crop_center()
        if self._renderer is not None and self._renderer.has_texture():
            straighten, _, _ = self._rotation_parameters()
            self._update_cover_scale(straighten)
        if request_update:
            self.update()
        self.viewTransformChanged.emit()

    def _display_rotate_steps(self, values: Mapping[str, Any] | None = None) -> int:
        mapped_values = values if values is not None else self._adjustments
        user_steps = geometry.get_rotate_steps(mapped_values)
        return (user_steps + self._source_rotate90_steps) % 4

    def _display_adjustments(self, values: Mapping[str, Any] | None = None) -> dict[str, Any]:
        mapped = dict(values if values is not None else self._adjustments)
        mapped["Crop_Rotate90"] = float(self._display_rotate_steps(mapped))
        return mapped

    def _logical_crop_values(self, values: Mapping[str, Any] | None = None) -> dict[str, float]:
        return geometry.logical_crop_mapping_from_texture(self._display_adjustments(values))

    def set_viewport_fill_enabled(self, enabled: bool) -> None:
        """Control whether the viewer covers the viewport instead of fitting inside it."""

        target = bool(enabled)
        if self._fill_viewport_enabled == target:
            return
        self._fill_viewport_enabled = target
        self._transform_controller.set_fill_viewport_enabled(target)
        straighten, _, _ = self._rotation_parameters()
        self._update_cover_scale(straighten)
        self.update()

    def set_transparent_rounded_clip(self, radius: float | None) -> None:
        """Enable a smooth alpha-rounded clip when *radius* is positive."""

        numeric = float(radius or 0.0)
        enabled = numeric > 0.0
        if (
            self._transparent_rounded_clip_enabled == enabled
            and abs(self._rounded_clip_radius - numeric) <= 1e-4
        ):
            return
        self._transparent_rounded_clip_enabled = enabled
        self._rounded_clip_radius = max(0.0, numeric)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, not enabled)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, enabled)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, enabled)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, enabled)
        self.setAutoFillBackground(not enabled)
        self.update()

    def _pass_clear_color(self) -> QColor:
        """Return the QRhi clear colour for the current transparency mode."""

        if self._transparent_rounded_clip_enabled:
            return QColor(0, 0, 0, 0)
        bg = self._fullscreen_handler.backdrop_color
        return QColor.fromRgbF(bg.redF(), bg.greenF(), bg.blueF(), 1.0)

    def _transition_clear_color(self) -> QColor:
        """Return an opaque clear colour for generation transitions."""

        color = QColor(self._pass_clear_color())
        color.setAlpha(255)
        return color

    def _gl_clear_rgba(self) -> tuple[float, float, float, float]:
        """Return the OpenGL clear colour matching the QRhi pass clear."""

        if self._transparent_rounded_clip_enabled:
            return (0.0, 0.0, 0.0, 0.0)
        bg = self._fullscreen_handler.backdrop_color
        return (bg.redF(), bg.greenF(), bg.blueF(), 1.0)

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
        self.viewTransformChanged.emit()

        return updates

    def set_zoom(self, factor: float, anchor: QPointF | None = None) -> None:
        """Adjust the zoom while preserving the requested *anchor* pixel."""

        self._cancel_auto_crop_lock()
        anchor_point = anchor or self.viewport_center()
        self._transform_controller.set_zoom(float(factor), anchor_point)

    def reset_zoom(self) -> None:
        if self._crop_controller.is_active():
            self._transform_controller.reset_zoom()
            self.viewTransformChanged.emit()
            return
        if not self._reset_zoom_frames_crop:
            self._auto_crop_view_locked = False
            if not self._center_crop_if_available():
                self._auto_crop_center_locked = False
                self._transform_controller.reset_zoom()
            self.viewTransformChanged.emit()
            return
        self._auto_crop_center_locked = False
        if not self._frame_crop_if_available():
            self._auto_crop_view_locked = False
            self._transform_controller.reset_zoom()
        self.viewTransformChanged.emit()

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
        if not self._uses_raw_gl:
            if target_size.isEmpty() or self._image is None or self._image.isNull():
                return QImage()
            try:
                from .....core.image_filters import apply_adjustments
            except Exception:
                _LOGGER.warning("render_offscreen_image: CPU adjustment fallback unavailable", exc_info=True)
                return QImage()
            rendered = apply_adjustments(self._image, adjustments or self._adjustments)
            if rendered.isNull():
                return QImage()
            if rendered.size() != target_size:
                rendered = rendered.scaled(
                    target_size,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            return rendered.convertToFormat(QImage.Format.Format_ARGB32)

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
        """QRhiWidget override: initialise renderer resources once."""
        self.complete_runtime()
        if self._gl_initialized:
            return
        initialize_started = time.perf_counter()
        backend = self.render_backend_name()
        emit_detail_event(
            "qrhi_initialize_started",
            generation=0,
            backend=backend,
        )
        rhi = self.rhi()
        if rhi is None:
            _LOGGER.warning("QRhi not available - image rendering disabled")
            emit_detail_event(
                "qrhi_initialize_finished",
                generation=0,
                backend=backend,
                success=False,
                duration_ms=(time.perf_counter() - initialize_started) * 1000.0,
            )
            return
        if not self._uses_raw_gl:
            renderer = RhiImageRenderer()
            try:
                renderer.initialize_resources(rhi, self.renderTarget().renderPassDescriptor(), cb)
            except Exception:
                _LOGGER.exception("Failed to initialise QRhi image renderer")
                emit_detail_event(
                    "qrhi_initialize_finished",
                    generation=0,
                    backend=backend,
                    success=False,
                    duration_ms=(time.perf_counter() - initialize_started) * 1000.0,
                )
                return
            self._renderer = renderer
            self._adjustment_applicator.invalidate_cache()
            self._adjustment_applicator.update_curve_lut_if_needed(self._adjustments)
            self._adjustment_applicator.update_levels_lut_if_needed(self._adjustments)
            self._gl_initialized = True
            emit_detail_event(
                "qrhi_initialize_finished",
                generation=0,
                backend=backend,
                success=True,
                duration_ms=(time.perf_counter() - initialize_started) * 1000.0,
            )
            return

        # Make the underlying OpenGL context current so we can issue raw GL
        # calls (create shaders, VAO, VBO, textures, …).
        rhi.makeThreadLocalNativeContextCurrent()
        current_context = QOpenGLContext.currentContext()
        if current_context is None:
            _LOGGER.warning("Current OpenGL context unavailable - image rendering disabled")
            emit_detail_event(
                "qrhi_initialize_finished",
                generation=0,
                backend=backend,
                success=False,
                duration_ms=(time.perf_counter() - initialize_started) * 1000.0,
            )
            return
        gf = current_context.extraFunctions()
        self._gl_funcs = gf

        if self._renderer is not None:
            self._renderer.destroy_resources()

        renderer_cls = _load_gl_renderer_class()
        self._renderer = renderer_cls(gf, parent=self)
        self._renderer.initialize_resources()
        self._adjustment_applicator.invalidate_cache()
        self._adjustment_applicator.update_curve_lut_if_needed(self._adjustments)
        self._adjustment_applicator.update_levels_lut_if_needed(self._adjustments)

        dpr = self.devicePixelRatioF()
        gf.glViewport(0, 0, int(self.width() * dpr), int(self.height() * dpr))
        self._gl_initialized = True
        emit_detail_event(
            "qrhi_initialize_finished",
            generation=0,
            backend=backend,
            success=True,
            duration_ms=(time.perf_counter() - initialize_started) * 1000.0,
        )

    def releaseResources(self) -> None:  # type: ignore[override]
        """QRhiWidget override: release renderer resources."""
        self._gl_initialized = False
        self.cancel_still_lod_promotion(reason="resource_release")
        if self._runtime_ready:
            if self._renderer is not None:
                rhi = self.rhi()
                if self._uses_raw_gl and rhi is not None:
                    # Ensure the underlying OpenGL context is current before
                    # issuing raw GL deletes in GLRenderer.destroy_resources().
                    rhi.makeThreadLocalNativeContextCurrent()
                self._renderer.destroy_resources()
            self._texture_manager.mark_texture_lost()
            self._sync_gpu_residency()
            tracker = self._surface_residency_tracker
            if tracker is not None:
                tracker.release("detail-upload-staging")
            self._tracked_staging_resources.clear()
        self._first_render_done = False
        self._first_render_submission_pending = False
        self._video_frame_presentation_pending = False
        self._content_revision = 0
        self._rendered_content_identity = None
        self._last_composed_content_identity = None
        if self._runtime_ready:
            source = self._texture_manager.get_current_image_source()
            if source is not None and not self._using_video_frame_source:
                self._still_presentation_pending = True
        self.renderResourcesInvalidated.emit()
        emit_detail_event("context_rebuild", generation=0, state="released")

    def render(self, cb) -> None:  # type: ignore[override]
        """QRhiWidget override: render the current image/video frame."""
        self.complete_runtime()
        if not self._uses_raw_gl:
            self._render_rhi(cb)
            return

        if not self._gl_initialized:
            # GL resources are not yet available but we MUST still clear the
            # render target with an opaque colour so the surface is never
            # transparent.  An early bare return would leave the texture
            # uninitialised, compositing as transparent under the main
            # window's WA_TranslucentBackground.
            cb.beginPass(
                self.renderTarget(),
                (
                    self._transition_clear_color()
                    if GLImageViewer._presentation_is_suppressed(self)
                    else self._pass_clear_color()
                ),
                QRhiDepthStencilClearValue(),
            )
            cb.endPass()
            self._queue_first_frame_ready()
            self._rendered_content_identity = None
            return
        gf = self._gl_funcs
        if gf is None or self._renderer is None:
            cb.beginPass(
                self.renderTarget(),
                (
                    self._transition_clear_color()
                    if GLImageViewer._presentation_is_suppressed(self)
                    else self._pass_clear_color()
                ),
                QRhiDepthStencilClearValue(),
            )
            cb.endPass()
            self._queue_first_frame_ready()
            self._rendered_content_identity = None
            return

        output_size = self.renderTarget().pixelSize()
        if output_size.isEmpty():
            if sys.platform.startswith("linux"):
                _LOGGER.warning(
                    "[diag][gl_viewer] render skipped empty target using_video=%s dirty=%s widget=%sx%s",
                    self._using_video_frame_source,
                    self._video_frame_dirty,
                    self.width(),
                    self.height(),
                )
            return
        self._last_render_target_size = QSize(output_size)
        self._sync_view_transform_for_render_target(output_size)

        suppressed = GLImageViewer._presentation_is_suppressed(self)
        suppressed_video_generation = (
            GLImageViewer._suppressed_video_upload_generation(self)
        )
        if suppressed and suppressed_video_generation is None:
            cb.beginPass(
                self.renderTarget(),
                self._transition_clear_color(),
                QRhiDepthStencilClearValue(),
            )
            cb.endPass()
            self._queue_first_frame_ready()
            self._rendered_content_identity = None
            return

        # Start a QRhi render pass (required by QRhiWidget) then immediately
        # switch to raw OpenGL via beginExternal()/endExternal().  This lets
        # us keep all existing GL 3.3 shader code unchanged while both
        # widgets share the same QRhi rendering infrastructure.
        cb.beginPass(
            self.renderTarget(),
            self._pass_clear_color(),
            QRhiDepthStencilClearValue(),
            flags=QRhiCommandBuffer.BeginPassFlag.ExternalContent,
        )
        cb.beginExternal()

        # --- All raw OpenGL calls happen between beginExternal/endExternal ---
        vw = max(1, output_size.width())
        vh = max(1, output_size.height())
        gl_module = _load_gl_module()
        gf.glViewport(0, 0, vw, vh)
        clear_r, clear_g, clear_b, clear_a = self._gl_clear_rgba()
        gf.glClearColor(clear_r, clear_g, clear_b, clear_a)
        gf.glClear(gl_module.GL_COLOR_BUFFER_BIT)

        staged_video_upload: _PendingVideoUploadState | None = None
        self._activate_pending_still_texture(purpose="rollback")
        self._prepare_still_lod_promotion_for_render()
        self._activate_pending_still_texture()
        if (
            self._using_video_frame_source
            and self._video_frame_dirty
            and (self._video_frame is not None or self._pending_video_image is not None)
        ):
            self._diag_video_render_count += 1
            if sys.platform.startswith("linux") and self._should_log_diag_frame(self._diag_video_render_count):
                _LOGGER.warning(
                    "[diag][gl_viewer] render #%s pre-upload rt=%sx%s widget=%sx%s pending_rot=%s source_rot=%s has_texture=%s frame=%s",
                    self._diag_video_render_count,
                    vw,
                    vh,
                    self.width(),
                    self.height(),
                    self._pending_source_rotate90_steps,
                    self._source_rotate90_steps,
                    self._renderer.has_texture(),
                    self._diag_video_frame_summary(self._video_frame),
                )
            try:
                if suppressed_video_generation is not None:
                    staged_video_upload = self._stage_pending_video_source()
                    if staged_video_upload is None:
                        raise RuntimeError("No matching video source available for upload")
                    pre_rotated = staged_video_upload.pre_rotated
                else:
                    pre_rotated = self._upload_pending_video_source()
                if sys.platform.startswith("linux") and self._should_log_diag_frame(self._diag_video_render_count):
                    logical_tex_w, logical_tex_h = self._display_texture_dimensions()
                    _LOGGER.warning(
                        "[diag][gl_viewer] render #%s post-upload pre_rotated=%s final_rot=%s logical_tex=%sx%s cover=%.5f zoom=%.5f pan=(%.2f,%.2f)",
                        self._diag_video_render_count,
                        pre_rotated,
                        self._source_rotate90_steps,
                        logical_tex_w,
                        logical_tex_h,
                        self._transform_controller.get_image_cover_scale(),
                        self._transform_controller.get_effective_scale(),
                        self._transform_controller.get_pan_pixels().x(),
                        self._transform_controller.get_pan_pixels().y(),
                    )
            except Exception:
                _LOGGER.exception("Failed to upload video frame into GLImageViewer")
                if suppressed_video_generation is not None:
                    self._emit_video_gpu_upload_retry(
                        suppressed_video_generation,
                        "upload",
                    )
                    cb.endExternal()
                    cb.endPass()
                    self._queue_first_frame_ready()
                    self._rendered_content_identity = None
                    return
        elif (
            self._image is not None
            and not self._image.isNull()
            and self._texture_manager.needs_texture_upload()
        ):
            upload_started = time.perf_counter()
            self._upload_current_still_with_tracking()
            log_detail_profile(
                "gl_viewer",
                "still.gpu_upload",
                (time.perf_counter() - upload_started) * 1000.0,
                path=self._still_source_name(),
            )
            current_key = self.current_image_source()
            self._emit_still_gpu_upload(current_key)
        elif self._pending_warm_surfaces and self._still_lod_promotion is None:
            warm = self._pending_warm_surfaces.pop(0)
            if self._warm_still_with_tracking(warm):
                emit_detail_event(
                    "gpu_upload",
                    generation=self._still_generation_by_key.get(warm.decode_key, 0),
                    key=str(warm.decode_key),
                    warm=True,
                )
            if self._pending_warm_surfaces:
                self.update()

        # Consume foreground failures before the no-texture early return.  On
        # the first image there is no previous active texture, so returning
        # first would suppress the allocation-failure signal and strand the
        # render session forever instead of requesting a lower LOD.
        self._consume_still_upload_result()
        if not self._renderer.has_texture():
            if sys.platform.startswith("linux") and self._using_video_frame_source:
                _LOGGER.warning(
                    "[diag][gl_viewer] render no-texture rt=%sx%s dirty=%s pending_reset=%s",
                    vw,
                    vh,
                    self._video_frame_dirty,
                    self._pending_video_reset_view,
                )
            cb.endExternal()
            cb.endPass()
            self._queue_first_frame_ready()
            self._rendered_content_identity = None
            if suppressed_video_generation is not None:
                self._emit_video_gpu_upload_retry(
                    suppressed_video_generation,
                    "no_texture",
                )
            return

        effective_scale = self._transform_controller.get_effective_scale()
        cover_scale = self._transform_controller.get_image_cover_scale()

        time_value = time.monotonic() - self._time_base
        
        view_pan = self._transform_controller.get_pan_pixels()

        effective_adjustments: dict[str, float] | Mapping[str, float]
        if self._crop_controller.is_active():
            # During crop interactions we want to preview the entire photo with
            # a translucent overlay.  The fragment shader drives the crop
            # window entirely from the ``Crop_*`` uniforms, therefore we
            # override those values on-the-fly instead of mutating
            # ``self._adjustments`` (which stores the persisted edit state).
            effective_adjustments = _crop_preview_adjustments(
                self._display_adjustments()
            )
        else:
            # Convert texture-space crop to logical-space for shader
            # Shader tests crop in pre-rotation space (uv_perspective),
            # so it needs logical-space crop parameters
            effective_adjustments = dict(self._display_adjustments())
            logical_crop = geometry.logical_crop_mapping_from_texture(effective_adjustments)
            effective_adjustments.update(logical_crop)


        logical_tex_w, logical_tex_h = self._display_texture_dimensions()
        if (
            sys.platform.startswith("linux")
            and self._using_video_frame_source
            and self._should_log_diag_frame(self._diag_video_render_count)
        ):
            _LOGGER.warning(
                "[diag][gl_viewer] draw #%s rt=%sx%s logical_tex=%sx%s cover=%.5f zoom=%.5f pan=(%.2f,%.2f) rounded=%s",
                self._diag_video_render_count,
                vw,
                vh,
                logical_tex_w,
                logical_tex_h,
                cover_scale,
                effective_scale,
                view_pan.x(),
                view_pan.y(),
                self._transparent_rounded_clip_enabled,
            )

        self._renderer.render(
            view_width=float(vw),
            view_height=float(vh),
            scale=effective_scale,
            pan=view_pan,
            adjustments=effective_adjustments,
            time_value=time_value,
            logical_tex_size=(float(logical_tex_w), float(logical_tex_h)),
            corner_radius_px=(
                self._rounded_clip_radius * self.devicePixelRatioF()
                if self._transparent_rounded_clip_enabled
                else 0.0
            ),
        )
        self._consume_still_upload_result()

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
        if staged_video_upload is not None and suppressed_video_generation is not None:
            self._commit_pending_video_source(staged_video_upload)
            self.complete_presentation_transition(suppressed_video_generation)
        self._queue_first_frame_ready()
        rendered_identity = self._take_pending_content_submission()
        if rendered_identity is not None:
            self._rendered_content_identity = rendered_identity

    def _render_rhi(self, cb) -> None:
        """Render the current image through QRhi without raw OpenGL."""

        if not self._gl_initialized or self._renderer is None:
            cb.beginPass(
                self.renderTarget(),
                (
                    self._transition_clear_color()
                    if GLImageViewer._presentation_is_suppressed(self)
                    else self._pass_clear_color()
                ),
                QRhiDepthStencilClearValue(),
            )
            cb.endPass()
            self._queue_first_frame_ready()
            self._rendered_content_identity = None
            return

        output_size = self.renderTarget().pixelSize()
        if output_size.isEmpty():
            return
        self._last_render_target_size = QSize(output_size)
        self._sync_view_transform_for_render_target(output_size)

        suppressed = GLImageViewer._presentation_is_suppressed(self)
        suppressed_video_generation = (
            GLImageViewer._suppressed_video_upload_generation(self)
        )
        if suppressed and suppressed_video_generation is None:
            cb.beginPass(
                self.renderTarget(),
                self._transition_clear_color(),
                QRhiDepthStencilClearValue(),
            )
            cb.endPass()
            self._queue_first_frame_ready()
            self._rendered_content_identity = None
            return

        vw = max(1, output_size.width())
        vh = max(1, output_size.height())

        staged_video_upload: _PendingVideoUploadState | None = None
        self._activate_pending_still_texture(purpose="rollback")
        self._prepare_still_lod_promotion_for_render()
        self._activate_pending_still_texture()
        if (
            self._using_video_frame_source
            and self._video_frame_dirty
            and (self._video_frame is not None or self._pending_video_image is not None)
        ):
            self._diag_video_render_count += 1
            try:
                if suppressed_video_generation is not None:
                    staged_video_upload = self._stage_pending_video_source()
                    if staged_video_upload is None:
                        raise RuntimeError("No matching video source available for upload")
                else:
                    self._upload_pending_video_source()
            except Exception:
                _LOGGER.exception("Failed to upload video frame into QRhi image viewer")
                if suppressed_video_generation is not None:
                    self._emit_video_gpu_upload_retry(
                        suppressed_video_generation,
                        "upload",
                    )
                    cb.beginPass(
                        self.renderTarget(),
                        self._transition_clear_color(),
                        QRhiDepthStencilClearValue(),
                    )
                    cb.endPass()
                    self._queue_first_frame_ready()
                    self._rendered_content_identity = None
                    return
        elif (
            self._image is not None
            and not self._image.isNull()
            and self._texture_manager.needs_texture_upload()
        ):
            upload_started = time.perf_counter()
            self._upload_current_still_with_tracking()
            log_detail_profile(
                "gl_viewer",
                "still.gpu_upload",
                (time.perf_counter() - upload_started) * 1000.0,
                path=self._still_source_name(),
            )
            current_key = self.current_image_source()
            self._emit_still_gpu_upload(current_key)
        elif self._pending_warm_surfaces and self._still_lod_promotion is None:
            warm = self._pending_warm_surfaces.pop(0)
            if self._warm_still_with_tracking(warm):
                emit_detail_event(
                    "gpu_upload",
                    generation=self._still_generation_by_key.get(warm.decode_key, 0),
                    key=str(warm.decode_key),
                    warm=True,
                )
            if self._pending_warm_surfaces:
                self.update()

        self._consume_still_upload_result()
        if not self._renderer.has_texture():
            cb.beginPass(
                self.renderTarget(),
                self._pass_clear_color(),
                QRhiDepthStencilClearValue(),
            )
            cb.endPass()
            self._queue_first_frame_ready()
            self._rendered_content_identity = None
            if suppressed_video_generation is not None:
                self._emit_video_gpu_upload_retry(
                    suppressed_video_generation,
                    "no_texture",
                )
            return

        effective_scale = self._transform_controller.get_effective_scale()
        time_value = time.monotonic() - self._time_base
        view_pan = self._transform_controller.get_pan_pixels()

        if self._crop_controller.is_active():
            effective_adjustments = _crop_preview_adjustments(
                self._display_adjustments()
            )
        else:
            effective_adjustments = dict(self._display_adjustments())
            logical_crop = geometry.logical_crop_mapping_from_texture(effective_adjustments)
            effective_adjustments.update(logical_crop)

        logical_tex_w, logical_tex_h = self._display_texture_dimensions()
        crop_rect = None
        crop_faded = False
        if self._crop_controller.is_active():
            crop_rect = self._crop_controller.current_crop_rect_pixels()
            crop_faded = self._crop_controller.is_faded_out()

        self._renderer.render(
            cb=cb,
            render_target=self.renderTarget(),
            clear_color=self._pass_clear_color(),
            view_width=float(vw),
            view_height=float(vh),
            scale=effective_scale,
            pan=view_pan,
            adjustments=effective_adjustments,
            time_value=time_value,
            logical_tex_size=(float(logical_tex_w), float(logical_tex_h)),
            corner_radius_px=(
                self._rounded_clip_radius * self.devicePixelRatioF()
                if self._transparent_rounded_clip_enabled
                else 0.0
            ),
            crop_rect=crop_rect,
            crop_faded=crop_faded,
        )
        self._consume_still_upload_result()

        if staged_video_upload is not None and suppressed_video_generation is not None:
            self._commit_pending_video_source(staged_video_upload)
            self.complete_presentation_transition(suppressed_video_generation)

        self._queue_first_frame_ready()
        rendered_identity = self._take_pending_content_submission()
        if rendered_identity is not None:
            self._rendered_content_identity = rendered_identity

    def _still_source_name(self) -> str:
        source = self._texture_manager.get_current_image_source()
        return getattr(source, "name", str(source or ""))

    def _take_pending_content_submission(
        self,
    ) -> tuple[str, object, int, int] | None:
        """Bind the content drawn by this render call to its submission event."""

        if self._still_presentation_pending:
            source = self._texture_manager.get_current_image_source()
            if source is not None:
                self._still_presentation_pending = False
                generation = self._still_generation_by_key.get(source, 0)
                self._content_revision += 1
                return ("still", source, generation, self._content_revision)
        if self._video_frame_presentation_pending:
            self._video_frame_presentation_pending = False
            self._content_revision += 1
            return (
                "video",
                self._video_frame_content_generation,
                self._video_frame_content_serial,
                self._content_revision,
            )
        return None

    def _prepare_still_lod_promotion_for_render(self) -> None:
        promotion = self._still_lod_promotion
        if promotion is None:
            return
        if promotion.phase == "resident":
            if promotion.activation_held:
                return
            if self._pending_still_activation is None:
                self._pending_still_activation = _PendingStillActivation(
                    key=promotion.key,
                    generation=promotion.generation,
                    surface=promotion.surface,
                    purpose="promotion",
                )
            return
        if promotion.phase != "queued":
            return

        queued = self._texture_manager.stage_still_texture(
            promotion.key,
            promotion.surface.image,
            protected_keys=self._protected_still_keys(),
        )
        if queued:
            self._observe_upload_staging(promotion.key, promotion.surface.image)
            promotion.phase = "staging"
            self._consume_still_upload_result()
            return
        if self._texture_manager.has_resident_texture(promotion.key):
            promotion.phase = "resident"
            emit_detail_event(
                "lod_upgrade_resident",
                generation=promotion.generation,
                decode_level=promotion.surface.decode_level,
                gpu_cache_hit=True,
            )
            self.update()
            return
        if not self._consume_still_upload_result():
            self._fail_still_lod_promotion(promotion, "staging_rejected")

    def _activate_pending_still_texture(
        self,
        *,
        purpose: Literal["presentation", "promotion", "rollback"] | None = None,
    ) -> bool:
        pending = self._pending_still_activation
        if pending is None or (purpose is not None and pending.purpose != purpose):
            return False
        promotion = self._still_lod_promotion
        if pending.purpose == "promotion" and (
            promotion is None
            or promotion.key != pending.key
            or promotion.generation != pending.generation
            or promotion.activation_held
        ):
            self._pending_still_activation = None
            return False

        self._pending_still_activation = None
        activated = self._texture_manager.activate_resident_texture(pending.key)
        if not activated:
            if pending.purpose == "rollback":
                emit_detail_event(
                    "lod_rollback_failed",
                    generation=pending.generation,
                    reason="previous_not_resident_at_activation",
                )
                self.stillLodRollbackFailed.emit(pending.key, pending.generation)
            elif pending.purpose == "promotion" and promotion is not None:
                self._fail_still_lod_promotion(
                    promotion,
                    "resident_activation_failed",
                )
            else:
                self.stillTextureAllocationFailed.emit(
                    pending.key,
                    pending.generation,
                    "resident_activation_failed",
                )
            return False

        surface = pending.surface
        self._image = surface.image
        self._source_image_dimensions = surface.source_size
        if pending.purpose == "rollback":
            self._still_presentation_pending = False
            self._rollback_submission_pending = (pending.key, pending.generation)
            self._update_crop_perspective_state()
            emit_detail_event(
                "lod_rollback_activated",
                generation=pending.generation,
            )
            return True
        if pending.purpose == "presentation":
            return True

        if promotion is None:
            return True
        self._adjustments = dict(promotion.adjustments)
        self._update_crop_perspective_state()
        self._adjustment_applicator.update_curve_lut_if_needed(self._adjustments)
        self._adjustment_applicator.update_levels_lut_if_needed(self._adjustments)
        self._still_presentation_pending = True
        promotion.phase = "activating"
        emit_detail_event(
            "lod_upgrade_activated",
            generation=promotion.generation,
            decode_level=surface.decode_level,
        )
        return True

    def _take_still_upload_result(self) -> dict[str, object] | None:
        take_result = getattr(self._renderer, "take_still_upload_result", None)
        if not callable(take_result):
            return None
        result = take_result()
        if result is not None:
            self._release_upload_staging(result.get("key"))
            self._sync_gpu_residency()
        return result

    def _consume_still_upload_result(self) -> bool:
        """Publish a foreground allocation failure and suppress false presentation."""

        result = GLImageViewer._take_still_upload_result(self)
        if result is not None and result.get("purpose") == "lod_promotion":
            promotion = self._still_lod_promotion
            if promotion is None or result.get("key") != promotion.key:
                return False
            if result.get("success"):
                promotion.phase = "resident"
                emit_detail_event(
                    "lod_upgrade_resident",
                    generation=promotion.generation,
                    decode_level=promotion.surface.decode_level,
                    gpu_cache_hit=False,
                )
                self.update()
            else:
                self._fail_still_lod_promotion(
                    promotion,
                    str(result.get("reason", "staging_failed")),
                )
            return False
        failed = bool(
            result is not None
            and result.get("activate")
            and not result.get("success")
        )
        if not failed:
            return False
        self._still_presentation_pending = False
        failed_key = result.get("key")
        self.stillTextureAllocationFailed.emit(
            failed_key,
            self._still_generation_by_key.get(failed_key, 0),
            str(result.get("reason", "allocation_failed")),
        )
        return True

    def _fail_still_lod_promotion(
        self,
        promotion: _StillLodPromotion,
        reason: str,
    ) -> None:
        if self._still_lod_promotion is not promotion:
            return
        self._still_lod_promotion = None
        self.stillTextureAllocationFailed.emit(
            promotion.key,
            promotion.generation,
            str(reason),
        )

    def _upload_current_still_with_tracking(self) -> None:
        key = self.current_image_source()
        image = self._image
        if image is None:
            return
        self._observe_upload_staging(key, image)
        try:
            self._texture_manager.upload_texture_if_needed(image)
        except Exception:
            self._release_upload_staging(key)
            raise

    def _emit_still_gpu_upload(self, key: object) -> None:
        generation = self._still_generation_by_key.get(key, 0)
        cover_scale = self._transform_controller.get_image_cover_scale()
        effective_scale = self._transform_controller.get_effective_scale()
        emit_detail_event(
            "gpu_upload",
            generation=generation,
            key=str(key),
            cover_scale=cover_scale,
            effective_scale=effective_scale,
        )
        snapshot = self._pending_first_frame_transform
        if snapshot is None or snapshot.generation != generation:
            return
        if abs(snapshot.cover_scale - cover_scale) <= 1e-4:
            return
        emit_detail_event(
            "still_first_frame_cover_drift",
            generation=generation,
            cover_before=snapshot.cover_scale,
            cover_after=cover_scale,
            effective_before=snapshot.effective_scale,
            effective_after=effective_scale,
        )

    def _warm_still_with_tracking(self, surface: DecodedSurface) -> bool:
        queued = self._texture_manager.warm_still_texture(
            surface.decode_key,
            surface.image,
            protected_keys=self._protected_still_keys(),
        )
        if queued:
            self._observe_upload_staging(surface.decode_key, surface.image)
        return queued

    def _observe_upload_staging(self, key: object, image: QImage) -> None:
        tracker = self._surface_residency_tracker
        if tracker is None or key is None or image.isNull():
            return
        previous = self._tracked_staging_resources.pop(key, None)
        if previous is not None:
            tracker.release("detail-upload-staging", previous)
        resource_id = ("upload-staging", key, int(image.cacheKey()))
        self._tracked_staging_resources[key] = resource_id
        tracker.retain(
            "detail-upload-staging",
            "upload_queue",
            resource_id,
            SurfaceByteBreakdown(
                upload_staging=max(
                    0,
                    int(image.bytesPerLine()) * int(image.height()),
                )
            ),
            generation=self._still_generation_by_key.get(key, 0),
        )

    def _release_upload_staging(self, key: object) -> None:
        tracker = self._surface_residency_tracker
        resource_id = self._tracked_staging_resources.pop(key, None)
        if tracker is not None and resource_id is not None:
            tracker.release(
                "detail-upload-staging",
                resource_id,
                generation=self._still_generation_by_key.get(key, 0),
            )

    def _sync_gpu_residency(self) -> None:
        tracker = self._surface_residency_tracker
        if tracker is None:
            return
        getter = getattr(self._renderer, "still_residency_bytes", None)
        current = dict(getter()) if callable(getter) else {}
        for key in tuple(self._tracked_gpu_resources):
            if key in current:
                continue
            tracker.release(
                "detail-gpu-residency",
                self._tracked_gpu_resources.pop(key),
                generation=self._still_generation_by_key.get(key, 0),
            )
        for key, byte_count in current.items():
            resource_id = ("gpu-still", key)
            self._tracked_gpu_resources[key] = resource_id
            tracker.retain(
                "detail-gpu-residency",
                "gpu_residency",
                resource_id,
                SurfaceByteBreakdown(gpu_estimated=max(0, int(byte_count))),
                generation=self._still_generation_by_key.get(key, 0),
            )

    def _queue_first_frame_ready(self) -> None:
        """Record that an opaque draw is waiting for window submission."""
        if not self._first_render_done:
            self._first_render_submission_pending = True

    def _on_frame_submitted(self) -> None:
        """Publish pending draw acknowledgements after window composition."""

        if self._first_render_submission_pending:
            self._first_render_submission_pending = False
            self._first_render_done = True
            self.firstFrameReady.emit()
        rollback = getattr(self, "_rollback_submission_pending", None)
        if (
            isinstance(rollback, tuple)
            and len(rollback) == 2
            and self._texture_manager.get_current_image_source() == rollback[0]
        ):
            self._rollback_submission_pending = None
            key, generation = rollback
            self._content_revision += 1
            self._last_composed_content_identity = (
                "still",
                key,
                self._still_generation_by_key.get(key, 0),
                self._content_revision,
            )
            emit_detail_event(
                "lod_rollback_frame_submitted",
                generation=generation,
            )
            self.stillLodRollbackSubmitted.emit(key, generation)
            self._trim_still_surface_refs()
        submission = self._rendered_content_identity
        if submission is not None and submission != self._last_composed_content_identity:
            self._last_composed_content_identity = submission
            kind, identity, serial, _revision = submission
            if kind == "still":
                promotion = self._still_lod_promotion
                promotion_submitted = bool(
                    promotion is not None
                    and promotion.phase == "activating"
                    and promotion.key == identity
                    and promotion.generation == serial
                )
                self.stillFrameSubmitted.emit(identity, serial)
                self.stillFramePresented.emit(identity)
                snapshot = self._pending_first_frame_transform
                if snapshot is not None and snapshot.generation == serial:
                    self._pending_first_frame_transform = None
                if promotion_submitted and self._still_lod_promotion is promotion:
                    self._still_lod_promotion = None
                    self._trim_still_surface_refs()
                    if self._pending_warm_surfaces:
                        self.update()
            else:
                self.videoFramePresented.emit(int(identity), serial)

    # --------------------------- Crop helpers ---------------------------

    def setCropMode(self, enabled: bool, values: Mapping[str, float] | None = None) -> None:
        was_active = self._crop_controller.is_active()
        source_values = values if values is not None else self._adjustments
        self._crop_controller.set_active(enabled, self._logical_crop_values(source_values))
        if enabled and not was_active:
            self._cancel_auto_crop_lock()
            self._transform_controller.reset_zoom()
        elif not enabled and was_active:
            self.reset_zoom()
        self.update()

    def crop_values(self) -> dict[str, float]:
        logical_map = self._crop_controller.get_crop_values()
        logical_tuple = geometry.normalised_crop_from_mapping(logical_map)
        rotate_steps = self._display_rotate_steps()

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

    def _update_cover_scale(self, straighten_deg: float) -> None:
        crop_viewport.update_cover_scale(self, straighten_deg)


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
        if not self._runtime_ready:
            return
        self._loading_overlay.update_geometry(self.size())
        self.request_viewport_relayout()
        if sys.platform.startswith("linux"):
            _LOGGER.warning(
                "[diag][gl_viewer] resize widget=%sx%s rt=%sx%s using_video=%s dirty=%s",
                self.width(),
                self.height(),
                self._last_render_target_size.width(),
                self._last_render_target_size.height(),
                self._using_video_frame_source,
                self._video_frame_dirty,
            )

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

    def _center_crop_if_available(self) -> bool:
        return crop_viewport.center_crop_if_available(self)

    def _reapply_locked_crop_view(self) -> None:
        crop_viewport.reapply_locked_crop_view(self)

    def _reapply_locked_crop_center(self) -> None:
        crop_viewport.reapply_locked_crop_center(self)

    def _cancel_auto_crop_lock(self) -> None:
        crop_viewport.cancel_auto_crop_lock(self)

    def _compute_crop_rect_pixels(self) -> QRectF | None:
        return crop_viewport.compute_crop_rect_pixels(self)

    def _handle_crop_interaction_changed(
        self, cx: float, cy: float, width: float, height: float
    ) -> None:
        crop_viewport.handle_crop_interaction_changed(self, cx, cy, width, height)
