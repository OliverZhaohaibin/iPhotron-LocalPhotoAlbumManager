"""GUI entry point for the iPhoto desktop application."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def main(argv: list[str] | None = None) -> int:
    """Launch the Qt application and return the exit code."""

    arguments = list(sys.argv if argv is None else argv)
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

    from iPhoto.appctx import AppContext
    from iPhoto.gui.coordinators.main_coordinator import MainCoordinator
    from iPhoto.gui.ui.main_window import MainWindow

    # Defer heavy library binding + initial scan until the event loop is running.
    context = AppContext(defer_startup_tasks=True)
    container = context.container

    # --- Phase 4: Coordinator Wiring ---
    window = MainWindow(context)

    # Coordinator needs Window, Context, and Container
    window.show()

    def _initialize_after_show() -> None:
        coordinator = MainCoordinator(window, context, container)
        window.set_coordinator(coordinator)
        coordinator.start()
        context.resume_startup_tasks()

        if len(arguments) > 1:
            coordinator.open_album_from_path(Path(arguments[1]))
            return
        window.ui.sidebar.select_all_photos(emit_signal=True)

    QTimer.singleShot(0, _initialize_after_show)

    return app.exec()


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())
