"""PySide6 host widget for the native OsmAnd OpenGL map control."""

from __future__ import annotations

import ctypes
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import PySide6
import shiboken6
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from maps.map_sources import (
    MapBackendMetadata,
    MapSourceSpec,
    resolve_osmand_native_widget_library,
)
from maps.map_widget.map_renderer import CityAnnotation
from maps.tile_parser import TileLoadingError

MERCATOR_LAT_BOUND = 85.05112878
_NATIVE_DLL_DIR_HANDLES: list[Any] = []
_NATIVE_WIDGET_RUNTIME_PROBE: dict[Path, tuple[bool, str | None]] = {}


@dataclass(frozen=True)
class _BridgeAPI:
    library: ctypes.WinDLL


def _ensure_dll_directory(path: Path) -> None:
    if hasattr(os, "add_dll_directory") and path.exists():
        _NATIVE_DLL_DIR_HANDLES.append(os.add_dll_directory(str(path)))


def _load_bridge(library_path: Path) -> _BridgeAPI:
    if os.name != "nt":
        raise TileLoadingError("The native OsmAnd widget is currently only supported on Windows")

    # Register all directories that contain transitive DLL dependencies BEFORE
    # calling WinDLL. On Windows, add_dll_directory() only works if called prior
    # to the first LoadLibrary for that DLL.
    pyside_root = Path(PySide6.__file__).resolve().parent
    shiboken_root = Path(shiboken6.__file__).resolve().parent
    _ensure_dll_directory(pyside_root)
    _ensure_dll_directory(shiboken_root)

    # Register colocated runtime mirrors for dependency lookup, but keep the
    # originally resolved widget DLL path intact. Otherwise a freshly built
    # official Release binary can be silently replaced by an older dist-msvc
    # copy that happens to exist in the workspace.
    dist_dir = library_path.parent
    package_root = Path(__file__).resolve().parents[3]
    for candidate_dist in [
        library_path.parent.parent.parent.parent
            / "tools" / "osmand_render_helper_native" / "dist-msvc",
        library_path.parent.parent.parent.parent
            / "tools" / "osmand_render_helper_native" / "dist",
        package_root / "tools" / "osmand_render_helper_native" / "dist-msvc",
        package_root / "tools" / "osmand_render_helper_native" / "dist",
    ]:
        if candidate_dist.is_dir():
            dist_dir = candidate_dist
            break

    # Register the selected DLL directory first so dependency lookup stays
    # aligned with the widget binary we are about to load.
    _ensure_dll_directory(library_path.parent)
    if dist_dir != library_path.parent:
        _ensure_dll_directory(dist_dir)

    library = ctypes.WinDLL(str(library_path))
    library.osmand_create_map_widget.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    library.osmand_create_map_widget.restype = ctypes.c_void_p

    library.osmand_widget_get_zoom.argtypes = [ctypes.c_void_p]
    library.osmand_widget_get_zoom.restype = ctypes.c_double
    library.osmand_widget_get_min_zoom.argtypes = [ctypes.c_void_p]
    library.osmand_widget_get_min_zoom.restype = ctypes.c_double
    library.osmand_widget_get_max_zoom.argtypes = [ctypes.c_void_p]
    library.osmand_widget_get_max_zoom.restype = ctypes.c_double
    library.osmand_widget_set_zoom.argtypes = [ctypes.c_void_p, ctypes.c_double]
    library.osmand_widget_set_zoom.restype = None
    library.osmand_widget_reset_view.argtypes = [ctypes.c_void_p]
    library.osmand_widget_reset_view.restype = None
    library.osmand_widget_pan_by_pixels.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_double]
    library.osmand_widget_pan_by_pixels.restype = None
    library.osmand_widget_set_center_lonlat.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_double]
    library.osmand_widget_set_center_lonlat.restype = None
    library.osmand_widget_get_center_lonlat.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
    ]
    library.osmand_widget_get_center_lonlat.restype = None
    library.osmand_widget_project_lonlat.argtypes = [
        ctypes.c_void_p,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
    ]
    library.osmand_widget_project_lonlat.restype = ctypes.c_int
    return _BridgeAPI(library=library)


def probe_native_widget_runtime(package_root: Path | None = None) -> tuple[bool, str | None]:
    root = (package_root or Path(__file__).resolve().parent.parent).resolve()
    cached = _NATIVE_WIDGET_RUNTIME_PROBE.get(root)
    if cached is not None:
        return cached

    library_path = resolve_osmand_native_widget_library(root)
    if library_path is None:
        result = (False, "The native OsmAnd widget DLL is not available")
    else:
        try:
            _load_bridge(library_path)
        except Exception as exc:  # pragma: no cover - exercised only on local runtimes
            result = (False, f"{type(exc).__name__}: {exc}")
        else:
            result = (True, None)

    _NATIVE_WIDGET_RUNTIME_PROBE[root] = result
    return result


def _lonlat_to_normalized(longitude: float, latitude: float) -> tuple[float, float]:
    latitude = max(min(float(latitude), MERCATOR_LAT_BOUND), -MERCATOR_LAT_BOUND)
    x = (float(longitude) + 180.0) / 360.0
    sin_lat = math.sin(math.radians(latitude))
    y = 0.5 - math.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * math.pi)
    return x, y


class NativeOsmAndWidget(QWidget):
    """Host a native C++ OsmAnd `QOpenGLWidget` inside a PySide6 widget tree."""

    viewChanged = Signal(float, float, float)
    panned = Signal(QPointF)
    panFinished = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        map_source: MapSourceSpec | None = None,
        tile_root: Path | str = "tiles",
        style_path: Path | str = "style.json",
    ) -> None:
        super().__init__(parent)
        del tile_root, style_path

        if map_source is None or map_source.kind != "osmand_obf":
            raise TileLoadingError("The native OsmAnd widget requires an OBF map source")

        package_root = Path(__file__).resolve().parent.parent
        self._map_source = map_source.resolved(package_root)
        library_path = resolve_osmand_native_widget_library(package_root)
        if library_path is None:
            raise TileLoadingError("The native OsmAnd widget DLL is not available")
        self._library_path = library_path.resolve()

        self._bridge = _load_bridge(library_path)
        error_buffer = ctypes.create_unicode_buffer(4096)
        parent_pointer = int(shiboken6.getCppPointer(self)[0])
        native_pointer = self._bridge.library.osmand_create_map_widget(
            ctypes.c_void_p(parent_pointer),
            str(self._map_source.data_path),
            str(self._map_source.resources_root or ""),
            str(self._map_source.style_path or ""),
            0,
            ctypes.cast(error_buffer, ctypes.c_void_p),
            len(error_buffer),
        )
        if not native_pointer:
            message = error_buffer.value or "Failed to create the native OsmAnd widget"
            import sys
            print(f"[NativeOsmAndWidget] osmand_create_map_widget failed: {message}", file=sys.stderr)
            raise TileLoadingError(message)

        self._native_pointer = ctypes.c_void_p(native_pointer)
        self._native_widget = shiboken6.wrapInstance(int(native_pointer), QWidget)
        self._native_widget.setObjectName("NativeOsmAndMapWidget")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._native_widget)
        self.setFocusProxy(self._native_widget)
        self.setMouseTracking(True)
        self.setMinimumSize(640, 480)
        self._native_widget.installEventFilter(self)
        self._bridge_dragging = False
        self._bridge_last_mouse_pos = QPointF()

        min_zoom = float(self._bridge.library.osmand_widget_get_min_zoom(self._native_pointer)) or 2.0
        max_zoom = float(self._bridge.library.osmand_widget_get_max_zoom(self._native_pointer)) or 19.0
        self._metadata = MapBackendMetadata(
            min_zoom=min_zoom,
            max_zoom=max(max_zoom, min_zoom),
            provides_place_labels=True,
            tile_kind="raster",
            tile_scheme="xyz",
        )

        self._last_view_state: tuple[float, float, float] | None = None
        self._state_timer = QTimer(self)
        self._state_timer.setInterval(120)
        self._state_timer.timeout.connect(self._poll_view_state)
        self._state_timer.start()
        self._deferred_view_sync_timer = QTimer(self)
        self._deferred_view_sync_timer.setSingleShot(True)
        self._deferred_view_sync_timer.setInterval(0)
        self._deferred_view_sync_timer.timeout.connect(self._emit_view_change)
        self._emit_view_change()

    @property
    def zoom(self) -> float:
        return float(self._bridge.library.osmand_widget_get_zoom(self._native_pointer))

    def set_zoom(self, zoom: float) -> None:
        self._bridge.library.osmand_widget_set_zoom(self._native_pointer, float(zoom))
        self._emit_view_change()

    def reset_view(self) -> None:
        self._bridge.library.osmand_widget_reset_view(self._native_pointer)
        self._emit_view_change()

    def pan_by_pixels(self, delta_x: float, delta_y: float) -> None:
        self._bridge.library.osmand_widget_pan_by_pixels(self._native_pointer, float(delta_x), float(delta_y))
        self.panned.emit(QPointF(float(delta_x), float(delta_y)))
        self._emit_view_change()

    def center_lonlat(self) -> tuple[float, float]:
        longitude = ctypes.c_double(0.0)
        latitude = ctypes.c_double(0.0)
        self._bridge.library.osmand_widget_get_center_lonlat(
            self._native_pointer,
            ctypes.byref(longitude),
            ctypes.byref(latitude),
        )
        return float(longitude.value), float(latitude.value)

    def center_on(self, lon: float, lat: float) -> None:
        self._bridge.library.osmand_widget_set_center_lonlat(self._native_pointer, float(lon), float(lat))
        self._emit_view_change()

    def focus_on(self, lon: float, lat: float, zoom_delta: float = 1.0) -> None:
        self.center_on(lon, lat)
        if zoom_delta:
            self.set_zoom(self.zoom + float(zoom_delta))

    def shutdown(self) -> None:
        if self._state_timer.isActive():
            self._state_timer.stop()
        if self._deferred_view_sync_timer.isActive():
            self._deferred_view_sync_timer.stop()

    def map_backend_metadata(self) -> MapBackendMetadata:
        return self._metadata

    def loaded_library_path(self) -> Path:
        return self._library_path

    def set_city_annotations(self, cities: Sequence[CityAnnotation]) -> None:
        del cities
        return None

    def city_at(self, position: QPointF) -> str | None:
        del position
        return None

    def event_target(self) -> QObject:
        return self._native_widget

    def prefers_exact_screen_projection(self) -> bool:
        return True

    def project_lonlat(self, lon: float, lat: float) -> QPointF | None:
        screen_x = ctypes.c_double(0.0)
        screen_y = ctypes.c_double(0.0)
        projected = self._bridge.library.osmand_widget_project_lonlat(
            self._native_pointer,
            float(lon),
            float(lat),
            ctypes.byref(screen_x),
            ctypes.byref(screen_y),
        )
        if projected:
            return QPointF(float(screen_x.value), float(screen_y.value))

        try:
            world_position = _lonlat_to_normalized(lon, lat)
        except (TypeError, ValueError):
            return None

        world_size = float(256.0 * (2.0 ** self.zoom))
        world_x = world_position[0] * world_size
        world_y = world_position[1] * world_size

        center_lon, center_lat = self.center_lonlat()
        center_x, center_y = _lonlat_to_normalized(center_lon, center_lat)
        center_px = center_x * world_size
        center_py = center_y * world_size
        delta_x = world_x - center_px
        if delta_x > world_size / 2.0:
            world_x -= world_size
        elif delta_x < -world_size / 2.0:
            world_x += world_size

        top_left_x = center_px - self.width() / 2.0
        top_left_y = center_py - self.height() / 2.0
        return QPointF(world_x - top_left_x, world_y - top_left_y)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if watched is self._native_widget:
            event_type = event.type()
            if event_type == QEvent.Type.MouseButtonPress:
                mouse_event = event
                if mouse_event.button() == Qt.MouseButton.LeftButton:
                    self._bridge_dragging = True
                    self._bridge_last_mouse_pos = mouse_event.position()
                    self._schedule_deferred_view_sync()
            elif event_type == QEvent.Type.MouseMove:
                mouse_event = event
                if self._bridge_dragging and mouse_event.buttons() & Qt.MouseButton.LeftButton:
                    current_pos = mouse_event.position()
                    delta = current_pos - self._bridge_last_mouse_pos
                    self._bridge_last_mouse_pos = current_pos
                    if not delta.isNull():
                        self.panned.emit(QPointF(delta))
                    self._schedule_deferred_view_sync()
            elif event_type == QEvent.Type.MouseButtonRelease:
                mouse_event = event
                if self._bridge_dragging and mouse_event.button() == Qt.MouseButton.LeftButton:
                    self._bridge_dragging = False
                    self.panFinished.emit()
                    self._schedule_deferred_view_sync()
            elif event_type in {QEvent.Type.Wheel, QEvent.Type.Resize}:
                self._schedule_deferred_view_sync()

        return super().eventFilter(watched, event)

    def _emit_view_change(self) -> None:
        center_x, center_y, zoom = self._read_view_state()
        self._last_view_state = (center_x, center_y, zoom)
        self.viewChanged.emit(center_x, center_y, zoom)

    def _poll_view_state(self) -> None:
        current_state = self._read_view_state()
        if self._last_view_state is None:
            self._last_view_state = current_state
            return
        if any(abs(current - previous) > 1e-6 for current, previous in zip(current_state, self._last_view_state)):
            self._last_view_state = current_state
            self.viewChanged.emit(*current_state)

    def _schedule_deferred_view_sync(self) -> None:
        if not self._deferred_view_sync_timer.isActive():
            self._deferred_view_sync_timer.start()

    def _read_view_state(self) -> tuple[float, float, float]:
        longitude, latitude = self.center_lonlat()
        center_x, center_y = _lonlat_to_normalized(longitude, latitude)
        return float(center_x), float(center_y), self.zoom


__all__ = ["NativeOsmAndWidget", "probe_native_widget_runtime"]
