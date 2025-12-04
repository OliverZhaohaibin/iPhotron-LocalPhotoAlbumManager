"""GUI entry point for the iPhoto desktop application."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette, QPixmap, QPainter, QFont
from PySide6.QtWidgets import QApplication, QSplashScreen


from iPhotos.src.iPhoto.appctx import AppContext
from iPhotos.src.iPhoto.gui.ui.main_window import MainWindow


def _create_splash_screen() -> QSplashScreen:
    """Create a lightweight splash screen for immediate visual feedback."""

    pixmap = QPixmap(400, 200)
    pixmap.fill(QColor("#2d2d2d"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#ffffff"))
    font = QFont("Arial", 24, QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "LexiPhoto")
    painter.end()

    splash = QSplashScreen(pixmap)
    splash.showMessage(
        "Initializing...",
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
        QColor("#cccccc")
    )
    return splash


def main(argv: list[str] | None = None) -> int:
    """Launch the Qt application and return the exit code."""

    arguments = list(sys.argv if argv is None else argv)
    app = QApplication(arguments)

    splash = _create_splash_screen()
    splash.show()
    app.processEvents()

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

    context = AppContext()
    window = MainWindow(context)
    window.show()
    splash.finish(window)

    def _boot_and_navigate() -> None:
        window.boot()
        # Allow opening an album directly via argv[1].
        if len(arguments) > 1:
            window.open_album_from_path(Path(arguments[1]))
        else:
            window.ui.sidebar.select_all_photos()

    QTimer.singleShot(0, _boot_and_navigate)
    QTimer.singleShot(5000, context.validate_recent_albums)

    return app.exec()


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())
