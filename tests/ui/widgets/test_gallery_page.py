"""Tests for GalleryPageWidget."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")

from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.gallery_page import GalleryPageWidget
from iPhoto.gui.ui.widgets.main_window_metrics import HEADER_BUTTON_SIZE, HEADER_ICON_GLYPH_SIZE


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_gallery_back_button_matches_header_button_metrics(qapp):
    page = GalleryPageWidget()

    assert page.back_button.iconSize() == HEADER_ICON_GLYPH_SIZE
    assert page.back_button.size() == HEADER_BUTTON_SIZE
    assert page.back_button.autoRaise() is True


def test_cluster_gallery_mode_toggles_header_visibility(qapp):
    page = GalleryPageWidget()

    page.set_cluster_gallery_mode(True)
    assert page._header.isVisible() is True

    page.set_cluster_gallery_mode(False)
    assert page._header.isVisible() is False
