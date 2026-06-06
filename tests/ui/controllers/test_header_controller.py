from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)

from PySide6.QtWidgets import QApplication, QLabel

from iPhoto.gui.ui.controllers.header_controller import HeaderController


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def header(qapp):
    location_label = QLabel()
    timestamp_label = QLabel()
    controller = HeaderController(location_label, timestamp_label)
    return controller, location_label, timestamp_label


def test_location_is_visible_without_timestamp(header) -> None:
    controller, location_label, timestamp_label = header

    controller.update_from_values("Munich", None)

    assert location_label.text() == "Munich"
    assert not location_label.isHidden()
    assert timestamp_label.text() == ""
    assert timestamp_label.isHidden()


def test_timestamp_is_visible_without_location(header) -> None:
    controller, location_label, timestamp_label = header

    controller.update_from_values(None, "2026-06-06T12:00:00+00:00")

    assert location_label.text() == ""
    assert location_label.isHidden()
    assert timestamp_label.text()
    assert not timestamp_label.isHidden()
    assert timestamp_label.font() == controller._timestamp_single_line_font


def test_header_clears_when_location_and_timestamp_are_missing(header) -> None:
    controller, location_label, timestamp_label = header
    controller.update_from_values("Munich", None)

    controller.update_from_values(None, None)

    assert location_label.text() == ""
    assert location_label.isHidden()
    assert timestamp_label.text() == ""
    assert timestamp_label.isHidden()
