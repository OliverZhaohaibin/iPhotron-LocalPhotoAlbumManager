from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication
from iPhotos.src.iPhoto.gui.ui.widgets.filmstrip_view import FilmstripView
import pytest

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

def test_filmstrip_view_has_scrollbar_style(qapp):
    view = FilmstripView()
    style = view.styleSheet()

    assert "QScrollBar" in style, "FilmstripView should have QScrollBar styling"
    assert "background-color: transparent" in style

def test_filmstrip_view_updates_style_on_palette_change(qapp):
    view = FilmstripView()

    # Change palette to something distinct
    palette = view.palette()
    test_color = QColor("#123456")
    palette.setColor(QPalette.ColorRole.WindowText, test_color)
    view.setPalette(palette)

    new_style = view.styleSheet()

    assert "QScrollBar" in new_style

    # Check for the expected track color (alpha=30)
    # modern_scrollbar_style sets track alpha to 30
    expected_track_color = QColor(test_color)
    expected_track_color.setAlpha(30)
    expected_hex = expected_track_color.name(QColor.NameFormat.HexArgb)

    assert expected_hex in new_style, f"Stylesheet should contain the updated track color {expected_hex}"
