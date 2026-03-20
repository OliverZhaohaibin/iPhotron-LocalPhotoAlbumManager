"""Entry point for the PySide6-based map preview application."""

from __future__ import annotations

if __package__ in {None, ""}:  # pragma: no cover - direct script bootstrap
    import sys
    from pathlib import Path

    _SRC_ROOT = Path(__file__).resolve().parents[1]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

import sys
from pathlib import Path

from PySide6.QtGui import QAction, QKeySequence, QOffscreenSurface, QOpenGLContext
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox

from maps.map_sources import MapBackendMetadata, MapSourceSpec, has_usable_osmand_default
from maps.map_widget import MapGLWidget, MapWidget
from maps.map_widget._map_widget_base import MapWidgetBase
from maps.style_resolver import StyleLoadError
from maps.tile_parser import TileLoadingError


def check_opengl_support() -> bool:
    """Return ``True`` when the system can create a basic OpenGL context."""

    try:
        surface = QOffscreenSurface()
        surface.create()
        if not surface.isValid():
            return False

        context = QOpenGLContext()
        if not context.create():
            return False

        if not context.makeCurrent(surface):
            return False

        context.doneCurrent()
        return True
    except Exception:
        # Any failure means the platform cannot offer the required OpenGL
        # features, so we fall back to the CPU implementation.
        return False


def choose_default_map_source(package_root: Path) -> MapSourceSpec:
    """Return the best startup source for the standalone preview window."""

    if has_usable_osmand_default(package_root):
        return MapSourceSpec.osmand_default(package_root)
    return MapSourceSpec.legacy_default(package_root)


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
    ) -> None:
        super().__init__()
        self.setWindowTitle("Map Preview")
        self.resize(1280, 860)

        self._package_root = Path(__file__).resolve().parent
        self._tile_root = tile_root
        self._style_path = style_path
        chosen_source = map_source or choose_default_map_source(self._package_root)
        self._map_source = chosen_source.resolved(self._package_root)
        if self._map_source.kind == "legacy_pbf":
            self._tile_root = str(self._map_source.data_path)
            self._style_path = str(self._map_source.style_path or self._style_path)

        self._widget_cls: type[MapWidgetBase] = widget_class or MapWidget
        self._map_widget: MapWidgetBase = self._create_map_widget(map_source=self._map_source)
        self._set_central_map(self._map_widget)

        self._create_actions()
        self._create_menus()
        self.statusBar().showMessage("Ready")
        self._refresh_window_chrome()
        self._announce_backend_state()

    # ------------------------------------------------------------------
    def _create_actions(self) -> None:
        """Assemble actions that appear in the menu bar."""

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

    # ------------------------------------------------------------------
    def _create_menus(self) -> None:
        """Create the menu structure shown in the window."""

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

    # ------------------------------------------------------------------
    def _create_map_widget(self, *, map_source: MapSourceSpec) -> MapWidgetBase:
        """Instantiate the preferred widget class with CPU fallback."""

        try:
            widget = self._widget_cls(map_source=map_source)
        except (StyleLoadError, TileLoadingError):
            # Style and tile loading errors should propagate so the caller can
            # provide a more helpful error dialog.
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
            widget = MapWidget(map_source=map_source)
        return widget

    # ------------------------------------------------------------------
    def _zoom_in(self) -> None:
        """Increase the zoom level using a multiplicative factor."""

        self._map_widget.set_zoom(self._map_widget.zoom * 1.5)

    # ------------------------------------------------------------------
    def _zoom_out(self) -> None:
        """Decrease the zoom level while maintaining smooth transitions."""

        self._map_widget.set_zoom(self._map_widget.zoom / 1.5)

    # ------------------------------------------------------------------
    def _reset_view(self) -> None:
        """Re-center the map and return to the default zoom level."""

        self._map_widget.reset_view()

    # ------------------------------------------------------------------
    def _pan_by_fraction(self, fraction_x: float, fraction_y: float) -> None:
        """Translate the map by a fraction of the current viewport size."""

        self._map_widget.pan_by_pixels(
            self._map_widget.width() * fraction_x,
            self._map_widget.height() * fraction_y,
        )

    # ------------------------------------------------------------------
    def _open_style(self) -> None:
        """Allow the user to select a different legacy ``style.json`` file."""

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
        except StyleLoadError as exc:  # pragma: no cover - best effort error reporting
            QMessageBox.critical(self, "Error", f"Unable to load the style file:\n{exc}")
            return
        except TileLoadingError as exc:  # pragma: no cover - best effort error reporting
            QMessageBox.critical(self, "Error", f"Unable to initialize tiles:\n{exc}")
            return

        self._style_path = path
        self._map_source = new_source
        self._set_central_map(widget)
        self._announce_backend_state()

    # ------------------------------------------------------------------
    def _open_map_source(self) -> None:
        """Allow the user to switch between OBF and legacy tile sources."""

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
            except (StyleLoadError, TileLoadingError) as exc:  # pragma: no cover - best effort error reporting
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
        except StyleLoadError as exc:  # pragma: no cover - best effort error reporting
            QMessageBox.critical(self, "Error", f"Unable to load the style file:\n{exc}")
            return
        except TileLoadingError as exc:  # pragma: no cover - best effort error reporting
            QMessageBox.critical(self, "Error", f"Unable to open the tile directory:\n{exc}")
            return

        self._tile_root = directory
        self._map_source = new_source
        self._set_central_map(widget)
        self._announce_backend_state()

    # ------------------------------------------------------------------
    def _active_backend_label(self) -> str:
        """Return a concise label describing the active runtime backend."""

        return describe_active_backend(self._map_source, self._map_widget.map_backend_metadata())

    # ------------------------------------------------------------------
    def _refresh_window_chrome(self) -> None:
        """Synchronize the title bar and status bar with the current view."""

        self._update_window_title()
        self._update_status_bar()

    # ------------------------------------------------------------------
    def _update_window_title(self) -> None:
        """Include backend and zoom details in the main window title."""

        self.setWindowTitle(
            f"Map Preview - {self._active_backend_label()} - Zoom {self._map_widget.zoom:.2f}",
        )

    # ------------------------------------------------------------------
    def _update_status_bar(self) -> None:
        """Display the current backend, zoom level, and map center."""

        longitude, latitude = self._map_widget.center_lonlat()
        status_text = format_status_message(
            self._map_source,
            self._map_widget.map_backend_metadata(),
            zoom=self._map_widget.zoom,
            longitude=longitude,
            latitude=latitude,
        )
        self.statusBar().showMessage(status_text)

    # ------------------------------------------------------------------
    def _announce_backend_state(self) -> None:
        """Inform the user when the requested OBF source fell back to legacy tiles."""

        self._refresh_window_chrome()
        metadata = self._map_widget.map_backend_metadata()
        if self._map_source.kind == "osmand_obf" and metadata.tile_kind != "raster":
            self.statusBar().showMessage(
                "OsmAnd helper is unavailable, so the preview is using the legacy vector fallback.",
                10000,
            )

    # ------------------------------------------------------------------
    def _handle_view_changed(self, center_x: float, center_y: float, zoom: float) -> None:
        """Refresh title and status text when the viewport changes."""

        del center_x, center_y, zoom
        self._refresh_window_chrome()

    # ------------------------------------------------------------------
    def _set_central_map(self, widget: MapWidgetBase) -> None:
        """Replace the current central widget with ``widget`` safely."""

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


def main() -> int:
    """Application entry point used by ``python main.py``."""

    app = QApplication(sys.argv)

    use_opengl = check_opengl_support()
    widget_cls: type[MapWidgetBase] = MapGLWidget if use_opengl else MapWidget
    if use_opengl:
        print("OpenGL support detected. Using GPU accelerated rendering.")
    else:
        print("OpenGL support unavailable. Falling back to CPU rendering.")

    try:
        window = MainWindow(widget_class=widget_cls)
    except (StyleLoadError, TileLoadingError) as exc:
        QMessageBox.critical(None, "Error", f"Failed to initialize map:\n{exc}")
        return 1

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
