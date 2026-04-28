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


def test_show_information_uses_information_popup(qapp: QApplication) -> None:
    """``dialogs.show_information`` should create an ``InformationPopup``.

    Because ``show_information`` blocks via a local event loop, we schedule
    a check-and-close via a single-shot timer.  The popup's properties are
    captured before it is closed (``WA_DeleteOnClose`` destroys the C++
    object on close).
    """

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QWidget

    from iPhoto.gui.ui.widgets import dialogs

    parent = QWidget()
    captured: list[dict[str, str]] = []

    def _check_and_close() -> None:
        children = parent.findChildren(InformationPopup)
        for child in children:
            captured.append({"title": child.title(), "message": child.message()})
            child.close()

    QTimer.singleShot(50, _check_and_close)
    dialogs.show_information(parent, "Test message", title="Test Title")

    assert len(captured) == 1
    assert captured[0]["title"] == "Test Title"
    assert captured[0]["message"] == "Test message"
    parent.close()


def test_show_warning_uses_information_popup(qapp: QApplication) -> None:
    """``dialogs.show_warning`` should use the project-styled ``InformationPopup``."""

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QWidget

    from iPhoto.gui.ui.widgets import dialogs

    parent = QWidget()
    captured: list[dict[str, str]] = []

    def _check_and_close() -> None:
        children = parent.findChildren(InformationPopup)
        for child in children:
            captured.append({"title": child.title(), "message": child.message()})
            child.close()

    QTimer.singleShot(50, _check_and_close)
    dialogs.show_warning(parent, "Warning message", title="Warning Title")

    assert len(captured) == 1
    assert captured[0]["title"] == "Warning Title"
    assert captured[0]["message"] == "Warning message"
    parent.close()


def test_show_information_centers_popup_on_main_window(qapp: QApplication) -> None:
    """The themed popup should be centered on the top-level window, not a child widget."""

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QWidget

    from iPhoto.gui.ui.widgets import dialogs

    window = QWidget()
    window.resize(520, 360)
    window.move(180, 140)

    child = QWidget(window)
    child.setGeometry(48, 72, 160, 120)

    captured: list[tuple[tuple[int, int], tuple[int, int]]] = []

    def _check_and_close() -> None:
        children = child.findChildren(InformationPopup)
        for popup in children:
            popup_center = popup.frameGeometry().center()
            window_center = window.frameGeometry().center()
            captured.append(
                (
                    (popup_center.x(), popup_center.y()),
                    (window_center.x(), window_center.y()),
                )
            )
            popup.close()

    window.show()
    qapp.processEvents()
    QTimer.singleShot(50, _check_and_close)
    dialogs.show_information(child, "Centered message", title="Centered Title")

    assert len(captured) == 1
    assert captured[0][0] == captured[0][1]
    window.close()
