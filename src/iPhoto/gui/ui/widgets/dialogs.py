"""Reusable dialog helpers for the desktop UI."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget

from ..theme_manager import DARK_THEME, LIGHT_THEME, ThemeColors


def select_directory(parent: QWidget, caption: str, start: Optional[Path] = None) -> Optional[Path]:
    """Return a directory selected by the user or ``None`` when cancelled."""

    directory = str(start) if start is not None else ""
    path = QFileDialog.getExistingDirectory(parent, caption, directory)
    if not path:
        return None
    return Path(path)


def _apply_theme(box: QMessageBox, parent: Optional[QWidget]) -> None:
    """Apply the active theme colors to the message box."""
    # Prioritize parent palette if available, otherwise fallback to app palette
    if parent:
        palette = parent.palette()
    else:
        palette = QApplication.palette()

    bg_color = palette.color(QPalette.ColorRole.Window).name()
    text_color = palette.color(QPalette.ColorRole.WindowText).name()

    # Explicitly set the stylesheet to override any global application styles
    # that might be forcing a dark/light background inconsistently.
    stylesheet = (
        f"QMessageBox {{ background-color: {bg_color}; color: {text_color}; }}"
        f"QLabel {{ color: {text_color}; }}"
    )
    box.setStyleSheet(stylesheet)


def _resolve_popup_palette_source(parent: Optional[QWidget]) -> QPalette:
    """Return the most appropriate palette source for popup surfaces."""

    theme_colors = _resolve_popup_theme_colors(parent)
    if theme_colors is not None:
        palette = QPalette(QApplication.palette())
        palette.setColor(QPalette.ColorRole.Window, theme_colors.window_background)
        palette.setColor(QPalette.ColorRole.WindowText, theme_colors.text_primary)
        palette.setColor(QPalette.ColorRole.Base, theme_colors.window_background)
        palette.setColor(QPalette.ColorRole.AlternateBase, theme_colors.window_background)
        palette.setColor(QPalette.ColorRole.ToolTipBase, theme_colors.window_background)
        palette.setColor(QPalette.ColorRole.ToolTipText, theme_colors.text_primary)
        palette.setColor(QPalette.ColorRole.Text, theme_colors.text_primary)
        palette.setColor(QPalette.ColorRole.Button, theme_colors.window_background)
        palette.setColor(QPalette.ColorRole.ButtonText, theme_colors.text_primary)
        palette.setColor(QPalette.ColorRole.Mid, theme_colors.border_color)
        return palette

    if parent is not None:
        main_window = parent.window()
        if main_window is not None:
            return QPalette(main_window.palette())
        return QPalette(parent.palette())
    return QPalette(QApplication.palette())


def _resolve_popup_theme_colors(parent: Optional[QWidget]) -> ThemeColors | None:
    """Resolve theme colors from the hosting window context when available."""

    widget = parent.window() if parent is not None and parent.window() is not None else parent
    coordinator = getattr(widget, "coordinator", None)
    context = getattr(coordinator, "_context", None)
    theme_manager = getattr(context, "theme", None)
    if theme_manager is not None and hasattr(theme_manager, "get_effective_theme_mode"):
        return DARK_THEME if theme_manager.get_effective_theme_mode() == "dark" else LIGHT_THEME

    settings = getattr(context, "settings", None)
    if settings is not None and hasattr(settings, "get"):
        theme_setting = settings.get("ui.theme", "system")
        if theme_setting == "dark":
            return DARK_THEME
        if theme_setting == "light":
            return LIGHT_THEME

    app = QGuiApplication.instance()
    if app is not None and app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
        return DARK_THEME
    if app is not None:
        window_color = QApplication.palette().color(QPalette.ColorRole.Window)
        return DARK_THEME if window_color.lightness() < 128 else LIGHT_THEME
    return None


def show_error(parent: QWidget, message: str, *, title: str = "iPhoto") -> None:
    """Display a blocking error message."""

    box = QMessageBox(QMessageBox.Icon.Critical, title, message, QMessageBox.StandardButton.Ok, parent)
    _apply_theme(box, parent)
    box.exec()


def show_information(parent: QWidget, message: str, *, title: str = "iPhoto") -> None:
    """Display a blocking informational popup using :class:`InformationPopup`.

    The popup is centred over *parent* and reuses the main window's close
    button for a consistent look and feel.  A local event loop keeps the
    call blocking so that existing callers (e.g.
    ``DialogController.prompt_for_basic_library()``) continue to work as
    expected.
    """

    from PySide6.QtCore import QEventLoop

    from .information_popup import InformationPopup

    popup = InformationPopup(parent, title=title, message=message)
    popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    popup.setPalette(_resolve_popup_palette_source(parent))
    popup.setBackgroundRole(QPalette.ColorRole.Window)
    popup.center_on(parent)

    loop = QEventLoop()
    popup.destroyed.connect(loop.quit)
    popup.show()
    popup.setPalette(_resolve_popup_palette_source(parent))
    popup.center_on(parent)
    popup.raise_()
    loop.exec()


def show_warning(parent: QWidget, message: str, *, title: str = "iPhoto") -> None:
    """Display a blocking warning popup using the project-styled InformationPopup."""

    show_information(parent, message, title=title)


def confirm_action(
    parent: QWidget,
    message: str,
    *,
    title: str = "Confirmation",
    yes_label: str = "Yes",
    no_label: str = "No",
) -> bool:
    """Ask the user to confirm an action.

    Returns:
        True if the user selected the affirmative option, False otherwise.
    """
    box = QMessageBox(QMessageBox.Icon.Question, title, message, QMessageBox.StandardButton.NoButton, parent)
    yes_btn = box.addButton(yes_label, QMessageBox.ButtonRole.YesRole)
    box.addButton(no_label, QMessageBox.ButtonRole.NoRole)

    _apply_theme(box, parent)
    box.exec()

    clicked = box.clickedButton()
    return clicked == yes_btn if clicked is not None else False
