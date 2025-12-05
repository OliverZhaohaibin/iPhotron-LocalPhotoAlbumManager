"""Reusable dialog helpers for the desktop UI."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget


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


def show_error(parent: QWidget, message: str, *, title: str = "iPhoto") -> None:
    """Display a blocking error message."""

    box = QMessageBox(QMessageBox.Icon.Critical, title, message, QMessageBox.StandardButton.Ok, parent)
    _apply_theme(box, parent)
    box.exec()


def show_information(parent: QWidget, message: str, *, title: str = "iPhoto") -> None:
    """Display an informational message box."""

    box = QMessageBox(QMessageBox.Icon.Information, title, message, QMessageBox.StandardButton.Ok, parent)
    _apply_theme(box, parent)
    box.exec()


def show_warning(parent: QWidget, message: str, *, title: str = "iPhoto") -> None:
    """Display a blocking warning message."""

    box = QMessageBox(QMessageBox.Icon.Warning, title, message, QMessageBox.StandardButton.Ok, parent)
    _apply_theme(box, parent)
    box.exec()


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
