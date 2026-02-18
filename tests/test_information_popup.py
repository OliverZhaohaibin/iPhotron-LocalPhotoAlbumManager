"""Tests for the InformationPopup widget."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.information_popup import InformationPopup


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Ensure a single QApplication instance exists for widget tests."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_default_title_and_message(qapp: QApplication) -> None:
    """The popup should initialise with sensible defaults."""

    popup = InformationPopup()
    assert popup.title() == "Information"
    assert popup.message() == ""
    popup.close()


def test_custom_title_and_message(qapp: QApplication) -> None:
    """Constructor keyword arguments should populate the labels."""

    popup = InformationPopup(title="Notice", message="Hello world")
    assert popup.title() == "Notice"
    assert popup.message() == "Hello world"
    popup.close()


def test_set_title_updates_label(qapp: QApplication) -> None:
    """Calling set_title should update the title label text."""

    popup = InformationPopup()
    popup.set_title("Updated")
    assert popup.title() == "Updated"
    popup.close()


def test_set_message_updates_label(qapp: QApplication) -> None:
    """Calling set_message should update the message label text."""

    popup = InformationPopup()
    popup.set_message("New content")
    assert popup.message() == "New content"
    popup.close()


def test_close_button_exposed(qapp: QApplication) -> None:
    """The close_button property should return a QToolButton."""

    popup = InformationPopup()
    btn = popup.close_button
    assert btn is not None
    assert btn.toolTip() == "Close"
    popup.close()


def test_close_button_closes_popup(qapp: QApplication) -> None:
    """Clicking the close button should hide the popup."""

    popup = InformationPopup()
    popup.show()
    assert popup.isVisible()
    popup.close_button.click()
    assert not popup.isVisible()


def test_frameless_window_flags(qapp: QApplication) -> None:
    """The popup should use a frameless window hint."""

    popup = InformationPopup()
    flags = popup.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    popup.close()


def test_close_button_icon_size_matches_main_window(qapp: QApplication) -> None:
    """The close button dimensions should match the main window's controls."""

    from iPhoto.gui.ui.widgets.main_window_metrics import (
        WINDOW_CONTROL_BUTTON_SIZE,
        WINDOW_CONTROL_GLYPH_SIZE,
    )

    popup = InformationPopup()
    btn = popup.close_button
    assert btn.iconSize() == WINDOW_CONTROL_GLYPH_SIZE
    assert btn.size() == WINDOW_CONTROL_BUTTON_SIZE
    popup.close()
