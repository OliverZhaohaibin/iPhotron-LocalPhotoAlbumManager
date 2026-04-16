"""Entry point for the PySide6-based map preview application."""

from __future__ import annotations

if __package__ in {None, ""}:  # pragma: no cover - direct script bootstrap
    import sys
    from pathlib import Path

    _SRC_ROOT = Path(__file__).resolve().parents[1]
    _src_root_str = str(_SRC_ROOT)
    if _src_root_str in sys.path:
        sys.path.remove(_src_root_str)
    sys.path.insert(0, _src_root_str)

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QOffscreenSurface, QOpenGLContext, QSurfaceFormat
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox

from iPhoto.bootstrap.qt_shader_cache import configure_shader_cache_environment
from maps.map_sources import (
    MapBackendMetadata,
    MapSourceSpec,
    has_usable_osmand_default,
    has_usable_osmand_native_widget,
    prefer_osmand_native_widget,
)
from maps.map_widget import MapGLWidget, MapWidget, NativeOsmAndWidget
from maps.map_widget.native_osmand_widget import probe_native_widget_runtime
from maps.map_widget._map_widget_base import MapWidgetBase
from maps.style_resolver import StyleLoadError
from maps.tile_backend import OsmAndRasterBackend
from maps.tile_parser import TileLoadingError

_PYTHON_OBF_RUNTIME_PROBE: dict[Path, tuple[bool, str | None]] = {}


@dataclass(frozen=True)
class PreviewLaunchConfig:
    """Describe the backend setup requested for the standalone preview."""

    map_source: MapSourceSpec
    widget_class: type[MapWidgetBase]
    native_widget_class: type[MapWidgetBase] | None
    startup_message: str


def _configure_qt_shader_disk_cache() -> None:
    """Route shader/program disk caches into a managed ``.iPhoto`` directory."""

    configure_shader_cache_environment()


def check_opengl_support() -> bool:
    """Return ``True`` when the system can create a basic OpenGL context."""

    if os.environ.get("IPHOTO_DISABLE_OPENGL", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False

    try:
        surface = QOffscreenSurface()
        surface.create()

        context = QOpenGLContext()
        if not context.create():
            return False

        if hasattr(context, "isValid") and not context.isValid():
            return False

        # ``QOffscreenSurface.makeCurrent()`` is a useful warm-up when it works,
        # but some drivers reject offscreen binding even though ``QOpenGLWidget``
        # itself renders correctly. Treat a valid context as sufficient and use
        # ``makeCurrent`` only as an optional best-effort probe.
        if surface.isValid() and context.makeCurrent(surface):
            context.doneCurrent()
        return True
    except Exception:
        return False


def choose_default_map_source(
    package_root: Path,
    *,
    use_opengl: bool = True,
    native_widget_runtime_available: bool | None = None,
) -> MapSourceSpec:
    """Return the best startup source for the standalone preview window."""

    prefer_native_widget = use_opengl and prefer_osmand_native_widget()

    if has_usable_osmand_default(package_root):
        return MapSourceSpec.osmand_default(package_root)

    if prefer_native_widget and has_usable_osmand_native_widget(package_root):
        is_available = native_widget_runtime_available
        if is_available is None:
            is_available, _ = probe_native_widget_runtime(package_root)
        if is_available:
            return MapSourceSpec.osmand_default(package_root)

    return MapSourceSpec.legacy_default(package_root)


def choose_native_widget_class(
    package_root: Path,
    *,
    use_opengl: bool,
    prefer_native_widget: bool = True,
) -> tuple[type[MapWidgetBase] | None, str]:
    if not use_opengl:
        return None, "OpenGL support unavailable. Falling back to CPU rendering."

    if not prefer_native_widget:
        return None, "OpenGL support detected. Using the same GPU accelerated Python renderer as the Location section."

    if not prefer_osmand_native_widget():
        return None, "OpenGL support detected. Native widget disabled by configuration; using the Python OBF renderer."

    if not has_usable_osmand_native_widget(package_root):
        return None, "OpenGL support detected. Using GPU accelerated Python rendering."

    is_available, reason = probe_native_widget_runtime(package_root)
    if is_available:
        return NativeOsmAndWidget, "OpenGL support detected. Using the native OsmAnd widget when OBF data is selected."

    detail = f" Native widget disabled: {reason}." if reason else ""
    return None, f"OpenGL support detected.{detail} Using GPU accelerated Python rendering."


def prepare_qt_runtime_for_backend(backend: str, package_root: Path | None = None) -> None:
    """Apply Linux Qt startup flags needed by the native OsmAnd widget.

    The XCB/GLX flags are only applied when the native OsmAnd widget is actually
    going to be used.  Unconditionally forcing ``QT_QPA_PLATFORM=xcb`` for every
    non-Python backend would break Wayland-only environments or systems without
    XCB even when no native widget is selected.
    """

    normalized_backend = backend.strip().lower()
    if sys.platform != "linux":
        return

    # The "python" and "legacy" backends never use the native OsmAnd widget, so
    # no XCB/GLX flags are required.
    if normalized_backend in {"python", "legacy"}:
        return

    if normalized_backend == "auto":
        # For "auto" mode, only set XCB flags if the native widget is both
        # configured and present.  This avoids forcing XCB on Wayland-only
        # systems when the native widget is not actually available.
        root = (package_root or Path(__file__).resolve().parent).resolve()
        if not prefer_osmand_native_widget() or not has_usable_osmand_native_widget(root):
            return

    # "native" backend, or "auto" with native widget available: apply XCB flags.
    if not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    if os.environ.get("QT_QPA_PLATFORM") == "xcb":
        os.environ.setdefault("QT_OPENGL", "desktop")
        os.environ.setdefault("QT_XCB_GL_INTEGRATION", "xcb_glx")


def probe_python_obf_runtime(package_root: Path | None = None) -> tuple[bool, str | None]:
    """Return whether the bundled Python OBF helper can initialize quickly."""

    root = (package_root or Path(__file__).resolve().parent).resolve()
    cached = _PYTHON_OBF_RUNTIME_PROBE.get(root)
    if cached is not None:
        return cached

    if not has_usable_osmand_default(root):
        result = (False, "The OsmAnd helper backend is unavailable")
    else:
        backend = OsmAndRasterBackend(MapSourceSpec.osmand_default(root).resolved(root))
        try:
            backend.probe_runtime()
        except Exception as exc:  # pragma: no cover - exercised only on local runtimes
            result = (False, f"{type(exc).__name__}: {exc}")
        else:
            result = (True, None)
        finally:
            backend.shutdown()

    _PYTHON_OBF_RUNTIME_PROBE[root] = result
    return result


def build_argument_parser() -> argparse.ArgumentParser:
    """Return the CLI parser used by the standalone preview entry point."""

    parser = argparse.ArgumentParser(description="Preview OsmAnd or legacy map backends")
    parser.add_argument(
        "--backend",
        choices=("auto", "native", "python", "legacy"),
        default="auto",
        help="Select the startup renderer explicitly instead of auto-detecting it.",
    )
    parser.add_argument(
        "--center",
        nargs=2,
        metavar=("LON", "LAT"),
        type=float,
        help="Center the initial view on the provided longitude/latitude pair.",
    )
    parser.add_argument(
        "--zoom",
        type=float,
        help="Set the initial zoom level after the window has been created.",
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        help="Save a screenshot after startup and exit once the image is written.",
    )
    parser.add_argument(
        "--capture-delay-ms",
        type=int,
        default=1500,
        help="How long to wait before taking --screenshot (default: 1500).",
    )
    return parser


def configure_qt_opengl_defaults() -> None:
    """Prefer desktop OpenGL for the standalone preview before app creation."""

    _configure_qt_shader_disk_cache()

    if os.environ.get("IPHOTO_DISABLE_OPENGL", "").strip().lower() in {"1", "true", "yes", "on"}:
        return

    try:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseDesktopOpenGL, True)
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    except Exception:
        return

    try:
        surface_format = QSurfaceFormat()
        surface_format.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        QSurfaceFormat.setDefaultFormat(surface_format)
    except Exception:
        return


def choose_launch_configuration(
    package_root: Path,
    *,
    use_opengl: bool,
    backend: str,
) -> PreviewLaunchConfig:
    """Resolve the startup backend requested on the command line."""

    widget_cls: type[MapWidgetBase] = MapGLWidget if use_opengl else MapWidget
    normalized_backend = backend.strip().lower()
    renderer_label = "GPU accelerated" if use_opengl else "CPU"

    if normalized_backend == "auto":
        prefer_native_widget = use_opengl and prefer_osmand_native_widget()
        if prefer_native_widget and has_usable_osmand_native_widget(package_root):
            is_available, reason = probe_native_widget_runtime(package_root)
            if is_available:
                return PreviewLaunchConfig(
                    map_source=MapSourceSpec.osmand_default(package_root),
                    widget_class=widget_cls,
                    native_widget_class=NativeOsmAndWidget,
                    startup_message="OpenGL support detected. Using the native OsmAnd widget.",
                )
            native_detail = f" Native widget unavailable: {reason}." if reason else ""
        elif use_opengl and not prefer_native_widget:
            native_detail = " Native widget disabled by configuration."
        else:
            native_detail = ""

        helper_runtime_available = False
        helper_reason: str | None = None
        if has_usable_osmand_default(package_root):
            helper_runtime_available, helper_reason = probe_python_obf_runtime(package_root)
        if helper_runtime_available:
            return PreviewLaunchConfig(
                map_source=MapSourceSpec.osmand_default(package_root),
                widget_class=widget_cls,
                native_widget_class=None,
                startup_message=f"Using the {renderer_label} Python OBF renderer.{native_detail}",
            )

        detail = f" OBF helper unavailable: {helper_reason}." if helper_reason else ""
        return PreviewLaunchConfig(
            map_source=MapSourceSpec.legacy_default(package_root),
            widget_class=widget_cls,
            native_widget_class=None,
            startup_message=f"Using the {renderer_label} legacy vector renderer.{native_detail}{detail}",
        )

    if normalized_backend == "native":
        if not use_opengl:
            raise TileLoadingError("OpenGL support is unavailable, so the native OsmAnd widget can not be forced")
        if not has_usable_osmand_native_widget(package_root):
            raise TileLoadingError("The native OsmAnd widget library is not available")
        is_available, reason = probe_native_widget_runtime(package_root)
        if not is_available:
            detail = f": {reason}" if reason else ""
            raise TileLoadingError(f"The native OsmAnd widget failed its runtime probe{detail}")
        return PreviewLaunchConfig(
            map_source=MapSourceSpec.osmand_default(package_root),
            widget_class=widget_cls,
            native_widget_class=NativeOsmAndWidget,
            startup_message="OpenGL support detected. Forcing the native OsmAnd widget.",
        )

    if normalized_backend == "python":
        if not has_usable_osmand_default(package_root):
            raise TileLoadingError("The OsmAnd helper backend is unavailable, so the Python OBF renderer can not be forced")
        is_available, reason = probe_python_obf_runtime(package_root)
        if not is_available:
            detail = f": {reason}" if reason else ""
            raise TileLoadingError(f"The Python OBF renderer failed its runtime probe{detail}")
        return PreviewLaunchConfig(
            map_source=MapSourceSpec.osmand_default(package_root),
            widget_class=widget_cls,
            native_widget_class=None,
            startup_message=f"Forcing the {renderer_label} Python OBF renderer.",
        )

    if normalized_backend == "legacy":
        return PreviewLaunchConfig(
            map_source=MapSourceSpec.legacy_default(package_root),
            widget_class=widget_cls,
            native_widget_class=None,
            startup_message=f"Forcing the {renderer_label} legacy vector renderer.",
        )

    raise ValueError(f"unsupported backend mode: {backend}")


def _backend_kind_for_widget(
    map_widget: MapWidgetBase,
    *,
    map_source: MapSourceSpec,
) -> str:
    if isinstance(map_widget, NativeOsmAndWidget):
        return "osmand_native"
    if map_source.kind == "osmand_obf":
        return "osmand_python"
    return "legacy_python"


def _confirmed_gl_state(
    map_widget: MapWidgetBase,
    *,
    backend_kind: str,
) -> str:
    if backend_kind == "osmand_native":
        return "true"
    if isinstance(map_widget, MapGLWidget):
        return "true"
    if isinstance(map_widget, MapWidget):
        return "false"
    return "unknown"


def format_map_runtime_diagnostics(
    map_widget: MapWidgetBase,
    *,
    map_source: MapSourceSpec,
) -> str:
    """Return a one-line runtime summary that proves whether GL is active."""

    backend_kind = _backend_kind_for_widget(map_widget, map_source=map_source)
    metadata = map_widget.map_backend_metadata()
    event_target = map_widget.event_target()
    event_target_name = getattr(event_target, "objectName", lambda: "")()
    if not event_target_name:
        event_target_name = type(event_target).__name__
    native_library_path = getattr(map_widget, "loaded_library_path", lambda: None)()
    native_library_suffix = ""
    if native_library_path:
        native_library_suffix = f" native_library={native_library_path}"

    return (
        "[maps.main] "
        f"backend={backend_kind} "
        f"confirmed_gl={_confirmed_gl_state(map_widget, backend_kind=backend_kind)} "
        f"widget={type(map_widget).__name__} "
        f"event_target={event_target_name} "
        f"source={map_source.kind} "
        f"tile_kind={metadata.tile_kind} "
        f"tile_scheme={metadata.tile_scheme}"
        f"{native_library_suffix}"
    )


def describe_active_backend(
    requested_source: MapSourceSpec,
    metadata: MapBackendMetadata,
) -> str:
    """Return a short human-readable label for the active runtime backend."""

    if requested_source.kind == "osmand_obf":
        if metadata.tile_kind == "raster":
            return "OBF Raster"
        return "Legacy Vector Fallback"
    return "Legacy Vector"


def format_status_message(
    requested_source: MapSourceSpec,
    metadata: MapBackendMetadata,
    *,
    zoom: float,
    longitude: float,
    latitude: float,
) -> str:
    """Summarize the current map state for the status bar."""

    backend_label = describe_active_backend(requested_source, metadata)
    source_path = Path(requested_source.data_path).name
    return (
        f"{backend_label} | Zoom {zoom:.2f} | Center {latitude:.4f}, {longitude:.4f}"
        f" | Source {source_path}"
    )


class MainWindow(QMainWindow):
    """Primary application window that hosts an interactive map widget."""

    PAN_FRACTION = 0.18

    def __init__(
        self,
        tile_root: str = "tiles",
        style_path: str = "style.json",
        *,
        map_source: MapSourceSpec | None = None,
        widget_class: type[MapWidgetBase] | None = None,
        native_widget_class: type[MapWidgetBase] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Map Preview")
        self.resize(1280, 860)

        self._package_root = Path(__file__).resolve().parent
        self._tile_root = tile_root
        self._style_path = style_path
        self._widget_cls: type[MapWidgetBase] = widget_class or MapWidget
        self._native_widget_cls = native_widget_class
        self._runtime_diagnostics = ""
        chosen_source = map_source or choose_default_map_source(
            self._package_root,
            use_opengl=self._native_widget_cls is not None or self._widget_cls is MapGLWidget,
            native_widget_runtime_available=True if self._native_widget_cls is not None else None,
        )
        self._map_source = chosen_source.resolved(self._package_root)
        if self._map_source.kind == "legacy_pbf":
            self._tile_root = str(self._map_source.data_path)
            self._style_path = str(self._map_source.style_path or self._style_path)

        self._map_widget: MapWidgetBase = self._create_map_widget(map_source=self._map_source)
        self._set_central_map(self._map_widget)

        self._create_actions()
        self._create_menus()
        self.statusBar().showMessage("Ready")
        self._refresh_window_chrome()
        self._announce_backend_state()

    def _create_actions(self) -> None:
        self._action_zoom_in = QAction("Zoom In", self)
        self._action_zoom_in.setShortcuts([QKeySequence("+"), QKeySequence("=")])
        self._action_zoom_in.triggered.connect(self._zoom_in)

        self._action_zoom_out = QAction("Zoom Out", self)
        self._action_zoom_out.setShortcuts([QKeySequence("-"), QKeySequence("_")])
        self._action_zoom_out.triggered.connect(self._zoom_out)

        self._action_reset_view = QAction("Reset View", self)
        self._action_reset_view.setShortcuts([QKeySequence("Home"), QKeySequence("R")])
        self._action_reset_view.triggered.connect(self._reset_view)

        self._action_pan_left = QAction("Pan Left", self)
        self._action_pan_left.setShortcuts([QKeySequence("Left"), QKeySequence("A")])
        self._action_pan_left.triggered.connect(lambda: self._pan_by_fraction(-self.PAN_FRACTION, 0.0))

        self._action_pan_right = QAction("Pan Right", self)
        self._action_pan_right.setShortcuts([QKeySequence("Right"), QKeySequence("D")])
        self._action_pan_right.triggered.connect(lambda: self._pan_by_fraction(self.PAN_FRACTION, 0.0))

        self._action_pan_up = QAction("Pan Up", self)
        self._action_pan_up.setShortcuts([QKeySequence("Up"), QKeySequence("W")])
        self._action_pan_up.triggered.connect(lambda: self._pan_by_fraction(0.0, -self.PAN_FRACTION))

        self._action_pan_down = QAction("Pan Down", self)
        self._action_pan_down.setShortcuts([QKeySequence("Down"), QKeySequence("S")])
        self._action_pan_down.triggered.connect(lambda: self._pan_by_fraction(0.0, self.PAN_FRACTION))

        self._action_open_style = QAction("Load Legacy Style...", self)
        self._action_open_style.triggered.connect(self._open_style)

        self._action_open_map_source = QAction("Select Map Source...", self)
        self._action_open_map_source.triggered.connect(self._open_map_source)

    def _create_menus(self) -> None:
        menu_bar = self.menuBar()

        view_menu = menu_bar.addMenu("View")
        view_menu.addAction(self._action_zoom_in)
        view_menu.addAction(self._action_zoom_out)
        view_menu.addAction(self._action_reset_view)

        navigate_menu = menu_bar.addMenu("Navigate")
        navigate_menu.addAction(self._action_pan_left)
        navigate_menu.addAction(self._action_pan_right)
        navigate_menu.addAction(self._action_pan_up)
        navigate_menu.addAction(self._action_pan_down)

        file_menu = menu_bar.addMenu("File")
        file_menu.addAction(self._action_open_style)
        file_menu.addAction(self._action_open_map_source)

    def _create_map_widget(self, *, map_source: MapSourceSpec) -> MapWidgetBase:
        if map_source.kind == "osmand_obf" and self._native_widget_cls is not None:
            try:
                return self._native_widget_cls(map_source=map_source)
            except Exception as exc:  # pragma: no cover - best effort error reporting
                import sys, traceback
                print(f"[main] NativeOsmAndWidget failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                self.statusBar().showMessage(
                    f"Native OsmAnd widget unavailable, falling back to the Python renderer: {exc}",
                    8000,
                )

        try:
            return self._widget_cls(map_source=map_source)
        except (StyleLoadError, TileLoadingError):
            raise
        except Exception as exc:  # pragma: no cover - best effort error reporting
            if self._widget_cls is MapWidget:
                raise

            QMessageBox.warning(
                self,
                "GPU Acceleration Disabled",
                "The OpenGL based map view failed to initialize.\n"
                "The application will continue with the CPU renderer instead.\n\n"
                f"Details: {exc}",
            )
            self._widget_cls = MapWidget
            return MapWidget(map_source=map_source)

    def _zoom_in(self) -> None:
        self._map_widget.set_zoom(self._map_widget.zoom * 1.5)

    def _zoom_out(self) -> None:
        self._map_widget.set_zoom(self._map_widget.zoom / 1.5)

    def _reset_view(self) -> None:
        self._map_widget.reset_view()

    def _pan_by_fraction(self, fraction_x: float, fraction_y: float) -> None:
        self._map_widget.pan_by_pixels(
            self._map_widget.width() * fraction_x,
            self._map_widget.height() * fraction_y,
        )

    def _open_style(self) -> None:
        if self._map_source.kind != "legacy_pbf":
            QMessageBox.information(
                self,
                "Legacy Style Only",
                "The style.json picker only applies to the legacy PBF renderer.\n"
                "Select a tile directory to switch back to the legacy backend.",
            )
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select style.json",
            self._style_path,
            "JSON Files (*.json)",
        )
        if not path:
            return

        new_source = MapSourceSpec(
            kind="legacy_pbf",
            data_path=self._tile_root,
            style_path=path,
        ).resolved(self._package_root)

        try:
            widget = self._create_map_widget(map_source=new_source)
        except StyleLoadError as exc:
            QMessageBox.critical(self, "Error", f"Unable to load the style file:\n{exc}")
            return
        except TileLoadingError as exc:
            QMessageBox.critical(self, "Error", f"Unable to initialize tiles:\n{exc}")
            return

        self._style_path = path
        self._map_source = new_source
        self._set_central_map(widget)
        self._announce_backend_state()

    def _open_map_source(self) -> None:
        default_osmand = MapSourceSpec.osmand_default(self._package_root).resolved(self._package_root)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select map source",
            str(self._map_source.data_path),
            "OBF Files (*.obf);;All Files (*)",
        )
        if path:
            new_source = MapSourceSpec(
                kind="osmand_obf",
                data_path=path,
                resources_root=default_osmand.resources_root,
                style_path=default_osmand.style_path,
            ).resolved(self._package_root)
            try:
                widget = self._create_map_widget(map_source=new_source)
            except (StyleLoadError, TileLoadingError) as exc:
                QMessageBox.critical(self, "Error", f"Unable to open the OBF source:\n{exc}")
                return

            self._map_source = new_source
            self._set_central_map(widget)
            self._announce_backend_state()
            return

        directory = QFileDialog.getExistingDirectory(
            self,
            "Select tile directory",
            self._tile_root,
        )
        if not directory:
            return

        new_source = MapSourceSpec(
            kind="legacy_pbf",
            data_path=directory,
            style_path=self._style_path,
        ).resolved(self._package_root)
        try:
            widget = self._create_map_widget(map_source=new_source)
        except StyleLoadError as exc:
            QMessageBox.critical(self, "Error", f"Unable to load the style file:\n{exc}")
            return
        except TileLoadingError as exc:
            QMessageBox.critical(self, "Error", f"Unable to open the tile directory:\n{exc}")
            return

        self._tile_root = directory
        self._map_source = new_source
        self._set_central_map(widget)
        self._announce_backend_state()

    def _active_backend_label(self) -> str:
        return describe_active_backend(self._map_source, self._map_widget.map_backend_metadata())

    def _refresh_window_chrome(self) -> None:
        self._update_window_title()
        self._update_status_bar()

    def _update_window_title(self) -> None:
        self.setWindowTitle(
            f"Map Preview - {self._active_backend_label()} - Zoom {self._map_widget.zoom:.2f}",
        )

    def _update_status_bar(self) -> None:
        longitude, latitude = self._map_widget.center_lonlat()
        status_text = format_status_message(
            self._map_source,
            self._map_widget.map_backend_metadata(),
            zoom=self._map_widget.zoom,
            longitude=longitude,
            latitude=latitude,
        )
        self.statusBar().showMessage(status_text)

    def _announce_backend_state(self) -> None:
        self._refresh_window_chrome()
        self._emit_runtime_diagnostics()
        metadata = self._map_widget.map_backend_metadata()
        if self._map_source.kind == "osmand_obf" and metadata.tile_kind != "raster":
            self.statusBar().showMessage(
                "OsmAnd native/helper backend is unavailable, so the preview is using the legacy vector fallback.",
                10000,
            )

    def _emit_runtime_diagnostics(self) -> None:
        self._runtime_diagnostics = format_map_runtime_diagnostics(
            self._map_widget,
            map_source=self._map_source,
        )
        print(self._runtime_diagnostics, flush=True)

    def runtime_diagnostics(self) -> str:
        """Return the latest runtime diagnostics emitted by the preview window."""

        return self._runtime_diagnostics

    def apply_initial_view(
        self,
        *,
        center: tuple[float, float] | None = None,
        zoom: float | None = None,
    ) -> None:
        """Apply optional startup view overrides for debugging."""

        if center is not None:
            self._map_widget.center_on(center[0], center[1])
        if zoom is not None:
            self._map_widget.set_zoom(float(zoom))
        self._refresh_window_chrome()

    def capture_screenshot(self, destination: Path) -> bool:
        """Save a screenshot of the current preview window."""

        output_path = destination.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pixmap = self.grab()
        if pixmap.isNull():
            return False
        return pixmap.save(str(output_path))

    def _handle_view_changed(self, center_x: float, center_y: float, zoom: float) -> None:
        del center_x, center_y, zoom
        self._refresh_window_chrome()

    def _set_central_map(self, widget: MapWidgetBase) -> None:
        old = self.takeCentralWidget()
        if old is not None:
            if hasattr(old, "viewChanged"):
                try:
                    old.viewChanged.disconnect(self._handle_view_changed)  # type: ignore[attr-defined]
                except (RuntimeError, TypeError):
                    pass
            if hasattr(old, "shutdown"):
                old.shutdown()  # type: ignore[call-arg]
            old.deleteLater()

        self._map_widget = widget
        self.setCentralWidget(self._map_widget)
        if hasattr(self._map_widget, "viewChanged"):
            self._map_widget.viewChanged.connect(self._handle_view_changed)  # type: ignore[attr-defined]
        self._map_widget.setFocus()
        self._refresh_window_chrome()


def _schedule_screenshot_capture(
    app: QApplication,
    window: MainWindow,
    screenshot_path: Path,
    *,
    capture_delay_ms: int,
) -> None:
    """Capture a screenshot after the native/Python renderer settles."""

    delay_ms = max(0, int(capture_delay_ms))

    def _capture_and_exit() -> None:
        map_widget = window._map_widget
        if hasattr(map_widget, "shutdown"):
            map_widget.shutdown()

        if window.capture_screenshot(screenshot_path):
            print(f"[maps.main] screenshot={screenshot_path.resolve()}", flush=True)
            app.exit(0)
            return

        print(
            f"[maps.main] failed to save screenshot to {screenshot_path.resolve()}",
            file=sys.stderr,
            flush=True,
        )
        app.exit(1)

    QTimer.singleShot(delay_ms, _capture_and_exit)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    parsed_args = build_argument_parser().parse_args(arguments)
    package_root = Path(__file__).resolve().parent
    prepare_qt_runtime_for_backend(parsed_args.backend, package_root)
    configure_qt_opengl_defaults()
    app = QApplication([Path(__file__).name, *arguments])

    use_opengl = check_opengl_support()
    launch_config = choose_launch_configuration(
        package_root,
        use_opengl=use_opengl,
        backend=parsed_args.backend,
    )
    print(launch_config.startup_message, flush=True)

    try:
        window = MainWindow(
            map_source=launch_config.map_source,
            widget_class=launch_config.widget_class,
            native_widget_class=launch_config.native_widget_class,
        )
    except (StyleLoadError, TileLoadingError) as exc:
        if parsed_args.backend == "auto" and launch_config.map_source.kind == "osmand_obf":
            fallback_config = choose_launch_configuration(
                package_root,
                use_opengl=use_opengl,
                backend="legacy",
            )
            print(
                f"[maps.main] Python OBF startup failed ({exc}). Falling back to legacy preview.",
                flush=True,
            )
            try:
                window = MainWindow(
                    map_source=fallback_config.map_source,
                    widget_class=fallback_config.widget_class,
                    native_widget_class=fallback_config.native_widget_class,
                )
            except (StyleLoadError, TileLoadingError):
                QMessageBox.critical(None, "Error", f"Failed to initialize map:\n{exc}")
                return 1
        else:
            QMessageBox.critical(None, "Error", f"Failed to initialize map:\n{exc}")
            return 1

    if parsed_args.center is not None or parsed_args.zoom is not None:
        center = tuple(parsed_args.center) if parsed_args.center is not None else None
        window.apply_initial_view(center=center, zoom=parsed_args.zoom)

    window.show()
    if parsed_args.screenshot is not None:
        _schedule_screenshot_capture(
            app,
            window,
            parsed_args.screenshot,
            capture_delay_ms=parsed_args.capture_delay_ms,
        )
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
