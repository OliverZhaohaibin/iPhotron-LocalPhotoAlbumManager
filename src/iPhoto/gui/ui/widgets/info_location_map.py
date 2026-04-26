"""Compact single-pin map preview used by the detail info panel."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QPainter, QPainterPath, QPixmap, QRegion, QResizeEvent
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from maps.map_sources import MapSourceSpec
from maps.map_widget._map_widget_base import MapWidgetBase
from maps.map_widget.map_gl_widget import MapGLWidget
from maps.map_widget.map_widget import MapWidget

from .photo_map_view import check_opengl_support, choose_map_widget_backend

LOGGER = logging.getLogger(__name__)

_MAPS_PACKAGE_ROOT = Path(__file__).resolve().parents[4] / "maps"
_PIN_ICON_PATH = Path(__file__).resolve().parents[1] / "icon" / "map.pin.svg"
_PIN_ICON_WIDTH = 90
_PIN_ICON_HEIGHT = 114
_PIN_ANCHOR_X_RATIO = 256.0 / 512.0
_PIN_ANCHOR_Y_RATIO = 418.0 / 512.0


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

        anchor_x = self._pin.width() * _PIN_ANCHOR_X_RATIO
        anchor_y = self._pin.height() * _PIN_ANCHOR_Y_RATIO
        x = int(round(point.x() - anchor_x))
        y = int(round(point.y() - anchor_y))
        self._pin_label.move(x, y)
        self._pin_label.show()
        self._pin_label.raise_()


class InfoLocationMapView(QWidget):
    """Embed a non-editing map preview centred on a single assigned location."""

    DEFAULT_ZOOM = 8.0
    _MINIMUM_SIDE = 156
    _CORNER_RADIUS = 12.0
    _SETTLE_SYNC_DELAY_MS = 24
    _VIEWPORT_MATCH_EPSILON = 1e-6
    _PIN_SYNC_EVENT = QEvent.Type(QEvent.registerEventType())
    _VIEWPORT_SYNC_EVENT = QEvent.Type(QEvent.registerEventType())

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._map_widget: MapWidgetBase | None = None
        self._backend_kind = "unavailable"
        self._latitude: float | None = None
        self._longitude: float | None = None
        self._screen_point: QPointF | None = None
        self._requested_zoom = self.DEFAULT_ZOOM
        self._pending_viewport_sync = False
        self._pending_pin_sync_queue = False
        self._pending_viewport_sync_queue = False
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

        self._map_host = QWidget(self)
        self._map_host.setObjectName("infoLocationMapHost")
        self._map_host_layout = QVBoxLayout(self._map_host)
        self._map_host_layout.setContentsMargins(0, 0, 0, 0)
        self._map_host_layout.setSpacing(0)
        self._layout.addWidget(self._map_host, 1)

        self._message_label = QLabel("", self)
        self._message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message_label.setWordWrap(True)
        self._message_label.hide()
        self._layout.addWidget(self._message_label, 1)

        self._overlay = _PinOverlay(self, self._map_host)
        self._overlay.hide()

        self._create_map_widget()

    def map_widget(self) -> MapWidgetBase | None:
        return self._map_widget

    def current_location(self) -> tuple[float | None, float | None]:
        return self._latitude, self._longitude

    def set_location(self, latitude: float, longitude: float, *, zoom: float | None = None) -> None:
        self._latitude = float(latitude)
        self._longitude = float(longitude)
        self._requested_zoom = float(zoom if zoom is not None else self.DEFAULT_ZOOM)
        self._pending_viewport_sync = True
        self._screen_point = None
        if self._map_widget is None:
            self._message_label.setText("Map preview unavailable")
            self._message_label.show()
            self._map_host.hide()
            self._overlay.hide()
            self._pending_viewport_sync = False
            return

        self._message_label.hide()
        self._map_host.show()
        self._sync_overlay_geometry()
        if self._map_widget_ready_for_sync():
            self._apply_pending_viewport_now()
        self._queue_viewport_sync()
        if self._screen_point is None:
            self._overlay.set_screen_point(None)
            self._overlay.hide()

    def clear_location(self) -> None:
        self._latitude = None
        self._longitude = None
        self._screen_point = None
        self._pending_viewport_sync = False
        self._pin_sync_timer.stop()
        self._pin_settle_timer.stop()
        self._viewport_sync_timer.stop()
        self._viewport_settle_timer.stop()
        self._overlay.set_screen_point(None)
        self._overlay.hide()

    def shutdown(self) -> None:
        self._viewport_sync_timer.stop()
        self._viewport_settle_timer.stop()
        if self._map_widget is not None:
            try:
                self._map_widget.shutdown()
            except Exception:
                LOGGER.debug("Mini-map shutdown failed", exc_info=True)
            self._map_widget = None

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
        if watched is self._map_widget and event.type() == QEvent.Type.Resize:
            self._sync_corner_masks()
            self._sync_overlay_geometry()
            self._queue_viewport_sync()
        return super().eventFilter(watched, event)

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
        host_region = self._rounded_region(self._map_host.width(), self._map_host.height())
        self._map_host.setMask(host_region)
        if isinstance(self._map_widget, QWidget):
            widget_region = self._rounded_region(self._map_widget.width(), self._map_widget.height())
            self._map_widget.setMask(widget_region)

    def _sync_overlay_geometry(self) -> None:
        target_rect = self._visible_map_rect()
        if target_rect is None:
            return
        self._overlay.setGeometry(target_rect)
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
        self._overlay.set_screen_point(self._screen_point)
        self._pin_settle_timer.start()

    def _handle_map_pan_finished(self) -> None:
        self._queue_pin_sync()

    def _create_map_widget(self) -> None:
        map_source = MapSourceSpec.osmand_default(_MAPS_PACKAGE_ROOT)
        use_opengl = check_opengl_support()
        widget_cls, resolved_map_source, backend_kind = choose_map_widget_backend(
            map_source,
            use_opengl=use_opengl,
        )
        assert resolved_map_source is not None
        try:
            self._map_widget = widget_cls(self._map_host, map_source=resolved_map_source)
            self._backend_kind = backend_kind
        except Exception as exc:
            if backend_kind == "osmand_native":
                LOGGER.warning(
                    "Native OsmAnd widget unavailable for info-panel mini-map, falling back: %s",
                    exc,
                )
                fallback_cls = MapGLWidget if use_opengl else MapWidget
                self._map_widget = fallback_cls(self._map_host, map_source=resolved_map_source)
                self._backend_kind = "osmand_python"
            elif widget_cls is MapGLWidget:
                LOGGER.warning(
                    "OpenGL mini-map unavailable, falling back to CPU renderer: %s",
                    exc,
                )
                self._map_widget = MapWidget(self._map_host, map_source=resolved_map_source)
                self._backend_kind = "legacy_python"
            else:
                LOGGER.warning("Mini-map backend unavailable", exc_info=True)
                self._map_widget = None
                self._backend_kind = "unavailable"

        if self._map_widget is None:
            self._map_host.hide()
            self._message_label.setText("Map preview unavailable")
            self._message_label.show()
            return

        if isinstance(self._map_widget, QWidget):
            self._map_widget.setMinimumSize(0, 0)
            self._map_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._map_widget.installEventFilter(self)
        self._map_host_layout.addWidget(self._map_widget, 1)
        self._connect_map_signals()
        self._sync_square_height()
        self._sync_corner_masks()
        self._sync_overlay_geometry()
        QTimer.singleShot(0, self._sync_overlay_geometry)


__all__ = ["InfoLocationMapView"]
