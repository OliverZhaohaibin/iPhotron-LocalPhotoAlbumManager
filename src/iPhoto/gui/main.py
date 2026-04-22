"""GUI entry point for the iPhoto desktop application."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPalette, QSurfaceFormat
from PySide6.QtWidgets import QApplication

from iPhoto.bootstrap.qt_shader_cache import configure_shader_cache_environment

_logger = logging.getLogger(__name__)


def _configure_qt_shader_disk_cache() -> None:
    """Route shader/program caches into a managed ``.iPhoto`` work directory."""
    configure_shader_cache_environment()


def _prepare_qt_runtime_for_maps() -> None:
    """Apply Linux Qt platform flags required by the native OsmAnd widget."""

    if sys.platform != "linux":
        return

    if os.environ.get("IPHOTO_DISABLE_OPENGL", "").strip().lower() in {"1", "true", "yes", "on"}:
        return

    from maps.map_sources import has_usable_osmand_native_widget

    maps_package_root = Path(__file__).resolve().parents[2] / "maps"
    if not has_usable_osmand_native_widget(maps_package_root):
        return

    if not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    if os.environ.get("QT_QPA_PLATFORM") == "xcb":
        os.environ.setdefault("QT_OPENGL", "desktop")
        os.environ.setdefault("QT_XCB_GL_INTEGRATION", "xcb_glx")


def _prefer_local_source_tree() -> None:
    """Ensure direct script runs import the workspace package first.

    When ``main.py`` is launched directly from an IDE, Python may resolve the
    editable ``iPhoto`` install from another checkout before this repo's
    ``src`` tree. Prepending the local ``src`` path keeps the GUI aligned with
    the code being edited.
    """

    src_root = Path(__file__).resolve().parents[2]
    src_root_str = str(src_root)
    if sys.path and sys.path[0] == src_root_str:
        return
    try:
        sys.path.remove(src_root_str)
    except ValueError:
        pass
    sys.path.insert(0, src_root_str)


def _configure_qt_opengl_defaults() -> None:
    """Apply the same desktop OpenGL defaults used by the standalone map tool."""

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
        surface_format.setDepthBufferSize(24)
        surface_format.setStencilBufferSize(8)
        QSurfaceFormat.setDefaultFormat(surface_format)
    except Exception:
        return


def main(argv: list[str] | None = None) -> int:
    """Launch the Qt application and return the exit code."""

    _prefer_local_source_tree()

    # Ensure the ``iPhoto`` root logger is configured before any component
    # creates a child logger.  ``get_logger()`` lazily attaches a StreamHandler
    # to the ``iPhoto`` logger so all ``iPhoto.*`` loggers propagate output to
    # stderr at INFO level by default.
    from iPhoto.utils.logging import get_logger as _init_logging
    _init_logging()

    arguments = list(sys.argv if argv is None else argv)
    _prepare_qt_runtime_for_maps()
    _configure_qt_opengl_defaults()
    app = QApplication(arguments)

    # ``QToolTip`` instances inherit ``WA_TranslucentBackground`` from the frameless
    # main window, which means they expect the application to provide an opaque fill
    # colour.  Some Qt styles ignore stylesheet rules for tooltips, so we proactively
    # update the palette that drives those popups to guarantee readable text.
    tooltip_palette = QPalette(app.palette())

    def _resolved_colour(source: QColor, fallback: QColor) -> QColor:
        """Return a copy of *source* with a fully opaque alpha channel.

        Qt reports transparent colours for certain palette roles when
        ``WA_TranslucentBackground`` is active.  Failing to normalise the alpha value
        causes the compositor to blend the tooltip against the desktop wallpaper,
        producing the solid black rectangle described in the regression report.
        Falling back to a well-tested default keeps the tooltip legible even on
        themes that omit one of the roles we query.
        """

        if not source.isValid():
            return QColor(fallback)

        resolved = QColor(source)
        resolved.setAlpha(255)
        return resolved

    base_colour = _resolved_colour(
        tooltip_palette.color(QPalette.ColorRole.Window), QColor("#eef3f6")
    )
    text_colour = _resolved_colour(
        tooltip_palette.color(QPalette.ColorRole.WindowText), QColor(Qt.GlobalColor.black)
    )

    # Ensure the text remains readable by checking the lightness contrast.  When the
    # palette provides nearly identical shades we fall back to a simple dark-on-light
    # scheme that mirrors Qt's built-in defaults.
    if abs(base_colour.lightness() - text_colour.lightness()) < 40:
        base_colour = QColor("#eef3f6")
        text_colour = QColor(Qt.GlobalColor.black)

    tooltip_palette.setColor(QPalette.ColorRole.ToolTipBase, base_colour)
    tooltip_palette.setColor(QPalette.ColorRole.ToolTipText, text_colour)
    app.setPalette(tooltip_palette, "QToolTip")

    from iPhoto.bootstrap.runtime_context import RuntimeContext
    from iPhoto.gui.coordinators.main_coordinator import MainCoordinator
    from iPhoto.gui.ui.main_window import MainWindow

    # Defer heavy library binding + initial scan until the event loop is running.
    context = RuntimeContext.create(defer_startup=True)
    # --- Phase 4: Coordinator Wiring ---
    window = MainWindow(context)

    # Coordinator needs Window, Context, and Container
    window.show()

    def _initialize_after_show() -> None:
        _logger.info("_initialize_after_show: creating MainCoordinator")
        coordinator = MainCoordinator(window, context)
        window.set_coordinator(coordinator)
        coordinator.start()
        _logger.info("_initialize_after_show: coordinator started, resuming startup tasks")
        context.resume_startup_tasks()

        if len(arguments) > 1:
            _logger.info("_initialize_after_show: opening album from CLI argument %s", arguments[1])
            coordinator.open_album_from_path(Path(arguments[1]))
            return
        _logger.info("_initialize_after_show: selecting All Photos in sidebar")
        window.ui.sidebar.select_all_photos(emit_signal=True)

    QTimer.singleShot(0, _initialize_after_show)

    return app.exec()


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())
