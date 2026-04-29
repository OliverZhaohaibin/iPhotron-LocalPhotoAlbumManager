"""Lightweight floating window that previews a video asset."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sys
from typing import Callable, Mapping, Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRegion, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsScene,
    QGraphicsView,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from ....config import (
    PREVIEW_WINDOW_CLOSE_DELAY_MS,
    PREVIEW_WINDOW_CORNER_RADIUS,
    PREVIEW_WINDOW_DEFAULT_WIDTH,
    PREVIEW_WINDOW_MUTED,
)
from ....utils.ffmpeg import probe_video_rotation
from ..media import MediaController, require_multimedia
from .video_area import VideoArea

if importlib.util.find_spec("PySide6.QtMultimediaWidgets") is not None:
    from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
else:  # pragma: no cover - requires optional Qt module
    QGraphicsVideoItem = None  # type: ignore[assignment]


_PREVIEW_WINDOW_SHADOW_PADDING = 12
_RHI_PREVIEW_SHADOW_BLUR_RADIUS = 48.0
_RHI_PREVIEW_SHADOW_OFFSET_Y = 12
_RHI_PREVIEW_SHADOW_PADDING = int(
    _RHI_PREVIEW_SHADOW_BLUR_RADIUS + abs(_RHI_PREVIEW_SHADOW_OFFSET_Y)
)


class _PreviewWheelGuard(QObject):
    """Block wheel gestures inside long-press preview popups.

    Gallery long-press previews are intentionally read-only. Swallowing wheel
    events here prevents the embedded renderers from interpreting the gesture
    as zoom input while the preview is visible.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        del watched
        if event.type() == QEvent.Type.Wheel:
            event.accept()
            return True
        return False


class _RoundedVideoItem(QGraphicsVideoItem):
    """Graphics video item that clips playback to a rounded rectangle."""

    def __init__(self, corner_radius: int) -> None:
        if QGraphicsVideoItem is None:  # pragma: no cover - optional Qt module
            raise RuntimeError(
                "PySide6.QtMultimediaWidgets is unavailable; install PySide6 with "
                "QtMultimediaWidgets support to enable video previews."
            )
        super().__init__()
        self._corner_radius = float(max(0, corner_radius))
        self.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatioByExpanding)

    def set_corner_radius(self, corner_radius: int) -> None:
        radius = float(max(0, corner_radius))
        if self._corner_radius == radius:
            return
        self._corner_radius = radius
        self.update()

    def paint(self, painter: QPainter, option, widget=None) -> None:  # type: ignore[override]
        if self._corner_radius > 0.0:
            rect = self.boundingRect()
            radius = min(self._corner_radius, min(rect.width(), rect.height()) / 2.0)
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            painter.setClipPath(path)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        super().paint(painter, option, widget)


class _VideoView(QGraphicsView):
    """Hosts the rounded video item within a scene."""

    def __init__(
        self,
        on_resize: Callable[[], None],
        corner_radius: int,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._on_resize = on_resize
        self._scene = QGraphicsScene(self)
        self._video_item = _RoundedVideoItem(corner_radius)
        self._scene.addItem(self._video_item)
        self.setScene(self._scene)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent; border: none;")
        self._wheel_guard = _PreviewWheelGuard(self)
        self.installEventFilter(self._wheel_guard)
        self.viewport().installEventFilter(self._wheel_guard)

    def video_item(self) -> _RoundedVideoItem:
        return self._video_item

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_video_geometry()
        self._on_resize()

    def _update_video_geometry(self) -> None:
        viewport_size = self.viewport().size()
        rect = QRectF(
            0.0,
            0.0,
            float(max(0, viewport_size.width())),
            float(max(0, viewport_size.height())),
        )
        self._scene.setSceneRect(rect)
        self._video_item.setSize(rect.size())
        center = rect.center()
        self._video_item.setPos(center - self._video_item.boundingRect().center())


class _PreviewFrame(QWidget):
    """Draws rounded chrome around the embedded video widget."""

    def __init__(self, corner_radius: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._corner_radius = max(0, corner_radius)
        self._border_width = 0
        self._background = QColor(18, 18, 22)
        self._border = QColor(255, 255, 255, 28)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._video_view = _VideoView(self._update_masks, corner_radius, self)
        layout.addWidget(self._video_view)

        self._update_masks()

    def video_item(self) -> _RoundedVideoItem:
        return self._video_view.video_item()

    def video_view(self) -> _VideoView:
        return self._video_view

    def set_corner_radius(self, corner_radius: int) -> None:
        radius = max(0, corner_radius)
        if radius == self._corner_radius:
            return
        self._corner_radius = radius
        self._update_masks()
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = float(self._corner_radius)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._background)
        painter.drawPath(path)

        if self._border_width > 0 and self._border.alpha() > 0:
            pen = QPen(self._border)
            pen.setWidth(self._border_width)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_masks()

    def _update_masks(self) -> None:
        video_radius = max(0, self._corner_radius)
        self._video_view.video_item().set_corner_radius(video_radius)
        self.update()


class _RhiShadowFrame(QWidget):
    """Paints the rounded backing shape that receives the popup shadow."""

    def __init__(self, corner_radius: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._corner_radius = max(0, corner_radius)
        self._background = QColor(18, 18, 22)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

    def set_corner_radius(self, corner_radius: int) -> None:
        radius = max(0, corner_radius)
        if radius == self._corner_radius:
            return
        self._corner_radius = radius
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        if self.width() <= 0 or self.height() <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = max(
            0.0,
            min(float(self._corner_radius), min(rect.width(), rect.height()) / 2.0),
        )

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._background)
        painter.drawPath(path)


def _bottom_rounded_region(size: QSize, corner_radius: int) -> QRegion:
    """Return a hard child clip that only protects the bottom popup corners."""

    width = max(1, int(size.width()))
    height = max(1, int(size.height()))
    radius = max(0.0, min(float(corner_radius), width / 2.0, height / 2.0))
    if radius <= 0.0:
        return QRegion(0, 0, width, height)

    path = QPainterPath()
    path.moveTo(0.0, 0.0)
    path.lineTo(float(width), 0.0)
    path.lineTo(float(width), float(height) - radius)
    path.quadTo(float(width), float(height), float(width) - radius, float(height))
    path.lineTo(radius, float(height))
    path.quadTo(0.0, float(height), 0.0, float(height) - radius)
    path.closeSubpath()
    return QRegion(path.toFillPolygon().toPolygon())


class _RhiPreviewPopup(QWidget):
    """Transparent popup wrapper hosting an RHI video area and shadow backplate."""

    displaySizeChanged = Signal(QSizeF)
    _PROFILE_RAW = "raw"
    _PROFILE_EDITED = "edited"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._corner_radius = PREVIEW_WINDOW_CORNER_RADIUS
        self._shadow_padding = _RHI_PREVIEW_SHADOW_PADDING
        default_height = max(1, int(PREVIEW_WINDOW_DEFAULT_WIDTH * 9 / 16))
        self._content_size = QSize(PREVIEW_WINDOW_DEFAULT_WIDTH, default_height)
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._shadow_frame = _RhiShadowFrame(self._corner_radius, self)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(_RHI_PREVIEW_SHADOW_BLUR_RADIUS)
        shadow.setOffset(0, _RHI_PREVIEW_SHADOW_OFFSET_Y)
        shadow.setColor(QColor(0, 0, 0, 120))
        self._shadow_frame.setGraphicsEffect(shadow)

        self._content_frame = _RhiShadowFrame(self._corner_radius, self)

        self._wheel_guard = _PreviewWheelGuard(self)
        for target in (self, self._shadow_frame, self._content_frame):
            target.installEventFilter(self._wheel_guard)
        self._active_render_profile: str | None = None
        self._video_area = self._create_video_area()
        self._set_rounding_mode()
        self.resize_preview(self._content_size)
        self.hide()

    def resize_preview(self, size: QSize) -> None:
        content_width = max(1, int(size.width()))
        content_height = max(1, int(size.height()))
        self._content_size = QSize(content_width, content_height)
        total_size = QSize(
            content_width + 2 * self._shadow_padding,
            content_height + 2 * self._shadow_padding,
        )
        if self.size() != total_size:
            self.resize(total_size)
        self._layout_layers()

    def show_preview(
        self,
        source: Path,
        *,
        adjustments: Mapping[str, object] | None,
        trim_range_ms: tuple[int, int] | None,
        adjusted_preview: bool,
    ) -> None:
        render_profile = self._render_profile(
            adjustments=adjustments,
            trim_range_ms=trim_range_ms,
            adjusted_preview=adjusted_preview,
        )
        rebuilt = self._prepare_video_area_for_profile(render_profile)
        if not rebuilt:
            self._video_area.stop()
        self._set_rounding_mode()
        # macOS/Metal can fail when the same long-press popup alternates
        # between two QRhiWidget-backed child surfaces. Keep every mac preview
        # on the GL/adjusted surface; unedited videos still pass empty
        # adjustments and no trim.
        internal_adjusted_preview = bool(adjusted_preview or sys.platform == "darwin")
        self._video_area.load_video(
            source,
            adjustments=adjustments,
            trim_range_ms=trim_range_ms,
            adjusted_preview=internal_adjusted_preview,
        )
        self.show()
        self.raise_()
        self._video_area.play()

    def close_preview(self) -> None:
        self._video_area.stop()
        self.hide()

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._layout_layers()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)

    @classmethod
    def _render_profile(
        cls,
        *,
        adjustments: Mapping[str, object] | None,
        trim_range_ms: tuple[int, int] | None,
        adjusted_preview: bool,
    ) -> str:
        if adjusted_preview or trim_range_ms is not None or bool(adjustments):
            return cls._PROFILE_EDITED
        return cls._PROFILE_RAW

    def _prepare_video_area_for_profile(self, profile: str) -> bool:
        previous_profile = getattr(self, "_active_render_profile", None)
        should_rebuild = (
            sys.platform == "darwin"
            and previous_profile is not None
            and previous_profile != profile
        )
        if should_rebuild:
            self._rebuild_video_area()
        self._active_render_profile = profile
        return should_rebuild

    def _create_video_area(self) -> VideoArea:
        video_area = VideoArea(self._content_frame)
        video_area.set_controls_enabled(False)
        video_area.set_muted(PREVIEW_WINDOW_MUTED)
        video_area.hide_controls(animate=False)
        video_area.set_surface_color("#121216")
        video_area.set_viewport_fill_enabled(True)
        video_area.edit_viewer.set_crop_framing_enabled(True)
        video_area.displaySizeChanged.connect(self.displaySizeChanged.emit)
        for target in (
            video_area,
            video_area._surface_stack,
            video_area._renderer,
            video_area._edit_viewer,
        ):
            target.installEventFilter(self._wheel_guard)
        return video_area

    def _rebuild_video_area(self) -> None:
        old_video_area = self._video_area
        try:
            old_video_area.stop()
        except Exception:
            pass
        try:
            old_video_area.displaySizeChanged.disconnect(self.displaySizeChanged.emit)
        except (RuntimeError, TypeError):
            pass
        old_video_area.hide()
        old_video_area.setParent(None)
        old_video_area.deleteLater()

        self._video_area = self._create_video_area()
        self._set_rounding_mode()
        self._layout_layers()

    def _layout_layers(self) -> None:
        content_rect = QRect(
            self._shadow_padding,
            self._shadow_padding,
            max(1, self.width() - 2 * self._shadow_padding),
            max(1, self.height() - 2 * self._shadow_padding),
        )
        self._shadow_frame.setGeometry(content_rect)
        self._content_frame.setGeometry(content_rect)
        self._content_frame.setMask(
            _bottom_rounded_region(content_rect.size(), self._corner_radius)
        )
        self._video_area.setGeometry(QRect(0, 0, content_rect.width(), content_rect.height()))
        self._content_frame.raise_()
        self._video_area.raise_()

    def _set_rounding_mode(self) -> None:
        self._shadow_frame.set_corner_radius(self._corner_radius)
        self._content_frame.set_corner_radius(self._corner_radius)
        self._video_area.set_transparent_preview_enabled(
            True,
            corner_radius=float(self._corner_radius),
        )


class PreviewWindow(QWidget):
    """Frameless preview surface that reuses the media controller API."""

    _probe_executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preview-probe")
    _native_size_probe_cache: dict[str, tuple[float, float]] = {}
    _probe_result_ready = Signal(int, str, float, float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        require_multimedia()
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._shadow_padding = _PREVIEW_WINDOW_SHADOW_PADDING
        self._corner_radius = PREVIEW_WINDOW_CORNER_RADIUS
        default_height = max(1, int(PREVIEW_WINDOW_DEFAULT_WIDTH * 9 / 16))
        self._content_size = QSize(PREVIEW_WINDOW_DEFAULT_WIDTH, default_height)
        self._current_native_size = QSizeF()
        self._anchor_rect: Optional[QRect] = None
        self._anchor_point: Optional[QPoint] = None
        self._aspect_ratio_hint: Optional[float] = None
        self._native_size_seeded_from_probe = False
        self._native_size_seeded = False
        self._pending_orientation_flip: Optional[int] = None
        self._active_probe_request_id = 0
        self._active_source_key = ""
        self._probe_future: Optional[Future[tuple[float, float]]] = None
        self._using_rhi_popup = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            self._shadow_padding,
            self._shadow_padding,
            self._shadow_padding,
            self._shadow_padding,
        )
        layout.setSpacing(0)

        self._frame = _PreviewFrame(self._corner_radius, self)
        layout.addWidget(self._frame)
        self._wheel_guard = _PreviewWheelGuard(self)
        for target in (
            self,
            self._frame,
            self._frame.video_view(),
            self._frame.video_view().viewport(),
        ):
            target.installEventFilter(self._wheel_guard)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(48.0)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 120))
        self._frame.setGraphicsEffect(shadow)

        self._apply_content_size(
            self._content_size.width(),
            self._content_size.height(),
        )

        self._media = MediaController(self)
        self._media.set_video_output(self._frame.video_item())
        self._media.set_muted(PREVIEW_WINDOW_MUTED)
        self._frame.video_item().nativeSizeChanged.connect(self._on_native_size_changed)
        self._rhi_popup = _RhiPreviewPopup(parent)
        self._rhi_popup.displaySizeChanged.connect(self._on_native_size_changed)

        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self._do_close)
        self._probe_result_ready.connect(self._on_probe_result_ready)
        self.hide()

    def show_preview(
        self,
        source: Path | str,
        at: Optional[QRect | QPoint] = None,
        *,
        aspect_ratio_hint: Optional[float] = None,
        adjustments: Mapping[str, object] | None = None,
        trim_range_ms: tuple[int, int] | None = None,
        adjusted_preview: bool = False,
    ) -> None:
        """Display *source* near *at* and start playback immediately."""

        path = Path(source)
        self._close_timer.stop()
        self._media.unload()
        next_uses_rhi_popup = bool(
            sys.platform == "darwin"
            or adjusted_preview
            or trim_range_ms is not None
        )
        self._current_native_size = QSizeF()
        self._native_size_seeded_from_probe = False
        self._native_size_seeded = False
        self._pending_orientation_flip = None
        self._aspect_ratio_hint = None
        self._active_probe_request_id += 1
        self._active_source_key = str(path.resolve())
        if isinstance(aspect_ratio_hint, (int, float)):
            numeric = float(aspect_ratio_hint)
            if numeric > 0.0:
                self._aspect_ratio_hint = numeric
                self._native_size_seeded = True
        self._anchor_rect = at if isinstance(at, QRect) else None
        self._anchor_point = at if isinstance(at, QPoint) else None
        self._prime_native_size_async(path, request_id=self._active_probe_request_id)
        self._using_rhi_popup = next_uses_rhi_popup
        self._apply_layout_for_anchor()
        if self._using_rhi_popup:
            self._rhi_popup.show_preview(
                path,
                adjustments=adjustments,
                trim_range_ms=trim_range_ms,
                adjusted_preview=adjusted_preview,
            )
            self.hide()
        else:
            self._rhi_popup.close_preview()
            self._media.load(path)

        if not self._using_rhi_popup:
            self.show()
            self.raise_()
            self._media.play()

    def close_preview(self, delayed: bool = True) -> None:
        """Hide the preview window, optionally with a delay."""

        if delayed:
            self._close_timer.start(PREVIEW_WINDOW_CLOSE_DELAY_MS)
        else:
            self._do_close()

    def _do_close(self) -> None:
        self._close_timer.stop()
        self._media.unload()
        self._rhi_popup.close_preview()
        self.hide()

    def _clamp_to_screen(self, origin: QPoint, *, size: Optional[QSize] = None) -> QPoint:
        screen = self._rhi_popup.screen() if self._using_rhi_popup else self.screen()
        if screen is None:
            return origin
        area = screen.availableGeometry()
        window_size = size if size is not None else self.size()
        min_x = area.x()
        min_y = area.y()
        max_x = area.x() + max(0, area.width() - window_size.width())
        max_y = area.y() + max(0, area.height() - window_size.height())
        return QPoint(
            max(min_x, min(origin.x(), max_x)),
            max(min_y, min(origin.y(), max_y)),
        )

    def _apply_content_size(self, content_width: int, content_height: int) -> None:
        content_width = max(1, content_width)
        content_height = max(1, content_height)
        self._content_size = QSize(content_width, content_height)
        self._frame.setFixedSize(self._content_size)
        total_width = self._content_size.width() + 2 * self._shadow_padding
        total_height = self._content_size.height() + 2 * self._shadow_padding
        if self.size() != QSize(total_width, total_height):
            self.resize(total_width, total_height)
        self._frame.set_corner_radius(self._corner_radius)

    def _effective_aspect_ratio(self) -> float:
        native_width = float(self._current_native_size.width())
        native_height = float(self._current_native_size.height())
        if native_width > 0.0 and native_height > 0.0:
            return native_width / native_height
        if self._aspect_ratio_hint is not None and self._aspect_ratio_hint > 0.0:
            return self._aspect_ratio_hint
        return 16.0 / 9.0

    def _popup_size_for_aspect(self, max_dimension: int, aspect_ratio: float) -> QSize:
        return self._size_for_aspect(max_dimension, aspect_ratio)

    def _apply_popup_layout(self) -> None:
        aspect_ratio = self._effective_aspect_ratio()
        if self._anchor_rect is not None:
            base_dimension = max(
                PREVIEW_WINDOW_DEFAULT_WIDTH,
                self._anchor_rect.width(),
                self._anchor_rect.height(),
            )
            content_size = self._popup_size_for_aspect(base_dimension, aspect_ratio)
            self._rhi_popup.resize_preview(content_size)
            center = self._anchor_rect.center()
            origin = QPoint(
                center.x() - self._rhi_popup.width() // 2,
                center.y() - self._rhi_popup.height() // 2,
            )
            self._rhi_popup.move(self._clamp_to_screen(origin, size=self._rhi_popup.size()))
            return

        content_size = self._popup_size_for_aspect(PREVIEW_WINDOW_DEFAULT_WIDTH, aspect_ratio)
        self._rhi_popup.resize_preview(content_size)
        if self._anchor_point is not None:
            origin = self._anchor_point - QPoint(
                self._rhi_popup._shadow_padding,
                self._rhi_popup._shadow_padding,
            )
            self._rhi_popup.move(
                self._clamp_to_screen(origin, size=self._rhi_popup.size())
            )

    def _size_for_aspect(self, max_dimension: int, aspect_ratio: float) -> QSize:
        base = max(1, int(max_dimension))
        aspect = max(0.01, float(aspect_ratio))
        if aspect >= 1.0:
            width = base
            height = max(1, int(round(width / aspect)))
        else:
            height = base
            width = max(1, int(round(height * aspect)))
        return QSize(width, height)

    def _apply_layout_for_anchor(self) -> None:
        if self._using_rhi_popup:
            self._apply_popup_layout()
            return
        aspect_ratio = self._effective_aspect_ratio()
        if self._anchor_rect is not None:
            base_dimension = max(
                PREVIEW_WINDOW_DEFAULT_WIDTH,
                self._anchor_rect.width(),
                self._anchor_rect.height(),
            )
            content_size = self._size_for_aspect(base_dimension, aspect_ratio)
            self._apply_content_size(content_size.width(), content_size.height())
            center = self._anchor_rect.center()
            origin = QPoint(center.x() - self.width() // 2, center.y() - self.height() // 2)
            self.move(self._clamp_to_screen(origin))
            return

        content_size = self._size_for_aspect(PREVIEW_WINDOW_DEFAULT_WIDTH, aspect_ratio)
        self._apply_content_size(content_size.width(), content_size.height())
        if self._anchor_point is not None:
            self.move(self._clamp_to_screen(self._anchor_point))

    def _on_native_size_changed(self, size: QSizeF) -> None:
        if size.width() <= 0.0 or size.height() <= 0.0:
            return
        if self._current_native_size == size:
            return
        candidate_aspect = float(size.width()) / float(size.height())
        current_w = float(self._current_native_size.width())
        current_h = float(self._current_native_size.height())
        current_aspect = (current_w / current_h) if (current_w > 0.0 and current_h > 0.0) else None

        def _orientation(aspect: float) -> int:
            if aspect < 0.95:
                return -1  # portrait
            if aspect > 1.05:
                return 1  # landscape
            return 0  # near-square / unknown

        if self._native_size_seeded and current_aspect is not None:
            current_orientation = _orientation(current_aspect)
            candidate_orientation = _orientation(candidate_aspect)
            if current_orientation != 0 and candidate_orientation != 0:
                if current_orientation != candidate_orientation:
                    # Guard against one-off orientation flips reported by some
                    # backends (e.g. portrait → temporary landscape → portrait).
                    if self._pending_orientation_flip != candidate_orientation:
                        self._pending_orientation_flip = candidate_orientation
                        return
                else:
                    self._pending_orientation_flip = None

        if self._native_size_seeded_from_probe:
            if current_aspect is not None:
                candidate_is_square = 0.9 <= candidate_aspect <= 1.1
                current_is_square = 0.9 <= current_aspect <= 1.1
                # Some multimedia backends briefly report a square native size
                # before stabilising to the true rotated dimensions. When a
                # probe-derived size is already available, ignore this transient
                # square update to prevent a visible "square flash".
                if candidate_is_square and not current_is_square:
                    return
            self._native_size_seeded_from_probe = False
        self._native_size_seeded = False
        self._pending_orientation_flip = None
        self._current_native_size = QSizeF(size)
        self._apply_layout_for_anchor()

    def _prime_native_size_async(self, source: Path, *, request_id: int) -> None:
        """Seed preview size using cached/async ffprobe metadata when available."""

        source_key = str(source.resolve())
        cached_size = self._native_size_probe_cache.get(source_key)
        if cached_size is not None:
            cached_width, cached_height = cached_size
            if cached_width > 0.0 and cached_height > 0.0:
                self._current_native_size = QSizeF(cached_width, cached_height)
                self._native_size_seeded_from_probe = True
                self._native_size_seeded = True
                return

        self._probe_future = self._probe_executor.submit(self._probe_native_size, source)
        self._probe_future.add_done_callback(
            lambda future, rid=request_id, sk=source_key: self._on_probe_future_done(
                request_id=rid,
                source_key=sk,
                future=future,
            )
        )

    @staticmethod
    def _probe_native_size(source: Path) -> tuple[float, float]:
        """Return display dimensions inferred from ffprobe metadata."""

        cw_degrees, raw_width, raw_height = probe_video_rotation(source)
        if raw_width <= 0 or raw_height <= 0:
            return (0.0, 0.0)
        if cw_degrees in (90, 270):
            display_width = raw_height
            display_height = raw_width
        else:
            display_width = raw_width
            display_height = raw_height
        return (float(display_width), float(display_height))

    def _on_probe_future_done(
        self,
        *,
        request_id: int,
        source_key: str,
        future: Future[tuple[float, float]],
    ) -> None:
        try:
            display_width, display_height = future.result()
        except Exception:
            display_width, display_height = 0.0, 0.0
        self._probe_result_ready.emit(request_id, source_key, display_width, display_height)

    def _on_probe_result_ready(
        self,
        request_id: int,
        source_key: str,
        display_width: float,
        display_height: float,
    ) -> None:
        if request_id != self._active_probe_request_id:
            return
        if source_key != self._active_source_key:
            return
        if display_width <= 0.0 or display_height <= 0.0:
            return
        self._native_size_probe_cache[source_key] = (display_width, display_height)
        self._current_native_size = QSizeF(display_width, display_height)
        self._native_size_seeded_from_probe = True
        self._native_size_seeded = True
        self._apply_layout_for_anchor()
