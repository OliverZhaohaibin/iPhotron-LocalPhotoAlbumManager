"""PySide6 host widget for the native OsmAnd OpenGL map control."""

from __future__ import annotations

import ctypes
import logging
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import PySide6
import shiboken6
from PySide6.QtCore import QEvent, QObject, QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QHideEvent,
    QImage,
    QOpenGLContext,
    QPainter,
    QPalette,
    QShowEvent,
    QSurfaceFormat,
    QWindow,
)
from PySide6.QtOpenGL import QOpenGLWindow
from PySide6.QtWidgets import QApplication, QSizePolicy, QVBoxLayout, QWidget

from maps.map_sources import (
    MapBackendMetadata,
    MapSourceSpec,
    resolve_osmand_native_widget_library,
)
from maps.map_widget.map_renderer import CityAnnotation
from maps.tile_parser import TileLoadingError

MERCATOR_LAT_BOUND = 85.05112878
_NATIVE_DLL_DIR_HANDLES: list[Any] = []
_PRELOADED_QT_LIBRARIES: list[ctypes.CDLL] = []
_NATIVE_WIDGET_RUNTIME_PROBE: dict[Path, tuple[bool, str | None]] = {}
_LOGGER = logging.getLogger(__name__)
_MAP_OPAQUE_BACKGROUND = "#88a8c2"
_GL_COLOR_BUFFER_BIT = 0x00004000
_GL_DEPTH_BUFFER_BIT = 0x00000100
_GL_SCISSOR_TEST = 0x0C11


@dataclass(frozen=True)
class _BridgeAPI:
    library: ctypes.CDLL


def _render_marker_buffer(
    size: QSize,
    device_pixel_ratio: float,
    painters: Sequence[Callable[[QPainter], None]],
) -> QImage:
    device_pixel_ratio = max(float(device_pixel_ratio), 1.0)
    width = max(1, math.ceil(float(size.width()) * device_pixel_ratio))
    height = max(1, math.ceil(float(size.height()) * device_pixel_ratio))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(device_pixel_ratio)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter()
    if not painter.begin(image):
        return QImage()
    try:
        for callback in list(painters):
            try:
                callback(painter)
            except Exception:
                _LOGGER.warning("Native OsmAnd marker overlay painter failed", exc_info=True)
    finally:
        painter.end()
    return image


class _NativeMarkerOverlayWindow(QOpenGLWindow):
    """Transparent native GL window used to paint markers above a native map window."""

    def __init__(self) -> None:
        super().__init__(QOpenGLWindow.UpdateBehavior.NoPartialUpdate)
        surface_format = QSurfaceFormat()
        surface_format.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        surface_format.setAlphaBufferSize(8)
        surface_format.setDepthBufferSize(0)
        surface_format.setStencilBufferSize(0)
        surface_format.setSamples(0)
        self.setFormat(surface_format)
        self.setTitle("NativeOsmAndMarkerOverlayWindow")
        self.setObjectName("NativeOsmAndMarkerOverlayWindow")
        self.setMinimumSize(QSize(1, 1))
        try:
            self.setFlag(Qt.WindowType.WindowTransparentForInput, True)
        except Exception:
            pass
        self._painters: list[Callable[[QPainter], None]] = []

    def add_painter(self, callback: Callable[[QPainter], None]) -> None:
        if callback not in self._painters:
            self._painters.append(callback)
            self.update()

    def remove_painter(self, callback: Callable[[QPainter], None]) -> None:
        self._painters = [existing for existing in self._painters if existing != callback]
        self.update()

    def paintGL(self) -> None:  # type: ignore[override]
        self._clear_transparent_backbuffer()
        if not self._painters:
            return

        marker_buffer = self._render_marker_buffer()
        if marker_buffer.isNull():
            return

        painter = QPainter()
        if not painter.begin(self):
            return
        try:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.drawImage(QPointF(0.0, 0.0), marker_buffer)
        finally:
            painter.end()

    def exposeEvent(self, event) -> None:  # type: ignore[override]
        super().exposeEvent(event)
        if self.isExposed():
            self.update()

    def _clear_transparent_backbuffer(self) -> None:
        context = QOpenGLContext.currentContext()
        if context is None:
            return
        try:
            functions = context.functions()
        except Exception:
            return
        if functions is None:
            return

        had_scissor = False
        try:
            if hasattr(functions, "glIsEnabled"):
                had_scissor = bool(functions.glIsEnabled(_GL_SCISSOR_TEST))
            if had_scissor and hasattr(functions, "glDisable"):
                functions.glDisable(_GL_SCISSOR_TEST)
            if hasattr(functions, "glColorMask"):
                functions.glColorMask(True, True, True, True)
            functions.glClearColor(0.0, 0.0, 0.0, 0.0)
            functions.glClear(_GL_COLOR_BUFFER_BIT | _GL_DEPTH_BUFFER_BIT)
        except Exception:
            return
        finally:
            if had_scissor and hasattr(functions, "glEnable"):
                try:
                    functions.glEnable(_GL_SCISSOR_TEST)
                except Exception:
                    pass

    def _render_marker_buffer(self) -> QImage:
        return _render_marker_buffer(self.size(), float(self.devicePixelRatio()), self._painters)


def _configure_opaque_widget_background(widget: QWidget) -> None:
    """Give native map hosts a stable opaque backing colour."""

    if not widget.objectName():
        widget.setObjectName(type(widget).__name__)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
    widget.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, False)
    widget.setAutoFillBackground(True)
    palette = QPalette(widget.palette())
    palette.setColor(QPalette.ColorRole.Window, QColor(_MAP_OPAQUE_BACKGROUND))
    widget.setPalette(palette)
    widget.setStyleSheet(
        f"QWidget#{widget.objectName()} {{ background-color: {_MAP_OPAQUE_BACKGROUND}; border: none; }}"
    )


def _startup_profile_enabled() -> bool:
    return os.environ.get("IPHOTO_OSMAND_PROFILE_STARTUP", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _log_startup_profile(stage: str, elapsed_ms: float, **details: object) -> None:
    if not _startup_profile_enabled():
        return

    suffix = ""
    if details:
        parts = [f"{key}={value}" for key, value in details.items()]
        suffix = " " + " ".join(parts)
    _LOGGER.info("[native_osmand_widget][startup] %s %.1fms%s", stage, elapsed_ms, suffix)


def _ensure_dll_directory(path: Path) -> None:
    if os.name == "nt" and hasattr(os, "add_dll_directory") and path.exists():
        _NATIVE_DLL_DIR_HANDLES.append(os.add_dll_directory(str(path)))


def _prepare_library_load(library_path: Path) -> None:
    pyside_root = Path(PySide6.__file__).resolve().parent
    shiboken_root = Path(shiboken6.__file__).resolve().parent
    if os.name == "nt":
        # Register all directories that contain transitive DLL dependencies
        # before calling WinDLL. The extension layout keeps the widget binary
        # and its runtime siblings together in ``extension/bin``.
        _ensure_dll_directory(pyside_root)
        _ensure_dll_directory(shiboken_root)
        _ensure_dll_directory(library_path.parent)
        return

    qt_lib_dir = (pyside_root / "Qt" / "lib").resolve()
    runtime_dirs = [library_path.parent.resolve()]
    if qt_lib_dir.is_dir():
        runtime_dirs.insert(0, qt_lib_dir)

    for candidate_dir in runtime_dirs:
        lib_dir = str(candidate_dir)
        ld_path = os.environ.get("LD_LIBRARY_PATH", "")
        if lib_dir not in ld_path.split(os.pathsep):
            os.environ["LD_LIBRARY_PATH"] = lib_dir + (os.pathsep + ld_path if ld_path else "")

    if sys.platform == "darwin":
        for candidate_dir in runtime_dirs:
            lib_dir = str(candidate_dir)
            dy_path = os.environ.get("DYLD_LIBRARY_PATH", "")
            if lib_dir not in dy_path.split(os.pathsep):
                os.environ["DYLD_LIBRARY_PATH"] = lib_dir + (os.pathsep + dy_path if dy_path else "")
    elif sys.platform.startswith("linux") and qt_lib_dir.is_dir():
        preload_mode = getattr(ctypes, "RTLD_GLOBAL", 0)
        for library_name in [
            "libQt6Core.so.6",
            "libQt6Gui.so.6",
            "libQt6Widgets.so.6",
            "libQt6Network.so.6",
            "libQt6OpenGL.so.6",
            "libQt6OpenGLWidgets.so.6",
        ]:
            candidate = qt_lib_dir / library_name
            if candidate.exists():
                _PRELOADED_QT_LIBRARIES.append(ctypes.CDLL(str(candidate), mode=preload_mode))


def _load_bridge(library_path: Path) -> _BridgeAPI:
    _prepare_library_load(library_path)

    load_started = time.perf_counter()
    library = ctypes.WinDLL(str(library_path)) if os.name == "nt" else ctypes.CDLL(str(library_path))
    _log_startup_profile(
        "load_bridge",
        (time.perf_counter() - load_started) * 1000.0,
        library=library_path,
    )
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
    get_event_target = getattr(library, "osmand_widget_get_event_target", None)
    if get_event_target is not None:
        get_event_target.argtypes = [ctypes.c_void_p]
        get_event_target.restype = ctypes.c_void_p

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
    cleanup = getattr(library, "osmand_widget_cleanup", None)
    if cleanup is not None:
        cleanup.argtypes = [ctypes.c_void_p]
        cleanup.restype = None
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


def _has_qapplication_instance() -> bool:
    try:
        return QApplication.instance() is not None
    except Exception:
        return False


def probe_native_widget_runtime(package_root: Path | None = None) -> tuple[bool, str | None]:
    root = (package_root or Path(__file__).resolve().parent.parent).resolve()
    cached = _NATIVE_WIDGET_RUNTIME_PROBE.get(root)
    if cached is not None:
        return cached

    library_path = resolve_osmand_native_widget_library(root)
    if library_path is None:
        result = (False, "The native OsmAnd widget library is not available")
    elif sys.platform == "darwin" and not _has_qapplication_instance():
        return (
            False,
            "QApplication must be constructed before probing the native OsmAnd widget on macOS",
        )
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
        _configure_opaque_widget_background(self)

        if map_source is None or map_source.kind != "osmand_obf":
            raise TileLoadingError("The native OsmAnd widget requires an OBF map source")

        package_root = Path(__file__).resolve().parent.parent
        self._map_source = map_source.resolved(package_root)
        library_path = resolve_osmand_native_widget_library(package_root)
        if library_path is None:
            raise TileLoadingError("The native OsmAnd widget library is not available")
        self._library_path = library_path.resolve()
        self._shutdown = False

        create_started = time.perf_counter()
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
            print(f"[NativeOsmAndWidget] osmand_create_map_widget failed: {message}", file=sys.stderr)
            raise TileLoadingError(message)
        _log_startup_profile(
            "create_widget_bridge",
            (time.perf_counter() - create_started) * 1000.0,
            source=self._map_source.data_path,
        )

        self._native_pointer = ctypes.c_void_p(native_pointer)
        self._native_widget = shiboken6.wrapInstance(int(native_pointer), QWidget)
        self._native_widget.setObjectName("NativeOsmAndMapWidget")
        self._native_event_target = self._native_widget
        get_event_target = getattr(self._bridge.library, "osmand_widget_get_event_target", None)
        if get_event_target is not None:
            event_target_pointer = get_event_target(self._native_pointer)
            if event_target_pointer and int(event_target_pointer) != int(native_pointer):
                self._native_event_target = shiboken6.wrapInstance(int(event_target_pointer), QWindow)
                if not self._native_event_target.objectName():
                    self._native_event_target.setObjectName("NativeOsmAndMapEventTarget")
        self._native_widget.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._native_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._native_widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        self._native_widget.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, False)
        self._native_widget.setAutoFillBackground(False)
        self._native_widget.setMinimumSize(0, 0)
        self._native_widget.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Ignored,
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._native_widget)
        self._layout = layout
        self.setFocusProxy(self._native_widget)
        self.setMouseTracking(True)
        self.setMinimumSize(640, 480)
        self._native_event_target.installEventFilter(self)
        self._bridge_dragging = False
        self._bridge_last_mouse_pos = QPointF()
        self._overlay_window: _NativeMarkerOverlayWindow | None = None
        self._overlay_container: QWidget | None = None

        min_zoom = float(self._bridge.library.osmand_widget_get_min_zoom(self._native_pointer)) or 2.0
        max_zoom = float(self._bridge.library.osmand_widget_get_max_zoom(self._native_pointer)) or 19.0
        self._metadata = MapBackendMetadata(
            min_zoom=min_zoom,
            max_zoom=max(max_zoom, min_zoom),
            provides_place_labels=True,
            tile_kind="raster",
            tile_scheme="xyz",
            fetch_max_zoom=max(0, int(max(max_zoom, min_zoom))),
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
        self._sync_native_widget_geometry()
        self._emit_view_change()

    @property
    def zoom(self) -> float:
        if self._is_shutdown():
            return float(self._metadata.min_zoom) if hasattr(self, "_metadata") else 0.0
        return float(self._bridge.library.osmand_widget_get_zoom(self._native_pointer))

    def set_zoom(self, zoom: float) -> None:
        if self._is_shutdown():
            return
        self._bridge.library.osmand_widget_set_zoom(self._native_pointer, float(zoom))
        self._emit_view_change()

    def reset_view(self) -> None:
        if self._is_shutdown():
            return
        self._bridge.library.osmand_widget_reset_view(self._native_pointer)
        self._emit_view_change()

    def pan_by_pixels(self, delta_x: float, delta_y: float) -> None:
        if self._is_shutdown():
            return
        self._bridge.library.osmand_widget_pan_by_pixels(self._native_pointer, float(delta_x), float(delta_y))
        self.panned.emit(QPointF(float(delta_x), float(delta_y)))
        self._emit_view_change()

    def center_lonlat(self) -> tuple[float, float]:
        if self._is_shutdown():
            return (0.0, 0.0)
        longitude = ctypes.c_double(0.0)
        latitude = ctypes.c_double(0.0)
        self._bridge.library.osmand_widget_get_center_lonlat(
            self._native_pointer,
            ctypes.byref(longitude),
            ctypes.byref(latitude),
        )
        return float(longitude.value), float(latitude.value)

    def center_on(self, lon: float, lat: float) -> None:
        if self._is_shutdown():
            return
        self._bridge.library.osmand_widget_set_center_lonlat(self._native_pointer, float(lon), float(lat))
        self._emit_view_change()

    def focus_on(self, lon: float, lat: float, zoom_delta: float = 1.0) -> None:
        self.center_on(lon, lat)
        if zoom_delta:
            self.set_zoom(self.zoom + float(zoom_delta))

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        if self._state_timer.isActive():
            self._state_timer.stop()
        if self._deferred_view_sync_timer.isActive():
            self._deferred_view_sync_timer.stop()
        self._bridge_dragging = False
        if hasattr(self, "_native_widget") and self._native_widget is not None:
            try:
                self._native_event_target.removeEventFilter(self)
            except RuntimeError:
                pass
            self._native_widget.setUpdatesEnabled(False)
        self.setUpdatesEnabled(False)
        cleanup = getattr(self._bridge.library, "osmand_widget_cleanup", None)
        if cleanup is not None and bool(self._native_pointer):
            try:
                cleanup(self._native_pointer)
            except Exception:
                _LOGGER.warning("Native OsmAnd renderer cleanup failed", exc_info=True)

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
        return self._native_event_target

    def supports_post_render_painter(self) -> bool:
        if os.environ.get("QT_QPA_PLATFORM", "").strip().lower() == "offscreen":
            return False
        return isinstance(self._native_event_target, QWindow)

    def add_post_render_painter(self, callback: Callable[[QPainter], None]) -> None:
        if not self.supports_post_render_painter():
            return
        overlay = self._ensure_marker_overlay()
        overlay.add_painter(callback)

    def remove_post_render_painter(self, callback: Callable[[QPainter], None]) -> None:
        if self._overlay_window is not None:
            self._overlay_window.remove_painter(callback)

    def request_full_update(self) -> None:
        if self._overlay_window is not None:
            self._overlay_window.update()
        self._native_widget.update()

    def prefers_exact_screen_projection(self) -> bool:
        return True

    def project_lonlat(self, lon: float, lat: float) -> QPointF | None:
        if self._is_shutdown():
            return None
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
        if self._is_shutdown():
            return super().eventFilter(watched, event)
        if watched is self._native_event_target or watched is self._native_widget:
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

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._is_shutdown():
            return
        self._sync_native_widget_geometry()
        self._sync_marker_overlay_geometry()
        self._schedule_deferred_view_sync()

    def showEvent(self, event: QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._is_shutdown():
            return
        self.setUpdatesEnabled(True)
        self._native_widget.setUpdatesEnabled(True)
        if not self._state_timer.isActive():
            self._state_timer.start()
        self._sync_native_widget_geometry()
        self._sync_marker_overlay_geometry()
        self._schedule_deferred_view_sync()

    def hideEvent(self, event: QHideEvent) -> None:  # type: ignore[override]
        if not self._is_shutdown():
            if self._state_timer.isActive():
                self._state_timer.stop()
            if self._deferred_view_sync_timer.isActive():
                self._deferred_view_sync_timer.stop()
            self._native_widget.setUpdatesEnabled(False)
            if self._overlay_container is not None:
                self._overlay_container.hide()
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)

    def _emit_view_change(self) -> None:
        if self._is_shutdown():
            return
        center_x, center_y, zoom = self._read_view_state()
        self._last_view_state = (center_x, center_y, zoom)
        self.viewChanged.emit(center_x, center_y, zoom)

    def _poll_view_state(self) -> None:
        if self._is_shutdown():
            return
        current_state = self._read_view_state()
        if self._last_view_state is None:
            self._last_view_state = current_state
            return
        if any(abs(current - previous) > 1e-6 for current, previous in zip(current_state, self._last_view_state)):
            self._last_view_state = current_state
            self.viewChanged.emit(*current_state)

    def _schedule_deferred_view_sync(self) -> None:
        if self._is_shutdown():
            return
        if not self._deferred_view_sync_timer.isActive():
            self._deferred_view_sync_timer.start()

    def _sync_native_widget_geometry(self) -> None:
        if self._is_shutdown():
            return
        target_rect = self.contentsRect()
        if target_rect.isEmpty():
            return
        if self._native_widget.minimumWidth() != 0 or self._native_widget.minimumHeight() != 0:
            self._native_widget.setMinimumSize(0, 0)
        if self._native_widget.geometry() != target_rect:
            self._native_widget.setGeometry(target_rect)

    def _ensure_marker_overlay(self) -> _NativeMarkerOverlayWindow:
        if self._overlay_window is None:
            self._overlay_window = _NativeMarkerOverlayWindow()
            self._overlay_container = QWidget.createWindowContainer(self._overlay_window, self)
            self._overlay_container.setObjectName("NativeOsmAndMarkerOverlayContainer")
            self._overlay_container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._overlay_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self._overlay_container.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
            self._overlay_container.setAutoFillBackground(False)
            self._overlay_container.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._sync_marker_overlay_geometry()
        return self._overlay_window

    def _sync_marker_overlay_geometry(self) -> None:
        if self._overlay_container is None:
            return
        target_rect = self.contentsRect()
        if target_rect.isEmpty():
            return
        if self._overlay_container.geometry() != target_rect:
            self._overlay_container.setGeometry(target_rect)
        self._overlay_container.show()
        self._overlay_container.raise_()
        if self._overlay_window is not None:
            self._overlay_window.update()

    def _read_view_state(self) -> tuple[float, float, float]:
        if self._is_shutdown():
            return (0.5, 0.5, self.zoom)
        longitude, latitude = self.center_lonlat()
        center_x, center_y = _lonlat_to_normalized(longitude, latitude)
        return float(center_x), float(center_y), self.zoom

    def _is_shutdown(self) -> bool:
        return bool(getattr(self, "_shutdown", False)) or not bool(getattr(self, "_native_pointer", None))


__all__ = ["NativeOsmAndWidget", "probe_native_widget_runtime"]
