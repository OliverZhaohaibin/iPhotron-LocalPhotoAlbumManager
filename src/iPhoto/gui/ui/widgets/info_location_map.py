"""Compact single-pin map preview used by the detail info panel."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QPainter,
    QPainterPath,
    QPixmap,
    QRegion,
    QResizeEvent,
    QWindow,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ....application.ports import MapRuntimePort
from maps.map_sources import MapSourceSpec
from maps.map_widget.drag_cursor import DragCursorManager

from .photo_map_view import (
    _configure_opaque_map_container,
)
from .map_widget_factory import (
    MapGLWidget,
    MapGLWindowWidget,
    MapWidget,
    MapWidgetBase,
    MapWidgetFactoryResult,
    _choose_map_widget_backend_with_runtime,
    _preferred_python_widget_class,
    check_opengl_support,
    choose_map_widget_backend,
    resolve_map_package_root,
)

LOGGER = logging.getLogger(__name__)

_PIN_ICON_PATH = Path(__file__).resolve().parents[1] / "icon" / "map.pin.svg"
_PIN_ICON_WIDTH = 90
_PIN_ICON_HEIGHT = 114
_PIN_ANCHOR_X_RATIO = 256.0 / 512.0
_PIN_ANCHOR_Y_RATIO = 418.0 / 512.0


def create_map_widget(
    parent: QWidget,
    *,
    map_source: MapSourceSpec | None,
    map_runtime_capabilities,
    package_root: Path,
    log: logging.Logger | None = None,
    context: str = "info-panel mini-map",
) -> MapWidgetFactoryResult:
    """Build the mini-map through shared factory primitives.

    The wrapper keeps long-standing tests able to patch this module's backend
    chooser while the concrete widget imports remain isolated in
    ``map_widget_factory``.
    """

    active_logger = log or LOGGER
    if map_runtime_capabilities is not None:
        use_opengl = map_runtime_capabilities.python_gl_available
        try:
            widget_cls, resolved_map_source, backend_kind = _choose_map_widget_backend_with_runtime(
                map_source,
                use_opengl=use_opengl,
                runtime_capabilities=map_runtime_capabilities,
                package_root=package_root,
            )
        except TypeError as exc:
            if "package_root" not in str(exc):
                raise
            widget_cls, resolved_map_source, backend_kind = _choose_map_widget_backend_with_runtime(
                map_source,
                use_opengl=use_opengl,
                runtime_capabilities=map_runtime_capabilities,
            )
    else:
        use_opengl = check_opengl_support()
        try:
            widget_cls, resolved_map_source, backend_kind = choose_map_widget_backend(
                map_source,
                use_opengl=use_opengl,
                package_root=package_root,
            )
        except TypeError as exc:
            if "package_root" not in str(exc):
                raise
            widget_cls, resolved_map_source, backend_kind = choose_map_widget_backend(
                map_source,
                use_opengl=use_opengl,
            )

    assert resolved_map_source is not None
    try:
        widget = widget_cls(parent, map_source=resolved_map_source)
        return MapWidgetFactoryResult(widget, resolved_map_source, backend_kind, use_opengl)
    except Exception as exc:
        if backend_kind == "osmand_native":
            active_logger.warning(
                "Native OsmAnd widget unavailable for %s, falling back: %s",
                context,
                exc,
            )
            fallback_cls = _preferred_python_widget_class(use_opengl=use_opengl)
            widget = fallback_cls(parent, map_source=resolved_map_source)
            return MapWidgetFactoryResult(
                widget,
                resolved_map_source,
                "osmand_python",
                use_opengl,
            )
        if widget_cls in {MapGLWidget, MapGLWindowWidget}:
            active_logger.warning(
                "OpenGL mini-map unavailable, falling back to CPU renderer: %s",
                exc,
            )
            widget = MapWidget(parent, map_source=resolved_map_source)
            fallback_backend_kind = (
                "osmand_python"
                if resolved_map_source.kind == "osmand_obf"
                else "legacy_python"
            )
            return MapWidgetFactoryResult(
                widget,
                resolved_map_source,
                fallback_backend_kind,
                use_opengl,
            )
        active_logger.warning("Mini-map backend unavailable", exc_info=True)
        return MapWidgetFactoryResult(None, resolved_map_source, "unavailable", use_opengl)


def _build_pin_pixmap(width: int, height: int) -> QPixmap:
    """Render the map pin into a fixed box while preserving its SVG aspect ratio."""

    renderer = QSvgRenderer(str(_PIN_ICON_PATH))
    target_size = QSize(width, height)
    pixmap = QPixmap(target_size)
    pixmap.fill(Qt.GlobalColor.transparent)
    if not renderer.isValid():
        return pixmap

    default_size = renderer.defaultSize()
    if not default_size.isValid() or default_size.width() <= 0 or default_size.height() <= 0:
        render_rect = QRectF(0.0, 0.0, float(width), float(height))
    else:
        scaled = default_size.scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio)
        x = (width - scaled.width()) / 2.0
        y = (height - scaled.height()) / 2.0
        render_rect = QRectF(x, y, float(scaled.width()), float(scaled.height()))

    painter = QPainter(pixmap)
    renderer.render(painter, render_rect)
    painter.end()
    return pixmap


def _pin_top_left(point: QPointF, pin: QPixmap) -> QPointF:
    anchor_x = pin.width() * _PIN_ANCHOR_X_RATIO
    anchor_y = pin.height() * _PIN_ANCHOR_Y_RATIO
    return QPointF(point.x() - anchor_x, point.y() - anchor_y)


class _PinOverlay(QWidget):
    """Transparent overlay that repositions a lightweight pin label."""

    def __init__(self, owner: "InfoLocationMapView", parent: QWidget) -> None:
        super().__init__(parent)
        self._owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._pin = _build_pin_pixmap(_PIN_ICON_WIDTH, _PIN_ICON_HEIGHT)
        self._pin_label = QLabel(self)
        self._pin_label.setPixmap(self._pin)
        self._pin_label.setFixedSize(self._pin.size())
        self._pin_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._pin_label.hide()

    def set_screen_point(self, point: QPointF | None) -> None:
        if point is None:
            self._pin_label.hide()
            return

        top_left = _pin_top_left(point, self._pin)
        x = int(round(top_left.x()))
        y = int(round(top_left.y()))
        self._pin_label.move(x, y)
        self._pin_label.show()
        self._pin_label.raise_()

    def pin_pixmap(self) -> QPixmap:
        return self._pin


class _RoundedMapClipFrame(QWidget):
    """Paint and clip the mini-map viewport as a stable rounded surface."""

    def __init__(self, *, radius: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._radius = float(radius)
        self.setObjectName("infoLocationMapClipFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAutoFillBackground(False)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().color(self.backgroundRole()))
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(self._radius, rect.width() / 2.0, rect.height() / 2.0)
        painter.drawRoundedRect(rect, radius, radius)


class InfoLocationMapView(QWidget):
    """Embed a non-editing map preview centred on a single assigned location."""

    DEFAULT_ZOOM = 8.0
    _MINIMUM_SIDE = 156
    _CORNER_RADIUS = 12.0
    _SETTLE_SYNC_DELAY_MS = 24
    _VIEWPORT_MATCH_EPSILON = 1e-6
    _PIN_SYNC_EVENT = QEvent.Type(QEvent.registerEventType())
    _VIEWPORT_SYNC_EVENT = QEvent.Type(QEvent.registerEventType())

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        map_runtime: MapRuntimePort | None = None,
    ) -> None:
        super().__init__(parent)
        _configure_opaque_map_container(self)
        self._map_runtime = map_runtime
        self._map_runtime_capabilities = (
            map_runtime.capabilities() if map_runtime is not None else None
        )
        self._map_package_root = resolve_map_package_root(map_runtime)
        self._map_widget: MapWidgetBase | None = None
        self._map_event_targets: list[QObject] = []
        self._application_event_filter_installed = False
        self._drag_cursor = DragCursorManager()
        self._dragging = False
        self._backend_kind = "unavailable"
        self._latitude: float | None = None
        self._longitude: float | None = None
        self._screen_point: QPointF | None = None
        self._requested_zoom = self.DEFAULT_ZOOM
        self._last_set_location: tuple[float, float, float] | None = None
        self._pending_viewport_sync = False
        self._pending_pin_sync_queue = False
        self._pending_viewport_sync_queue = False
        self._pin_paint_callback: Callable[[QPainter], None] | None = None
        self._uses_post_render_pin = False
        self._pin_sync_timer = QTimer(self)
        self._pin_sync_timer.setSingleShot(True)
        self._pin_sync_timer.setInterval(0)
        self._pin_sync_timer.timeout.connect(self._sync_pin_position_now)
        self._pin_settle_timer = QTimer(self)
        self._pin_settle_timer.setSingleShot(True)
        self._pin_settle_timer.setInterval(self._SETTLE_SYNC_DELAY_MS)
        self._pin_settle_timer.timeout.connect(self._sync_pin_position_now)
        self._viewport_sync_timer = QTimer(self)
        self._viewport_sync_timer.setSingleShot(True)
        self._viewport_sync_timer.setInterval(0)
        self._viewport_sync_timer.timeout.connect(self._apply_pending_viewport_now)
        self._viewport_settle_timer = QTimer(self)
        self._viewport_settle_timer.setSingleShot(True)
        self._viewport_settle_timer.setInterval(self._SETTLE_SYNC_DELAY_MS)
        self._viewport_settle_timer.timeout.connect(self._apply_pending_viewport_now)

        self.setMinimumSize(self._MINIMUM_SIDE, self._MINIMUM_SIDE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self._map_clip_frame = _RoundedMapClipFrame(
            radius=self._CORNER_RADIUS,
            parent=self,
        )
        _configure_opaque_map_container(self._map_clip_frame)
        self._map_clip_layout = QVBoxLayout(self._map_clip_frame)
        self._map_clip_layout.setContentsMargins(0, 0, 0, 0)
        self._map_clip_layout.setSpacing(0)
        self._layout.addWidget(self._map_clip_frame, 1)

        self._map_host = QWidget(self._map_clip_frame)
        self._map_host.setObjectName("infoLocationMapHost")
        _configure_opaque_map_container(self._map_host)
        self._map_host_layout = QVBoxLayout(self._map_host)
        self._map_host_layout.setContentsMargins(0, 0, 0, 0)
        self._map_host_layout.setSpacing(0)
        self._map_clip_layout.addWidget(self._map_host, 1)

        self._message_label = QLabel("", self)
        self._message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message_label.setWordWrap(True)
        self._message_label.hide()
        self._layout.addWidget(self._message_label, 1)

        self._overlay = _PinOverlay(self, self._map_host)
        self._overlay.hide()

    def set_map_runtime(self, map_runtime: MapRuntimePort | None) -> None:
        """Bind the session-owned runtime snapshot used for mini-map creation."""

        previous_package_root = self._map_package_root
        self._map_runtime = map_runtime
        self._map_runtime_capabilities = (
            map_runtime.capabilities() if map_runtime is not None else None
        )
        self._map_package_root = resolve_map_package_root(map_runtime)
        if self._map_widget is not None and self._map_package_root != previous_package_root:
            self.shutdown()

    def map_widget(self) -> MapWidgetBase | None:
        return self._map_widget

    def current_location(self) -> tuple[float | None, float | None]:
        return self._latitude, self._longitude

    def set_location(self, latitude: float, longitude: float, *, zoom: float | None = None) -> None:
        next_latitude = float(latitude)
        next_longitude = float(longitude)
        next_zoom = float(zoom if zoom is not None else self.DEFAULT_ZOOM)
        same_location = self._matches_current_location(
            next_latitude,
            next_longitude,
            next_zoom,
        )
        self._latitude = next_latitude
        self._longitude = next_longitude
        self._requested_zoom = next_zoom
        self._last_set_location = (next_latitude, next_longitude, next_zoom)
        if same_location and self._map_widget is not None:
            self._message_label.hide()
            self._map_clip_frame.show()
            self._map_host.show()
            self._sync_corner_masks()
            self._sync_overlay_geometry()
            if self._screen_point is None and self._map_widget_ready_for_sync():
                self._sync_pin_position_now()
            return

        self._pending_viewport_sync = True
        self._screen_point = None
        if self._map_widget is None:
            self._create_map_widget()
        if self._map_widget is None:
            self._message_label.setText("Map preview unavailable")
            self._message_label.show()
            self._map_clip_frame.hide()
            self._map_host.hide()
            self._overlay.hide()
            self._pending_viewport_sync = False
            return

        self._message_label.hide()
        self._map_clip_frame.show()
        self._map_host.show()
        self._sync_overlay_geometry()
        if self._map_widget_ready_for_sync():
            self._apply_pending_viewport_now()
        self._queue_viewport_sync()
        if self._screen_point is None:
            self._overlay.set_screen_point(None)
            self._overlay.hide()

    def clear_location(self) -> None:
        already_clear = (
            self._latitude is None
            and self._longitude is None
            and self._screen_point is None
            and not self._pending_viewport_sync
        )
        if already_clear:
            return
        self._reset_drag_cursor()
        self._latitude = None
        self._longitude = None
        self._screen_point = None
        self._last_set_location = None
        self._pending_viewport_sync = False
        self._pin_sync_timer.stop()
        self._pin_settle_timer.stop()
        self._viewport_sync_timer.stop()
        self._viewport_settle_timer.stop()
        self._overlay.set_screen_point(None)
        self._overlay.hide()
        self._request_pin_repaint()

    def shutdown(self) -> None:
        self._pin_sync_timer.stop()
        self._pin_settle_timer.stop()
        self._viewport_sync_timer.stop()
        self._viewport_settle_timer.stop()
        self._reset_drag_cursor()
        self._remove_map_event_filters()
        if self._map_widget is not None:
            map_widget = self._map_widget
            self._remove_pin_painter(map_widget)
            self._map_widget = None
            try:
                map_widget.shutdown()
            except Exception:
                LOGGER.debug("Mini-map shutdown failed", exc_info=True)
            if isinstance(map_widget, QWidget):
                self._map_host_layout.removeWidget(map_widget)
                map_widget.hide()
                map_widget.setParent(None)
                map_widget.deleteLater()
        self._screen_point = None
        self._pending_viewport_sync = False
        self._overlay.set_screen_point(None)
        self._overlay.hide()
        self._map_clip_frame.hide()
        self._map_host.hide()

    def hasHeightForWidth(self) -> bool:  # type: ignore[override]
        return True

    def heightForWidth(self, width: int) -> int:  # type: ignore[override]
        return max(self._MINIMUM_SIDE, int(width))

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(self._MINIMUM_SIDE, self._MINIMUM_SIDE)

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(self._MINIMUM_SIDE, self._MINIMUM_SIDE)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_square_height()
        self._sync_corner_masks()
        self._sync_overlay_geometry()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._sync_overlay_geometry()
        self._queue_viewport_sync()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._reset_drag_cursor()
        super().hideEvent(event)

    def event(self, event: QEvent) -> bool:  # type: ignore[override]
        if event.type() == self._PIN_SYNC_EVENT:
            self._pending_pin_sync_queue = False
            if self._map_widget_ready_for_sync():
                self._sync_pin_position_now()
            return True
        if event.type() == self._VIEWPORT_SYNC_EVENT:
            self._pending_viewport_sync_queue = False
            self._apply_pending_viewport_now()
            return True
        return super().event(event)

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        is_map_target = self._is_map_event_target(watched)
        if is_map_target or self._is_global_map_drag_event(event):
            self._handle_drag_cursor_event(event)

        if is_map_target and event.type() == QEvent.Type.Resize:
            self._sync_corner_masks()
            self._sync_overlay_geometry()
            self._queue_viewport_sync()
        return super().eventFilter(watched, event)

    def _is_map_event_target(self, watched: object) -> bool:
        return any(watched is target for target in self._map_event_targets)

    def _is_global_map_drag_event(self, event: QEvent) -> bool:
        event_type = event.type()
        if event_type not in {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.Leave,
            QEvent.Type.Hide,
        }:
            return False
        if self._dragging:
            return True
        if event_type != QEvent.Type.MouseButtonPress:
            return False
        button = getattr(event, "button", lambda: Qt.MouseButton.NoButton)()
        return button == Qt.MouseButton.LeftButton and self._event_is_inside_map_host(event)

    def _event_is_inside_map_host(self, event: QEvent) -> bool:
        global_point: QPoint | None = None
        global_position = getattr(event, "globalPosition", None)
        if callable(global_position):
            global_point = global_position().toPoint()
        else:
            global_pos = getattr(event, "globalPos", None)
            if callable(global_pos):
                global_point = global_pos()

        if global_point is None:
            return False

        host_rect = QRect(self._map_host.mapToGlobal(QPoint(0, 0)), self._map_host.size())
        return host_rect.contains(global_point)

    def _handle_drag_cursor_event(self, event: QEvent) -> None:
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress:
            button = getattr(event, "button", lambda: Qt.MouseButton.NoButton)()
            if button == Qt.MouseButton.LeftButton:
                self._dragging = True
                self._set_drag_cursor()
        elif event_type == QEvent.Type.MouseMove:
            buttons = getattr(event, "buttons", lambda: Qt.MouseButton.NoButton)()
            if self._dragging and buttons & Qt.MouseButton.LeftButton:
                self._set_drag_cursor()
            elif self._dragging and not (buttons & Qt.MouseButton.LeftButton):
                self._reset_drag_cursor()
        elif event_type == QEvent.Type.MouseButtonRelease:
            button = getattr(event, "button", lambda: Qt.MouseButton.NoButton)()
            if button == Qt.MouseButton.LeftButton:
                self._reset_drag_cursor()
        elif event_type in (QEvent.Type.Leave, QEvent.Type.Hide):
            self._reset_drag_cursor()

    def _rounded_region(self, width: int, height: int) -> QRegion:
        radius = min(self._CORNER_RADIUS, width / 2.0, height / 2.0)
        if width <= 0 or height <= 0 or radius <= 0:
            return QRegion(0, 0, max(0, width), max(0, height))

        path = QPainterPath()
        path.moveTo(radius, 0.0)
        path.lineTo(float(width) - radius, 0.0)
        path.quadTo(float(width), 0.0, float(width), radius)
        path.lineTo(float(width), float(height) - radius)
        path.quadTo(float(width), float(height), float(width) - radius, float(height))
        path.lineTo(radius, float(height))
        path.quadTo(0.0, float(height), 0.0, float(height) - radius)
        path.lineTo(0.0, radius)
        path.quadTo(0.0, 0.0, radius, 0.0)
        path.closeSubpath()
        return QRegion(path.toFillPolygon().toPolygon())

    def _sync_corner_masks(self) -> None:
        self.setMask(self._rounded_region(self.width(), self.height()))
        self._apply_rounded_mask(self._map_clip_frame)
        self._apply_rounded_mask(self._map_host)
        if isinstance(self._map_widget, QWidget):
            self._apply_rounded_mask(self._map_widget)
        for target in self._map_event_targets:
            self._apply_rounded_mask(target)

    def _apply_rounded_mask(self, target: QObject) -> None:
        if isinstance(target, QWidget):
            target.setMask(self._rounded_region(target.width(), target.height()))
            return
        if isinstance(target, QWindow):
            size = target.size()
            target.setMask(self._rounded_region(size.width(), size.height()))

    def _matches_current_location(
        self,
        latitude: float,
        longitude: float,
        zoom: float,
    ) -> bool:
        current = self._last_set_location
        if current is None:
            return False
        current_lat, current_lon, current_zoom = current
        return (
            abs(current_lat - latitude) <= self._VIEWPORT_MATCH_EPSILON
            and abs(current_lon - longitude) <= self._VIEWPORT_MATCH_EPSILON
            and abs(current_zoom - zoom) <= self._VIEWPORT_MATCH_EPSILON
            and not self._pending_viewport_sync
        )

    def _sync_overlay_geometry(self) -> None:
        target_rect = self._visible_map_rect()
        if target_rect is None:
            return
        self._overlay.setGeometry(target_rect)
        if self._uses_post_render_pin:
            self._overlay.hide()
            self._request_pin_repaint()
        else:
            self._overlay.raise_()
        if self._latitude is not None and self._longitude is not None:
            if self._map_widget_ready_for_sync():
                self._sync_pin_position_now()
            self._schedule_pin_sync()

    def _visible_map_rect(self) -> QRect | None:
        if self._map_widget is None:
            return None
        return self._map_widget.geometry()

    def _sync_square_height(self) -> None:
        target_height = max(self._MINIMUM_SIDE, self.width())
        if self.minimumHeight() == target_height and self.maximumHeight() == target_height:
            return
        self.setFixedHeight(target_height)
        self.updateGeometry()

    def _map_widget_ready_for_sync(self) -> bool:
        if self._map_widget is None:
            return False
        visible_rect = self._visible_map_rect()
        if visible_rect is None or visible_rect.isEmpty():
            return False
        if not self._map_host.isVisible():
            return False
        if isinstance(self._map_widget, QWidget):
            if not self._map_widget.isVisible():
                return False
            if self._map_widget.width() <= 1 or self._map_widget.height() <= 1:
                return False
        return True

    def _current_view_matches_requested_location(self) -> bool:
        if self._map_widget is None or self._latitude is None or self._longitude is None:
            return False
        if not self._map_widget_ready_for_sync():
            return False

        try:
            center_lon, center_lat = self._map_widget.center_lonlat()
        except Exception:
            return False

        if (
            abs(float(center_lon) - self._longitude) > self._VIEWPORT_MATCH_EPSILON
            or abs(float(center_lat) - self._latitude) > self._VIEWPORT_MATCH_EPSILON
        ):
            return False

        try:
            current_zoom = float(getattr(self._map_widget, "zoom"))
        except Exception:
            return False

        return abs(current_zoom - self._requested_zoom) <= self._VIEWPORT_MATCH_EPSILON

    def _schedule_viewport_sync(self) -> None:
        if not self._pending_viewport_sync:
            return
        if self._map_widget is None or self._latitude is None or self._longitude is None:
            return
        self._viewport_sync_timer.start()
        self._viewport_settle_timer.start()

    def _queue_viewport_sync(self) -> None:
        if self._pending_viewport_sync_queue:
            return
        self._pending_viewport_sync_queue = True
        QCoreApplication.postEvent(
            self,
            QEvent(self._VIEWPORT_SYNC_EVENT),
            Qt.EventPriority.LowEventPriority.value,
        )

    def _apply_pending_viewport_now(self) -> None:
        if not self._pending_viewport_sync:
            return
        if self._map_widget is None or self._latitude is None or self._longitude is None:
            self._pending_viewport_sync = False
            return
        if not self._map_widget_ready_for_sync():
            return

        try:
            # Apply zoom first, then re-center once the target scale is known.
            self._map_widget.set_zoom(self._requested_zoom)
            self._map_widget.center_on(self._longitude, self._latitude)
        except Exception:
            LOGGER.warning("Failed to update info-panel mini-map", exc_info=True)
            self._pending_viewport_sync = False
            return

        self._sync_pin_position_now()
        self._schedule_pin_sync()
        if self._current_view_matches_requested_location():
            self._pending_viewport_sync = False

    def _connect_map_signals(self) -> None:
        if self._map_widget is None:
            return

        view_changed = getattr(self._map_widget, "viewChanged", None)
        if view_changed is not None:
            view_changed.connect(self._handle_map_view_changed)

        panned = getattr(self._map_widget, "panned", None)
        if panned is not None:
            panned.connect(self._handle_map_panned)

        pan_finished = getattr(self._map_widget, "panFinished", None)
        if pan_finished is not None:
            pan_finished.connect(self._handle_map_pan_finished)

    def _project_current_location(self, *, center_fallback: bool) -> QPointF | None:
        if self._map_widget is None or self._latitude is None or self._longitude is None:
            return None

        visible_rect = self._visible_map_rect()
        if visible_rect is None:
            return None

        point = self._map_widget.project_lonlat(self._longitude, self._latitude)
        if point is None and center_fallback:
            return QPointF(visible_rect.width() / 2.0, visible_rect.height() / 2.0)
        return point

    def _sync_pin_position(self, *, center_fallback: bool) -> None:
        point = self._project_current_location(center_fallback=center_fallback)
        self._screen_point = QPointF(point) if point is not None else None
        if self._uses_post_render_pin:
            self._overlay.set_screen_point(None)
            self._overlay.hide()
            self._request_pin_repaint()
            return

        self._overlay.set_screen_point(self._screen_point)
        if point is None:
            self._overlay.hide()
        else:
            self._overlay.show()
            self._overlay.raise_()

    def _sync_pin_position_now(self) -> None:
        self._sync_pin_position(center_fallback=True)

    def _schedule_pin_sync(self) -> None:
        if self._latitude is None or self._longitude is None:
            return
        self._pin_sync_timer.start()
        self._pin_settle_timer.start()

    def _queue_pin_sync(self) -> None:
        if self._pending_pin_sync_queue:
            return
        self._pending_pin_sync_queue = True
        QCoreApplication.postEvent(
            self,
            QEvent(self._PIN_SYNC_EVENT),
            Qt.EventPriority.LowEventPriority.value,
        )

    def _handle_map_view_changed(self, _center_x: float, _center_y: float, _zoom: float) -> None:
        if self._pending_viewport_sync and self._current_view_matches_requested_location():
            self._pending_viewport_sync = False
        self._queue_pin_sync()

    def _handle_map_panned(self, delta: QPointF) -> None:
        self._pending_viewport_sync = False
        if self._screen_point is None:
            self._schedule_pin_sync()
            return

        self._screen_point = QPointF(
            self._screen_point.x() + float(delta.x()),
            self._screen_point.y() + float(delta.y()),
        )
        if self._uses_post_render_pin:
            self._request_pin_repaint()
        else:
            self._overlay.set_screen_point(self._screen_point)
        self._pin_settle_timer.start()

    def _handle_map_pan_finished(self) -> None:
        self._queue_pin_sync()

    def _create_map_widget(self) -> None:
        if self._map_widget is not None:
            return
        map_source = MapSourceSpec.osmand_default(self._map_package_root).resolved(
            self._map_package_root
        )
        result = create_map_widget(
            self._map_host,
            map_source=map_source,
            map_runtime_capabilities=self._map_runtime_capabilities,
            package_root=self._map_package_root,
            log=LOGGER,
            context="info-panel mini-map",
        )
        self._map_widget = result.widget
        self._backend_kind = result.backend_kind

        if self._map_widget is None:
            self._map_host.hide()
            self._message_label.setText("Map preview unavailable")
            self._message_label.show()
            return

        if isinstance(self._map_widget, QWidget):
            self._map_widget.setMinimumSize(0, 0)
            self._map_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._install_map_event_filters()
        self._map_host_layout.addWidget(self._map_widget, 1)
        self._install_pin_painter_if_supported()
        self._connect_map_signals()
        self._sync_square_height()
        self._sync_corner_masks()
        self._sync_overlay_geometry()
        QTimer.singleShot(0, self._sync_overlay_geometry)

    def _install_map_event_filters(self) -> None:
        self._remove_map_event_filters()
        if self._map_widget is None:
            return

        targets: list[QObject] = []
        if isinstance(self._map_widget, QObject):
            targets.append(self._map_widget)

        event_target_getter = getattr(self._map_widget, "event_target", None)
        if callable(event_target_getter):
            try:
                event_target = event_target_getter()
            except Exception:
                LOGGER.debug("Failed to resolve info-panel mini-map event target", exc_info=True)
            else:
                if isinstance(event_target, QObject) and not any(
                    event_target is target for target in targets
                ):
                    targets.append(event_target)

        for target in targets:
            target.installEventFilter(self)
        self._map_event_targets = targets
        app = QApplication.instance()
        if app is not None and not self._application_event_filter_installed:
            app.installEventFilter(self)
            self._application_event_filter_installed = True

    def _remove_map_event_filters(self) -> None:
        for target in self._map_event_targets:
            try:
                target.removeEventFilter(self)
            except RuntimeError:
                continue
        self._map_event_targets = []
        app = QApplication.instance()
        if app is not None and self._application_event_filter_installed:
            app.removeEventFilter(self)
        self._application_event_filter_installed = False

    def _cursor_targets(self) -> tuple[object, ...]:
        targets: list[object] = [self, self._map_host]
        if self._map_widget is not None:
            targets.append(self._map_widget)
        for target in self._map_event_targets:
            if not any(target is existing for existing in targets):
                targets.append(target)
        return tuple(targets)

    def _set_drag_cursor(self) -> None:
        self._drag_cursor.set_cursor(Qt.CursorShape.ClosedHandCursor, self._cursor_targets())

    def _reset_drag_cursor(self) -> None:
        self._dragging = False
        self._drag_cursor.reset(self._cursor_targets())

    def _install_pin_painter_if_supported(self) -> None:
        if self._map_widget is None:
            return
        add_post_render_painter = getattr(self._map_widget, "add_post_render_painter", None)
        supports_post_render_painter = getattr(
            self._map_widget,
            "supports_post_render_painter",
            lambda: True,
        )
        if not callable(add_post_render_painter) or not supports_post_render_painter():
            self._uses_post_render_pin = False
            self._pin_paint_callback = None
            return

        self._pin_paint_callback = self._paint_pin
        self._uses_post_render_pin = True
        add_post_render_painter(self._pin_paint_callback)
        self._overlay.set_screen_point(None)
        self._overlay.hide()

    def _remove_pin_painter(self, map_widget: MapWidgetBase) -> None:
        callback = self._pin_paint_callback
        if callback is None:
            return
        remove_post_render_painter = getattr(map_widget, "remove_post_render_painter", None)
        if callable(remove_post_render_painter):
            try:
                remove_post_render_painter(callback)
            except Exception:
                LOGGER.debug("Failed to remove info-panel mini-map pin painter", exc_info=True)
        self._pin_paint_callback = None
        self._uses_post_render_pin = False

    def _paint_pin(self, painter: QPainter) -> None:
        if self._screen_point is None:
            return
        pin = self._overlay.pin_pixmap()
        if pin.isNull():
            return
        top_left = _pin_top_left(self._screen_point, pin)
        painter.drawPixmap(int(round(top_left.x())), int(round(top_left.y())), pin)

    def _request_pin_repaint(self) -> None:
        if self._map_widget is None:
            return
        request_full_update = getattr(self._map_widget, "request_full_update", None)
        if callable(request_full_update):
            request_full_update()
        elif isinstance(self._map_widget, QWidget):
            self._map_widget.update()


__all__ = ["InfoLocationMapView"]
