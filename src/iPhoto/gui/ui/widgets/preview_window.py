"""Lightweight floating window that previews a video asset."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import importlib.util
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QResizeEvent
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

if importlib.util.find_spec("PySide6.QtMultimediaWidgets") is not None:
    from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
else:  # pragma: no cover - requires optional Qt module
    QGraphicsVideoItem = None  # type: ignore[assignment]


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

        self._shadow_padding = 12
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
    ) -> None:
        """Display *source* near *at* and start playback immediately."""

        path = Path(source)
        self._close_timer.stop()
        self._media.stop()
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
        self._media.load(path)

        self._apply_layout_for_anchor()

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
        self._media.stop()
        self.hide()

    def _clamp_to_screen(self, origin: QPoint) -> QPoint:
        screen = self.screen()
        if screen is None:
            return origin
        area = screen.availableGeometry()
        min_x = area.x()
        min_y = area.y()
        max_x = area.x() + max(0, area.width() - self.width())
        max_y = area.y() + max(0, area.height() - self.height())
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
