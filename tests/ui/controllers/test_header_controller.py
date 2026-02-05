from __future__ import annotations

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QApplication = QtWidgets.QApplication
QLabel = QtWidgets.QLabel

from src.iPhoto.gui.ui.controllers.header_controller import HeaderController


@pytest.fixture()
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_apply_header_text_shows_location_without_timestamp(qapp: QApplication) -> None:
    location = QLabel()
    timestamp = QLabel()
    controller = HeaderController(location, timestamp)

    controller._apply_header_text("Shanghai", None)

    assert location.isVisible()
    assert location.text() == "Shanghai"
    assert not timestamp.isVisible()


def test_format_timestamp_accepts_exif_datetime_string(qapp: QApplication) -> None:
    location = QLabel()
    timestamp = QLabel()
    controller = HeaderController(location, timestamp)

    formatted = controller._format_timestamp("2024:05:10 17:30:00")

    assert formatted is not None
    assert "10." in formatted
