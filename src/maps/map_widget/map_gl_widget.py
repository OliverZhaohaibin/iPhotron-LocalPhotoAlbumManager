"""QOpenGLWidget based implementation that enables GPU accelerated rendering."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Mapping, Sequence

from PySide6.QtCore import QPointF, QSize, QTimer, Signal, Qt
from PySide6.QtGui import (
    QCloseEvent,
    QHideEvent,
    QMouseEvent,
    QOpenGLContext,
    QPainter,
    QResizeEvent,
    QShowEvent,
    QSurfaceFormat,
    QWheelEvent,
)
from PySide6.QtOpenGL import QOpenGLWindow
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ._map_widget_base import MapWidgetController
from .map_renderer import CityAnnotation
from maps.map_sources import MapBackendMetadata, MapSourceSpec


_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_MAP_GL_CLEAR_COLOR = (0.533, 0.659, 0.761, 1.0)


def _map_gl_surface_format(*, platform: str | None = None) -> QSurfaceFormat:
    """Return the OpenGL surface format used by the map widget."""

    platform = sys.platform if platform is None else platform
    surface_format = QSurfaceFormat()
    surface_format.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    surface_format.setDepthBufferSize(24)
    surface_format.setStencilBufferSize(8)
    surface_format.setAlphaBufferSize(8 if platform == "darwin" else 0)
    surface_format.setSamples(0)
    return surface_format


def _map_gl_debug_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether one-shot GL diagnostics should be printed."""

    environ = os.environ if environ is None else environ
    return environ.get("IPHOTO_MAP_GL_DEBUG", "").strip().lower() in _TRUE_ENV_VALUES


def _map_gl_uses_no_partial_update(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether the GL map should redraw the full backing store."""

    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if platform not in {"darwin", "linux"}:
        return False
    partial_update = environ.get("IPHOTO_OSMAND_GL_PARTIAL_UPDATE", "").strip().lower()
    return partial_update not in _TRUE_ENV_VALUES


def _map_gl_uses_window_container(*, platform: str | None = None) -> bool:
    """Return whether the map should use a native GL window instead of a GL widget."""

    platform = sys.platform if platform is None else platform
    return platform == "darwin"


def _clear_current_opaque_backbuffer() -> None:
    """Clear the complete current GL surface to an opaque map background."""

    ctx = QOpenGLContext.currentContext()
    if ctx is None:
        return
    try:
        gl = ctx.functions()
    except Exception:
        return
    if gl is None:
        return

    had_scissor = False
    try:
        if hasattr(gl, "glIsEnabled"):
            had_scissor = bool(gl.glIsEnabled(MapGLWidget._GL_SCISSOR_TEST))
        if had_scissor and hasattr(gl, "glDisable"):
            gl.glDisable(MapGLWidget._GL_SCISSOR_TEST)
        if hasattr(gl, "glColorMask"):
            gl.glColorMask(True, True, True, True)
        gl.glClearColor(*_MAP_GL_CLEAR_COLOR)
        gl.glClear(MapGLWidget._GL_COLOR_BUFFER_BIT | MapGLWidget._GL_DEPTH_BUFFER_BIT)
    except Exception:
        return
    finally:
        if had_scissor and hasattr(gl, "glEnable"):
            try:
                gl.glEnable(MapGLWidget._GL_SCISSOR_TEST)
            except Exception:
                pass


class MapGLWidget(QOpenGLWidget):
    """Render the interactive preview using an OpenGL backed surface."""

    viewChanged = Signal(float, float, float)
    """Signal emitted whenever the map centre or zoom level changes."""

    panned = Signal(QPointF)
    """Signal emitted with raw drag deltas while the user drags the map."""

    panFinished = Signal()
    """Signal emitted once the current pan gesture completes."""

    _GL_COLOR_BUFFER_BIT = 0x00004000
    _GL_DEPTH_BUFFER_BIT = 0x00000100
    _GL_SCISSOR_TEST = 0x0C11

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        map_source: MapSourceSpec | None = None,
        tile_root: Path | str = "tiles",
        style_path: Path | str = "style.json",
    ) -> None:
        super().__init__(parent)
        if not self.objectName():
            self.setObjectName("MapGLWidget")

        self.setFormat(_map_gl_surface_format())

        if _map_gl_uses_no_partial_update():
            self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)
        else:
            self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.PartialUpdate)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, False)
        self.setAutoFillBackground(False)
        self.setStyleSheet("QWidget#MapGLWidget { background-color: #88a8c2; border: none; }")
        self._map_gl_debug = _map_gl_debug_enabled()
        self._logged_initialize_gl = False
        self._logged_paint_gl = False
        self._post_render_painters: list[Callable[[QPainter], None]] = []

        # ``MapWidgetController`` mirrors the logic used by the QWidget variant,
        # keeping rendering, tile loading, and input handling identical between
        # both front-ends while still giving this subclass full control over the
        # OpenGL specific surface lifecycle.
        self._controller = MapWidgetController(
            self,
            map_source=map_source,
            tile_root=tile_root,
            style_path=style_path,
        )
        self._controller.add_view_listener(self._emit_view_change)
        self._controller.add_pan_listener(self._emit_pan_delta)
        self._controller.add_pan_finished_listener(self._emit_pan_finished)

        # The OpenGL surface is now fully initialised, so QWidget-level helpers
        # such as mouse tracking and default sizing can be configured safely.
        self.setMouseTracking(True)
        self.setMinimumSize(640, 480)

    # ------------------------------------------------------------------
    @property
    def zoom(self) -> float:
        """Expose the current zoom level for the surrounding UI."""

        return self._controller.zoom

    # ------------------------------------------------------------------
    def set_zoom(self, zoom: float) -> None:
        """Forward zoom changes to the shared controller."""

        self._controller.set_zoom(zoom)

    # ------------------------------------------------------------------
    def reset_view(self) -> None:
        """Restore the default camera position and zoom level."""

        self._controller.reset_view()

    # ------------------------------------------------------------------
    def pan_by_pixels(self, delta_x: float, delta_y: float) -> None:
        """Translate the camera by a fixed on-screen pixel delta."""

        self._controller.pan_by_pixels(delta_x, delta_y)

    # ------------------------------------------------------------------
    def center_lonlat(self) -> tuple[float, float]:
        """Return the current viewport centre as ``(lon, lat)``."""

        return self._controller.center_lonlat()

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        """Stop background work before the widget is destroyed."""

        self._controller.shutdown()

    # ------------------------------------------------------------------
    def map_backend_metadata(self) -> MapBackendMetadata:
        """Expose the active map backend capabilities."""

        return self._controller.map_backend_metadata()

    # ------------------------------------------------------------------
    def project_lonlat(self, lon: float, lat: float) -> QPointF | None:
        """Return widget-relative coordinates for the provided GPS point."""

        return self._controller.project_lonlat(lon, lat)

    # ------------------------------------------------------------------
    def center_on(self, lon: float, lat: float) -> None:
        """Centre the viewport on *lon*/*lat*."""

        self._controller.center_on(lon, lat)

    # ------------------------------------------------------------------
    def focus_on(self, lon: float, lat: float, zoom_delta: float = 1.0) -> None:
        """Centre the viewport on *lon*/*lat* and zoom by *zoom_delta*."""

        self._controller.focus_on(lon, lat, zoom_delta)

    # ------------------------------------------------------------------
    def set_city_annotations(self, cities: Sequence[CityAnnotation]) -> None:
        """Forward the supplied city annotations to the shared controller."""

        self._controller.set_cities(cities)

    # ------------------------------------------------------------------
    def city_at(self, position: QPointF) -> str | None:
        """Return the full label text for the city under ``position`` if any."""

        return self._controller.city_at(position)

    # ------------------------------------------------------------------
    def event_target(self) -> QWidget:
        """Return the widget that directly receives pointer input events."""

        return self

    # ------------------------------------------------------------------
    def initializeGL(self) -> None:  # type: ignore[override]
        """Initialize the GL clear color to match the map background."""

        self._clear_opaque_backbuffer()
        self._log_gl_debug_once("initializeGL")

    # ------------------------------------------------------------------
    def paintGL(self) -> None:  # type: ignore[override]
        """Render the current frame inside the active OpenGL context."""

        self._clear_opaque_backbuffer()

        painter = QPainter()
        painter_started = painter.begin(self)
        self._log_gl_debug_once("paintGL", painter_started=painter_started)
        if not painter_started:
            # ``begin`` can theoretically fail when the underlying context is no
            # longer valid.  Returning early keeps Qt from raising confusing
            # low-level exceptions.
            return

        try:
            self._controller.render(painter)
            for callback in list(self._post_render_painters):
                try:
                    callback(painter)
                except Exception:
                    continue
        finally:
            painter.end()

    # ------------------------------------------------------------------
    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        """Propagate resize events and notify listeners about the new viewport."""

        super().resizeEvent(event)
        self.request_full_update()
        self._queue_macos_followup_update()
        self._emit_view_change(*self._controller.view_state())

    # ------------------------------------------------------------------
    def showEvent(self, event: QShowEvent) -> None:  # type: ignore[override]
        """Request a full repaint when the widget becomes visible."""

        super().showEvent(event)
        self.setUpdatesEnabled(True)
        self.request_full_update()
        self._queue_macos_followup_update()

    # ------------------------------------------------------------------
    def hideEvent(self, event: QHideEvent) -> None:  # type: ignore[override]
        """Pause repaint requests while the GL map surface is hidden."""

        if sys.platform != "darwin":
            self.setUpdatesEnabled(False)
        super().hideEvent(event)

    # ------------------------------------------------------------------
    def request_full_update(self) -> None:
        """Invalidate the entire widget rect even when partial updates are enabled."""

        super().update(self.rect())

    # ------------------------------------------------------------------
    def add_post_render_painter(self, callback: Callable[[QPainter], None]) -> None:
        """Draw extra content inside the same GL-backed painter pass."""

        if callback not in self._post_render_painters:
            self._post_render_painters.append(callback)
            self.request_full_update()

    # ------------------------------------------------------------------
    def remove_post_render_painter(self, callback: Callable[[QPainter], None]) -> None:
        """Stop drawing a previously registered post-render callback."""

        self._post_render_painters = [
            existing for existing in self._post_render_painters if existing != callback
        ]
        self.request_full_update()

    # ------------------------------------------------------------------
    def _queue_macos_followup_update(self) -> None:
        """Queue a second repaint after macOS has rebuilt the GL surface."""

        if sys.platform == "darwin":
            QTimer.singleShot(0, self.request_full_update)

    # ------------------------------------------------------------------
    def _clear_opaque_backbuffer(self) -> None:
        """Clear the complete GL backing store to an opaque map background."""

        _clear_current_opaque_backbuffer()

    # ------------------------------------------------------------------
    def _log_gl_debug_once(self, stage: str, *, painter_started: bool | None = None) -> None:
        """Print a single diagnostic line for GL context debugging when enabled."""

        if not self._map_gl_debug:
            return
        if stage == "initializeGL":
            if self._logged_initialize_gl:
                return
            self._logged_initialize_gl = True
        elif stage == "paintGL":
            if self._logged_paint_gl:
                return
            self._logged_paint_gl = True

        requested = self.format()
        ctx = QOpenGLContext.currentContext()
        actual = ctx.format() if ctx is not None else None

        def _describe_format(surface_format: QSurfaceFormat | None) -> str:
            if surface_format is None:
                return "none"
            return (
                f"alpha={surface_format.alphaBufferSize()} "
                f"depth={surface_format.depthBufferSize()} "
                f"stencil={surface_format.stencilBufferSize()} "
                f"samples={surface_format.samples()}"
            )

        try:
            update_behavior = self.updateBehavior()
        except Exception:
            update_behavior = "unknown"
        always_on_top = self.testAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)

        painter_status = ""
        if painter_started is not None:
            painter_status = f" painter_begin={painter_started}"

        print(
            "[MapGLWidget][debug] "
            f"stage={stage} "
            f"requested_format=({_describe_format(requested)}) "
            f"actual_format=({_describe_format(actual)}) "
            f"update_behavior={update_behavior} "
            f"always_stack_on_top={always_on_top}"
            f"{painter_status}",
            flush=True,
        )

    # ------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        """Ensure worker threads shut down before the OpenGL surface disappears."""

        self.shutdown()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        """Forward mouse press events to the shared interaction handler."""

        self._controller.handle_mouse_press(event)
        super().mousePressEvent(event)

    # ------------------------------------------------------------------
    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        """Forward mouse move events to the shared interaction handler."""

        self._controller.handle_mouse_move(event)
        super().mouseMoveEvent(event)

    # ------------------------------------------------------------------
    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        """Forward mouse release events to the shared interaction handler."""

        self._controller.handle_mouse_release(event)
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    def wheelEvent(self, event) -> None:  # type: ignore[override]
        """Forward wheel events to the shared interaction handler."""

        self._controller.handle_wheel_event(event)
        super().wheelEvent(event)

    # ------------------------------------------------------------------
    def _emit_view_change(self, center_x: float, center_y: float, zoom: float) -> None:
        """Forward controller updates via the Qt signal for external consumers."""

        self.viewChanged.emit(float(center_x), float(center_y), float(zoom))

    # ------------------------------------------------------------------
    def _emit_pan_delta(self, delta: QPointF) -> None:
        """Proxy incremental drag deltas to consumers on the Qt signal."""

        self.panned.emit(QPointF(delta))

    # ------------------------------------------------------------------
    def _emit_pan_finished(self) -> None:
        """Notify listeners that the current drag gesture has ended."""

        self.panFinished.emit()


class _MapOpenGLWindow(QOpenGLWindow):
    """Native OpenGL window used on macOS to avoid QOpenGLWidget FBO compositing."""

    def __init__(
        self,
        owner: "MapGLWindowWidget",
        *,
        map_source: MapSourceSpec | None = None,
        tile_root: Path | str = "tiles",
        style_path: Path | str = "style.json",
    ) -> None:
        update_behavior = (
            QOpenGLWindow.UpdateBehavior.NoPartialUpdate
            if _map_gl_uses_no_partial_update()
            else QOpenGLWindow.UpdateBehavior.PartialUpdate
        )
        super().__init__(update_behavior)
        self._owner = owner
        self.setFormat(_map_gl_surface_format())
        self.setTitle("MapGLWindow")
        self._map_gl_debug = _map_gl_debug_enabled()
        self._logged_initialize_gl = False
        self._logged_paint_gl = False
        self._post_render_painters: list[Callable[[QPainter], None]] = []

        self._controller = MapWidgetController(
            self,
            map_source=map_source,
            tile_root=tile_root,
            style_path=style_path,
        )
        self._controller.add_view_listener(owner._emit_view_change)
        self._controller.add_pan_listener(owner._emit_pan_delta)
        self._controller.add_pan_finished_listener(owner._emit_pan_finished)
        self.setMouseTracking(True)
        self.setMinimumSize(640, 480)

    # ------------------------------------------------------------------
    def setMouseTracking(self, enabled: bool) -> None:  # noqa: N802 - Qt compatibility shim
        """Accept the QWidget mouse-tracking API expected by the shared controller."""

        self._mouse_tracking_enabled = bool(enabled)

    # ------------------------------------------------------------------
    def setMinimumSize(self, width: int, height: int) -> None:  # noqa: N802 - Qt compatibility shim
        """Expose the QWidget-style two-argument minimum-size helper."""

        super().setMinimumSize(QSize(int(width), int(height)))

    # ------------------------------------------------------------------
    @property
    def zoom(self) -> float:
        return self._controller.zoom

    # ------------------------------------------------------------------
    def set_zoom(self, zoom: float) -> None:
        self._controller.set_zoom(zoom)

    # ------------------------------------------------------------------
    def reset_view(self) -> None:
        self._controller.reset_view()

    # ------------------------------------------------------------------
    def pan_by_pixels(self, delta_x: float, delta_y: float) -> None:
        self._controller.pan_by_pixels(delta_x, delta_y)

    # ------------------------------------------------------------------
    def center_lonlat(self) -> tuple[float, float]:
        return self._controller.center_lonlat()

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        self._controller.shutdown()

    # ------------------------------------------------------------------
    def map_backend_metadata(self) -> MapBackendMetadata:
        return self._controller.map_backend_metadata()

    # ------------------------------------------------------------------
    def project_lonlat(self, lon: float, lat: float) -> QPointF | None:
        return self._controller.project_lonlat(lon, lat)

    # ------------------------------------------------------------------
    def center_on(self, lon: float, lat: float) -> None:
        self._controller.center_on(lon, lat)

    # ------------------------------------------------------------------
    def focus_on(self, lon: float, lat: float, zoom_delta: float = 1.0) -> None:
        self._controller.focus_on(lon, lat, zoom_delta)

    # ------------------------------------------------------------------
    def set_city_annotations(self, cities: Sequence[CityAnnotation]) -> None:
        self._controller.set_cities(cities)

    # ------------------------------------------------------------------
    def city_at(self, position: QPointF) -> str | None:
        return self._controller.city_at(position)

    # ------------------------------------------------------------------
    def request_full_update(self) -> None:
        self.update()

    # ------------------------------------------------------------------
    def add_post_render_painter(self, callback: Callable[[QPainter], None]) -> None:
        if callback not in self._post_render_painters:
            self._post_render_painters.append(callback)
            self.request_full_update()

    # ------------------------------------------------------------------
    def remove_post_render_painter(self, callback: Callable[[QPainter], None]) -> None:
        self._post_render_painters = [
            existing for existing in self._post_render_painters if existing != callback
        ]
        self.request_full_update()

    # ------------------------------------------------------------------
    def initializeGL(self) -> None:  # type: ignore[override]
        _clear_current_opaque_backbuffer()
        self._log_gl_debug_once("initializeGL")

    # ------------------------------------------------------------------
    def resizeGL(self, width: int, height: int) -> None:  # type: ignore[override]
        del width, height
        self.request_full_update()
        self._owner._emit_view_change(*self._controller.view_state())

    # ------------------------------------------------------------------
    def paintGL(self) -> None:  # type: ignore[override]
        _clear_current_opaque_backbuffer()

        painter = QPainter()
        painter_started = painter.begin(self)
        self._log_gl_debug_once("paintGL", painter_started=painter_started)
        if not painter_started:
            return

        try:
            self._controller.render(painter)
            for callback in list(self._post_render_painters):
                try:
                    callback(painter)
                except Exception:
                    continue
        finally:
            painter.end()

    # ------------------------------------------------------------------
    def exposeEvent(self, event) -> None:  # type: ignore[override]
        super().exposeEvent(event)
        if self.isExposed():
            self.request_full_update()

    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._controller.handle_mouse_press(event)
        super().mousePressEvent(event)

    # ------------------------------------------------------------------
    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._controller.handle_mouse_move(event)
        super().mouseMoveEvent(event)

    # ------------------------------------------------------------------
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._controller.handle_mouse_release(event)
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        self._controller.handle_wheel_event(event)
        super().wheelEvent(event)

    # ------------------------------------------------------------------
    def _log_gl_debug_once(self, stage: str, *, painter_started: bool | None = None) -> None:
        if not self._map_gl_debug:
            return
        if stage == "initializeGL":
            if self._logged_initialize_gl:
                return
            self._logged_initialize_gl = True
        elif stage == "paintGL":
            if self._logged_paint_gl:
                return
            self._logged_paint_gl = True

        requested = self.format()
        ctx = QOpenGLContext.currentContext()
        actual = ctx.format() if ctx is not None else None

        def _describe_format(surface_format: QSurfaceFormat | None) -> str:
            if surface_format is None:
                return "none"
            return (
                f"alpha={surface_format.alphaBufferSize()} "
                f"depth={surface_format.depthBufferSize()} "
                f"stencil={surface_format.stencilBufferSize()} "
                f"samples={surface_format.samples()}"
            )

        try:
            update_behavior = self.updateBehavior()
        except Exception:
            update_behavior = "unknown"

        painter_status = ""
        if painter_started is not None:
            painter_status = f" painter_begin={painter_started}"

        print(
            "[MapGLWindow][debug] "
            f"stage={stage} "
            f"requested_format=({_describe_format(requested)}) "
            f"actual_format=({_describe_format(actual)}) "
            f"update_behavior={update_behavior}"
            f"{painter_status}",
            flush=True,
        )


class MapGLWindowWidget(QWidget):
    """QWidget wrapper around a native QOpenGLWindow for macOS map rendering."""

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
        if not self.objectName():
            self.setObjectName("MapGLWindowWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        self.setAutoFillBackground(True)
        self.setStyleSheet("QWidget#MapGLWindowWidget { background-color: #88a8c2; border: none; }")

        self._window = _MapOpenGLWindow(
            self,
            map_source=map_source,
            tile_root=tile_root,
            style_path=style_path,
        )
        self._container = QWidget.createWindowContainer(self._window, self)
        self._container.setObjectName("MapGLWindowContainer")
        self._container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._container.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        self._container.setAutoFillBackground(False)
        self._container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._container)
        self.setMinimumSize(640, 480)

    # ------------------------------------------------------------------
    @property
    def zoom(self) -> float:
        return self._window.zoom

    # ------------------------------------------------------------------
    def set_zoom(self, zoom: float) -> None:
        self._window.set_zoom(zoom)

    # ------------------------------------------------------------------
    def reset_view(self) -> None:
        self._window.reset_view()

    # ------------------------------------------------------------------
    def pan_by_pixels(self, delta_x: float, delta_y: float) -> None:
        self._window.pan_by_pixels(delta_x, delta_y)

    # ------------------------------------------------------------------
    def center_lonlat(self) -> tuple[float, float]:
        return self._window.center_lonlat()

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        self._window.shutdown()

    # ------------------------------------------------------------------
    def map_backend_metadata(self) -> MapBackendMetadata:
        return self._window.map_backend_metadata()

    # ------------------------------------------------------------------
    def project_lonlat(self, lon: float, lat: float) -> QPointF | None:
        return self._window.project_lonlat(lon, lat)

    # ------------------------------------------------------------------
    def center_on(self, lon: float, lat: float) -> None:
        self._window.center_on(lon, lat)

    # ------------------------------------------------------------------
    def focus_on(self, lon: float, lat: float, zoom_delta: float = 1.0) -> None:
        self._window.focus_on(lon, lat, zoom_delta)

    # ------------------------------------------------------------------
    def set_city_annotations(self, cities: Sequence[CityAnnotation]) -> None:
        self._window.set_city_annotations(cities)

    # ------------------------------------------------------------------
    def city_at(self, position: QPointF) -> str | None:
        return self._window.city_at(position)

    # ------------------------------------------------------------------
    def event_target(self) -> _MapOpenGLWindow:
        return self._window

    # ------------------------------------------------------------------
    def request_full_update(self) -> None:
        self._window.request_full_update()

    # ------------------------------------------------------------------
    def add_post_render_painter(self, callback: Callable[[QPainter], None]) -> None:
        self._window.add_post_render_painter(callback)

    # ------------------------------------------------------------------
    def remove_post_render_painter(self, callback: Callable[[QPainter], None]) -> None:
        self._window.remove_post_render_painter(callback)

    # ------------------------------------------------------------------
    def showEvent(self, event: QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.request_full_update()
        if sys.platform == "darwin":
            QTimer.singleShot(0, self.request_full_update)

    # ------------------------------------------------------------------
    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.request_full_update()

    # ------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    def _emit_view_change(self, center_x: float, center_y: float, zoom: float) -> None:
        self.viewChanged.emit(float(center_x), float(center_y), float(zoom))

    # ------------------------------------------------------------------
    def _emit_pan_delta(self, delta: QPointF) -> None:
        self.panned.emit(QPointF(delta))

    # ------------------------------------------------------------------
    def _emit_pan_finished(self) -> None:
        self.panFinished.emit()


__all__ = ["MapGLWidget", "MapGLWindowWidget"]
