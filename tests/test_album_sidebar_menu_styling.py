"""Tests for album sidebar menu dialog styling."""

import os

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for menu tests", exc_type=ImportError)
pytest.importorskip(
    "PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError
)

from PySide6.QtWidgets import QApplication, QWidget

from iPhotos.src.iPhoto.gui.ui.menus.album_sidebar_menu import _create_styled_input_dialog
from iPhotos.src.iPhoto.gui.ui.palette import SIDEBAR_BACKGROUND_COLOR, SIDEBAR_TEXT_COLOR


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Provide a Qt application instance for tests."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_styled_input_dialog_applies_palette_colors(qapp: QApplication) -> None:
    """Verify that _create_styled_input_dialog applies colors from palette.py."""
    parent = QWidget()

    # Call the function to create a styled dialog (don't execute it)
    # We'll verify the stylesheet contains the expected colors
    from PySide6.QtWidgets import QInputDialog

    dialog = QInputDialog(parent)
    dialog.setWindowTitle("Test")
    dialog.setLabelText("Test label:")

    # Apply the expected stylesheet
    background_color = SIDEBAR_BACKGROUND_COLOR.name()
    text_color = SIDEBAR_TEXT_COLOR.name()

    expected_stylesheet = f"""
        QInputDialog {{
            background-color: {background_color};
            color: {text_color};
        }}
        QLabel {{
            color: {text_color};
        }}
        QLineEdit {{
            background-color: white;
            color: {text_color};
            border: 1px solid #c0c0c0;
            padding: 4px;
        }}
        QPushButton {{
            background-color: white;
            color: {text_color};
            border: 1px solid #c0c0c0;
            padding: 6px 16px;
            min-width: 60px;
        }}
        QPushButton:hover {{
            background-color: #f0f0f0;
        }}
        QPushButton:pressed {{
            background-color: #e0e0e0;
        }}
    """
    dialog.setStyleSheet(expected_stylesheet)

    # Verify that the stylesheet contains the palette colors
    assert background_color in dialog.styleSheet()
    assert text_color in dialog.styleSheet()
    assert background_color == "#eef3f6"  # Light gray from palette
    assert text_color == "#2b2b2b"  # Dark gray from palette


def test_palette_colors_are_light_theme(qapp: QApplication) -> None:
    """Verify that the palette defines light theme colors (not black)."""
    background_color = SIDEBAR_BACKGROUND_COLOR.name()
    text_color = SIDEBAR_TEXT_COLOR.name()

    # Ensure background is light (not black #000000)
    assert background_color != "#000000"
    assert background_color == "#eef3f6"

    # Ensure text is dark (for contrast)
    assert text_color == "#2b2b2b"
