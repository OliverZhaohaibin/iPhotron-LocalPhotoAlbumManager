"""QQuickWidget-based map widget powered by Qt Location's OSM plugin."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QObject, QPointF, QUrl, Qt, Signal
from PySide6.QtGui import QCloseEvent, QMouseEvent, QResizeEvent, QWheelEvent
from PySide6.QtPositioning import QGeoCoordinate
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QWidget

from maps.map_sources import MapBackendMetadata, MapSourceSpec

from .map_renderer import CityAnnotation

TILE_SIZE = 256
MERCATOR_LAT_BOUND = 85.05112878


def lonlat_to_normalized(lon: float, lat: float) -> tuple[float, float] | None:
    """Convert longitude/latitude into normalised Web Mercator coordinates."""

    try:
        lon = float(lon)
        lat = float(lat)
    except (TypeError, ValueError):
        return None

    lat = max(min(lat, MERCATOR_LAT_BOUND), -MERCATOR_LAT_BOUND)
    x = (lon + 180.0) / 360.0
    sin_lat = math.sin(math.radians(lat))
    y = 0.5 - math.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * math.pi)
    return x, y


def normalized_to_lonlat(x: float, y: float) -> tuple[float, float]:
    """Convert normalised Web Mercator coordinates back to lon/lat."""

    x = float(x) % 1.0
    y = min(max(float(y), 0.0), 1.0)
    lon = x * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y))))
    return lon, lat


class QtLocationMapWidget(QQuickWidget):
    """Render the background map through Qt Location's online OSM plugin."""

    viewChanged = Signal(float, float, float)
    panned = Signal(QPointF)
    panFinished = Signal()

    BACKEND_METADATA = MapBackendMetadata(
        min_zoom=2.0,
        max_zoom=19.0,
        provides_place_labels=True,
        tile_kind="raster",
        tile_scheme="xyz",
        fetch_max_zoom=19,
    )

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        map_source: MapSourceSpec | None = None,
        tile_root: Path | str = "tiles",
        style_path: Path | str = "style.json",
    ) -> None:
        super().__init__(parent)
        self._map_source = map_source
        self._tile_root = tile_root
        self._style_path = style_path
        self._map_object: QObject | None = None
        self._zoom = float(self.BACKEND_METADATA.min_zoom)
        self._min_zoom = float(self.BACKEND_METADATA.min_zoom)
        self._max_zoom = float(self.BACKEND_METADATA.max_zoom)
        self._default_zoom = float(self.BACKEND_METADATA.min_zoom)
        self._center_x = 0.5
        self._center_y = 0.5
        self._dragging = False
        self._last_mouse_pos = QPointF()

        self.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.setMouseTracking(True)
        self.setMinimumSize(640, 480)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        qml_path = Path(__file__).resolve().parent / "qml" / "QtLocationMapView.qml"
        self.setSource(QUrl.fromLocalFile(str(qml_path)))

        if self.status() == QQuickWidget.Status.Error:
            errors = "; ".join(error.toString() for error in self.errors())
            raise RuntimeError(f"Unable to load Qt Location map scene: {errors}")

        root = self.rootObject()
        if root is None:
            raise RuntimeError("Qt Location map scene did not create a root object")

        self._map_object = root.findChild(QObject, "map")
        if self._map_object is None:
            raise RuntimeError("Qt Location map scene does not expose the map object")

        self._sync_map_camera()

    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float) -> None:
        zoom = max(self._min_zoom, min(self._max_zoom, float(zoom)))
        if abs(zoom - self._zoom) <= 1e-6:
            return
        self._zoom = zoom
        self._wrap_center()
        self._sync_map_camera()
        self._emit_view_change()

    def reset_view(self) -> None:
        self._center_x = 0.5
        self._center_y = 0.5
        self._zoom = self._default_zoom
        self._wrap_center()
        self._sync_map_camera()
        self._emit_view_change()

    def pan_by_pixels(self, delta_x: float, delta_y: float) -> None:
        """Translate the viewport by a fixed on-screen pixel delta."""

        world_size = self._world_size()
        self._center_x -= float(delta_x) / world_size
        self._center_y -= float(delta_y) / world_size
        self._wrap_center()
        self._sync_map_camera()
        self._emit_view_change()

    def center_lonlat(self) -> tuple[float, float]:
        """Return the current viewport centre as ``(lon, lat)``."""

        return normalized_to_lonlat(self._center_x, self._center_y)

    def shutdown(self) -> None:
        return None

    def map_backend_metadata(self) -> MapBackendMetadata:
        return self.BACKEND_METADATA

    def project_lonlat(self, lon: float, lat: float) -> QPointF | None:
        world_position = lonlat_to_normalized(lon, lat)
        if world_position is None:
            return None

        world_size = self._world_size()
        world_x = world_position[0] * world_size
        world_y = world_position[1] * world_size

        center_px = self._center_x * world_size
        center_py = self._center_y * world_size
        delta_x = world_x - center_px
        if delta_x > world_size / 2.0:
            world_x -= world_size
        elif delta_x < -world_size / 2.0:
            world_x += world_size

        top_left_x = center_px - self.width() / 2.0
        top_left_y = center_py - self.height() / 2.0
        return QPointF(world_x - top_left_x, world_y - top_left_y)

    def center_on(self, lon: float, lat: float) -> None:
        normalized = lonlat_to_normalized(lon, lat)
        if normalized is None:
            return
        self._center_x, self._center_y = normalized
        self._wrap_center()
        self._sync_map_camera()
        self._emit_view_change()

    def focus_on(self, lon: float, lat: float, zoom_delta: float = 1.0) -> None:
        self.center_on(lon, lat)
        if zoom_delta:
            self.set_zoom(self._zoom + float(zoom_delta))

    def set_city_annotations(self, cities: Sequence[CityAnnotation]) -> None:
        return None

    def city_at(self, position: QPointF) -> str | None:
        return None

    def event_target(self) -> QWidget:
        """Return the widget that directly receives pointer input events."""

        return self

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._wrap_center()
        self._sync_map_camera()
        self._emit_view_change()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_mouse_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            current_pos = event.position()
            delta = current_pos - self._last_mouse_pos
            self._last_mouse_pos = current_pos
            if not delta.isNull():
                world_size = self._world_size()
                self._center_x -= delta.x() / world_size
                self._center_y -= delta.y() / world_size
                self._wrap_center()
                self._sync_map_camera()
                self.panned.emit(QPointF(delta))
                self._emit_view_change()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.unsetCursor()
            self.panFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return

        zoom_factor = 1.0 + delta / 1200.0
        new_zoom = max(self._min_zoom, min(self._max_zoom, self._zoom * zoom_factor))
        if abs(new_zoom - self._zoom) <= 1e-6:
            event.accept()
            return

        world_size = self._world_size()
        center_px = self._center_x * world_size
        center_py = self._center_y * world_size
        view_top_left_x = center_px - self.width() / 2.0
        view_top_left_y = center_py - self.height() / 2.0

        mouse_world_x = (view_top_left_x + event.position().x()) / world_size
        mouse_world_y = (view_top_left_y + event.position().y()) / world_size

        self._zoom = new_zoom
        new_world_size = self._world_size()
        new_center_px = mouse_world_x * new_world_size - event.position().x() + self.width() / 2.0
        new_center_py = mouse_world_y * new_world_size - event.position().y() + self.height() / 2.0

        self._center_x = new_center_px / new_world_size
        self._center_y = new_center_py / new_world_size
        self._wrap_center()
        self._sync_map_camera()
        self._emit_view_change()
        event.accept()

    def _emit_view_change(self) -> None:
        self.viewChanged.emit(float(self._center_x), float(self._center_y), float(self._zoom))

    def _sync_map_camera(self) -> None:
        if self._map_object is None:
            return
        lon, lat = normalized_to_lonlat(self._center_x, self._center_y)
        self._map_object.setProperty("center", QGeoCoordinate(lat, lon))
        self._map_object.setProperty("zoomLevel", float(self._zoom))

    def _world_size(self) -> float:
        return float(TILE_SIZE * (2.0 ** self._zoom))

    def _wrap_center(self) -> None:
        self._center_x %= 1.0

        world_size = self._world_size()
        viewport_height = max(1, self.height())
        half_view_ratio = viewport_height / (2.0 * world_size)
        if half_view_ratio >= 0.5:
            self._center_y = 0.5
            return

        min_center = half_view_ratio
        max_center = 1.0 - half_view_ratio
        self._center_y = min(max(self._center_y, min_center), max_center)


__all__ = [
    "QtLocationMapWidget",
    "lonlat_to_normalized",
    "normalized_to_lonlat",
]

