"""Entry point for the PySide6 based map preview application."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QOffscreenSurface, QOpenGLContext
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox

from map_sources import MapSourceSpec

from map_widget._map_widget_base import MapWidgetBase
from maps.map_widget import MapWidget, MapGLWidget
from style_resolver import StyleLoadError
from tile_parser import TileLoadingError


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


class MainWindow(QMainWindow):
    """Primary application window that hosts an interactive map widget."""

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
        self.resize(1024, 768)

        self._package_root = Path(__file__).resolve().parent
        self._tile_root = tile_root
        self._style_path = style_path
        self._map_source = (map_source or MapSourceSpec.default(self._package_root)).resolved(
            self._package_root,
        )
        if self._map_source.kind == "legacy_pbf":
            self._tile_root = str(self._map_source.data_path)
            self._style_path = str(self._map_source.style_path or self._style_path)

        self._widget_cls: type[MapWidgetBase] = widget_class or MapWidget
        self._map_widget: MapWidgetBase = self._create_map_widget(map_source=self._map_source)
        self._set_central_map(self._map_widget)

        self._create_actions()
        self._create_menus()
        self._update_window_title()

    # ------------------------------------------------------------------
    def _create_actions(self) -> None:
        """Assemble actions that appear in the menu bar."""

        self._action_zoom_in = QAction("Zoom In", self)
        self._action_zoom_in.setShortcut(Qt.Key_Plus)
        self._action_zoom_in.triggered.connect(self._zoom_in)

        self._action_zoom_out = QAction("Zoom Out", self)
        self._action_zoom_out.setShortcut(Qt.Key_Minus)
        self._action_zoom_out.triggered.connect(self._zoom_out)

        self._action_open_style = QAction("Load Legacy Style...", self)
        self._action_open_style.triggered.connect(self._open_style)

        self._action_open_map_source = QAction("Select Map Source...", self)
        self._action_open_map_source.triggered.connect(self._open_map_source)

        self._action_reset_view = QAction("Reset View", self)
        self._action_reset_view.triggered.connect(self._reset_view)

    # ------------------------------------------------------------------
    def _create_menus(self) -> None:
        """Create the menu structure shown in the window."""

        menu_bar = self.menuBar()

        view_menu = menu_bar.addMenu("View")
        view_menu.addAction(self._action_zoom_in)
        view_menu.addAction(self._action_zoom_out)
        view_menu.addSeparator()
        view_menu.addAction(self._action_reset_view)

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
        self._update_window_title()

    # ------------------------------------------------------------------
    def _zoom_out(self) -> None:
        """Decrease the zoom level while maintaining smooth transitions."""

        self._map_widget.set_zoom(self._map_widget.zoom / 1.5)
        self._update_window_title()

    # ------------------------------------------------------------------
    def _reset_view(self) -> None:
        """Re-center the map and return to the default zoom level."""

        self._map_widget.reset_view()
        self._update_window_title()

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
        self._update_window_title()

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
            self._update_window_title()
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
        self._update_window_title()

    # ------------------------------------------------------------------
    def _update_window_title(self) -> None:
        """Include the active backend and zoom level in the window title."""

        source_name = "OBF" if self._map_source.kind == "osmand_obf" else "PBF"
        self.setWindowTitle(f"Map Preview - {source_name} - Zoom {self._map_widget.zoom:.2f}")

    # ------------------------------------------------------------------
    def _set_central_map(self, widget: MapWidgetBase) -> None:
        """Replace the current central widget with ``widget`` safely."""

        old = self.takeCentralWidget()
        if old is not None:
            if hasattr(old, "shutdown"):
                old.shutdown()  # type: ignore[call-arg]
            old.deleteLater()
        self._map_widget = widget
        self.setCentralWidget(self._map_widget)


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
