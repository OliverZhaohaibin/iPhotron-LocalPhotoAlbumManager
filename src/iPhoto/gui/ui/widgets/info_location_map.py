"""Compact single-pin map preview used by the detail info panel."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from maps.map_sources import MapSourceSpec
from maps.map_widget._map_widget_base import MapWidgetBase
from maps.map_widget.map_gl_widget import MapGLWidget
from maps.map_widget.map_widget import MapWidget

from ..icons import load_icon
from .photo_map_view import check_opengl_support, choose_map_widget_backend

LOGGER = logging.getLogger(__name__)

_MAPS_PACKAGE_ROOT = Path(__file__).resolve().parents[4] / "maps"


class _PinOverlay(QWidget):
    """Transparent overlay that repositions a lightweight pin label."""

    def __init__(self, owner: "InfoLocationMapView", parent: QWidget) -> None:
        super().__init__(parent)
        self._owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._pin = load_icon("map.pin.svg", size=(30, 38)).pixmap(30, 38)
        self._pin_label = QLabel(self)
        self._pin_label.setPixmap(self._pin)
        self._pin_label.setFixedSize(self._pin.size())
        self._pin_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._pin_label.hide()

    def set_screen_point(self, point: QPointF | None) -> None:
        if point is None:
            self._pin_label.hide()
            return

        x = int(round(point.x() - self._pin.width() / 2.0))
        y = int(round(point.y() - self._pin.height()))
        self._pin_label.move(x, y)
        self._pin_label.show()
        self._pin_label.raise_()


class InfoLocationMapView(QWidget):
    """Embed a non-editing map preview centred on a single assigned location."""

    DEFAULT_ZOOM = 8.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._map_widget: MapWidgetBase | None = None
        self._backend_kind = "unavailable"
        self._latitude: float | None = None
        self._longitude: float | None = None
        self._screen_point: QPointF | None = None

        self.setMinimumHeight(156)
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
        if self._map_widget is None:
            self._message_label.setText("Map preview unavailable")
            self._message_label.show()
            self._map_host.hide()
            self._overlay.hide()
            return

        self._message_label.hide()
        self._map_host.show()
        try:
            self._map_widget.center_on(self._longitude, self._latitude)
            self._map_widget.set_zoom(float(zoom if zoom is not None else self.DEFAULT_ZOOM))
        except Exception:
            LOGGER.warning("Failed to update info-panel mini-map", exc_info=True)
        self._overlay.show()
        self._overlay.raise_()
        self._sync_pin_position(center_fallback=True)

    def clear_location(self) -> None:
        self._latitude = None
        self._longitude = None
        self._screen_point = None
        self._overlay.set_screen_point(None)
        self._overlay.hide()

    def shutdown(self) -> None:
        if self._map_widget is not None:
            try:
                self._map_widget.shutdown()
            except Exception:
                LOGGER.debug("Mini-map shutdown failed", exc_info=True)
            self._map_widget = None

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_overlay_geometry()

    def _sync_overlay_geometry(self) -> None:
        if self._map_widget is None:
            return
        self._overlay.setGeometry(QRect(0, 0, self._map_host.width(), self._map_host.height()))
        self._overlay.raise_()
        if self._latitude is not None and self._longitude is not None:
            self._sync_pin_position(center_fallback=True)

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

        point = self._map_widget.project_lonlat(self._longitude, self._latitude)
        if point is None and center_fallback:
            return QPointF(self._map_host.width() / 2.0, self._map_host.height() / 2.0)
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

    def _handle_map_view_changed(self, _center_x: float, _center_y: float, _zoom: float) -> None:
        self._sync_pin_position(center_fallback=True)

    def _handle_map_panned(self, delta: QPointF) -> None:
        if self._screen_point is None:
            self._sync_pin_position(center_fallback=True)
            return

        self._screen_point = QPointF(
            self._screen_point.x() + float(delta.x()),
            self._screen_point.y() + float(delta.y()),
        )
        self._overlay.set_screen_point(self._screen_point)

    def _handle_map_pan_finished(self) -> None:
        self._sync_pin_position(center_fallback=True)

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

        self._map_host_layout.addWidget(self._map_widget, 1)
        self._connect_map_signals()
        self._sync_overlay_geometry()


__all__ = ["InfoLocationMapView"]
