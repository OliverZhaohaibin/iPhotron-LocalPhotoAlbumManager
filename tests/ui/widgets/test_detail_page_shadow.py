from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QFrame, QMainWindow, QStackedWidget, QWidget

from iPhoto.gui.ui.widgets.detail_page import DetailPageWidget, _PlaybackHeaderShadow


def test_playback_header_shadow_extends_beyond_chrome_without_changing_layout(qapp):
    root = QWidget()
    root.resize(200, 80)
    root.setStyleSheet("background: white;")

    chrome = QWidget(root)
    chrome.setGeometry(0, 0, 200, 22)
    separator = QFrame(chrome)
    separator.setGeometry(0, 20, 200, 2)
    separator.setStyleSheet("background: transparent; border: none;")

    shadow = _PlaybackHeaderShadow(separator, chrome, root)
    root.show()
    qapp.processEvents()

    assert separator.geometry() == QRect(0, 20, 200, 2)
    assert shadow.geometry() == QRect(0, 20, 200, 28)
    assert shadow.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    image = QImage(root.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("white"))
    root.render(image)

    top = image.pixelColor(100, 21).red()
    middle = image.pixelColor(100, 30).red()
    tail = image.pixelColor(100, 47).red()
    below = image.pixelColor(100, 48).red()
    assert top < middle < tail <= below
    assert below == 255


def test_playback_surface_touches_header_separator_but_keeps_filmstrip_gap(qapp):
    window = QMainWindow()
    window.resize(1200, 800)
    stack = QStackedWidget(window)
    window.setCentralWidget(stack)
    detail = DetailPageWidget(window, parent=stack, staged=True)
    stack.addWidget(detail)
    detail.complete_feature()
    window.show()
    qapp.processEvents()

    separator_bottom = (
        detail.detail_header_separator.mapTo(
            detail,
            detail.detail_header_separator.rect().bottomLeft(),
        ).y()
        + 1
    )
    player_top = detail.player_container.mapTo(
        detail,
        detail.player_container.rect().topLeft(),
    ).y()
    player_bottom = (
        detail.player_container.mapTo(
            detail,
            detail.player_container.rect().bottomLeft(),
        ).y()
        + 1
    )
    filmstrip_top = detail.filmstrip_view.mapTo(
        detail,
        detail.filmstrip_view.rect().topLeft(),
    ).y()

    assert player_top == separator_bottom
    assert filmstrip_top - player_bottom == 6
