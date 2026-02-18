"""Focused tests for the floating info panel widget."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.info_panel import InfoPanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Ensure a single QApplication instance exists for widget tests."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_info_panel_formats_video_metadata(qapp: QApplication) -> None:
    """Verify that video-specific fields render with human readable text."""

    panel = InfoPanel()
    metadata = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "dt": "2024-02-18T12:34:56Z",
        "make": "Apple",
        "model": "Apple iPhone 13 Pro",
        "is_video": True,
        "w": 1920,
        "h": 1080,
        "bytes": 24192000,
        "codec": "hevc",
        "frame_rate": 59.94,
        "dur": 8.0,
    }

    panel.set_asset_metadata(metadata)

    assert panel.current_rel() == "clip.MOV"
    assert panel._camera_label.text() == "Apple iPhone 13 Pro"
    summary_text = panel._summary_label.text()
    assert "1920 × 1080" in summary_text
    assert "23.1 MB" in summary_text
    assert "HEVC" in summary_text
    details_text = panel._exposure_label.text()
    assert "fps" in details_text
    assert "0:08" in details_text
    assert not panel._lens_label.isVisible()
    panel.close()


def test_info_panel_video_missing_details_shows_fallback(qapp: QApplication) -> None:
    """When metadata is sparse the video fallback string should be displayed."""

    panel = InfoPanel()
    metadata = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "is_video": True,
    }

    panel.set_asset_metadata(metadata)

    assert panel._exposure_label.text() == "Detailed video information is unavailable."
    assert not panel._summary_label.isVisible()
    panel.close()


def test_info_panel_frameless_window_flags(qapp: QApplication) -> None:
    """The info panel should use a frameless window hint."""

    from PySide6.QtCore import Qt

    panel = InfoPanel()
    flags = panel.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    panel.close()


def test_info_panel_close_button_matches_main_window(qapp: QApplication) -> None:
    """The close button dimensions should match the main window's controls."""

    from iPhoto.gui.ui.widgets.main_window_metrics import (
        WINDOW_CONTROL_BUTTON_SIZE,
        WINDOW_CONTROL_GLYPH_SIZE,
    )

    panel = InfoPanel()
    btn = panel.close_button
    assert btn is not None
    assert btn.toolTip() == "Close"
    assert btn.iconSize() == WINDOW_CONTROL_GLYPH_SIZE
    assert btn.size() == WINDOW_CONTROL_BUTTON_SIZE
    panel.close()


def test_info_panel_close_button_closes(qapp: QApplication) -> None:
    """Clicking the close button should hide the panel."""

    panel = InfoPanel()
    panel.show()
    assert panel.isVisible()
    panel.close_button.click()
    assert not panel.isVisible()
