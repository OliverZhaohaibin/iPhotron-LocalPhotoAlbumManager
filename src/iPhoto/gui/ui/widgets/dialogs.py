"""Reusable dialog helpers for the desktop UI."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget


def select_directory(parent: QWidget, caption: str, start: Optional[Path] = None) -> Optional[Path]:
    """Return a directory selected by the user or ``None`` when cancelled."""

    directory = str(start) if start is not None else ""
    path = QFileDialog.getExistingDirectory(parent, caption, directory)
    if not path:
        return None
    return Path(path)


def show_error(parent: QWidget, message: str, *, title: str = "iPhoto") -> None:
    """Display a blocking error message."""

    box = QMessageBox(QMessageBox.Icon.Critical, title, message, QMessageBox.StandardButton.Ok, parent)
    box.setPalette(QApplication.palette())
    box.exec()


def show_information(parent: QWidget, message: str, *, title: str = "iPhoto") -> None:
    """Display an informational message box."""

    box = QMessageBox(QMessageBox.Icon.Information, title, message, QMessageBox.StandardButton.Ok, parent)
    box.setPalette(QApplication.palette())
    box.exec()
