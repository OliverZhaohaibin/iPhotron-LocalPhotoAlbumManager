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


def test_info_panel_video_shows_lens_when_available(qapp: QApplication) -> None:
    """When a video asset has lens metadata the lens label must be visible."""

    panel = InfoPanel()
    metadata = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "is_video": True,
        "make": "Apple",
        "model": "Apple iPhone 12",
        "lens": "iPhone 12 back camera 4.2mm f/1.6",
        "w": 1920,
        "h": 1080,
        "bytes": 8_000_000,
        "codec": "hevc",
        "frame_rate": 30.0,
        "dur": 5.0,
    }

    panel.set_asset_metadata(metadata)

    assert not panel._lens_label.isHidden()
    assert "iPhone 12 back camera 4.2mm f/1.6" in panel._lens_label.text()
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


def test_info_panel_loading_state_shows_loading_message(qapp: QApplication) -> None:
    """Sparse metadata should show a loading hint while enrichment is pending."""

    panel = InfoPanel()
    metadata = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "is_video": True,
        "_metadata_loading": True,
    }

    panel.set_asset_metadata(metadata)

    assert panel._exposure_label.text() == "Loading detailed video information..."
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


def test_info_panel_emits_dismissed_when_closed(qapp: QApplication) -> None:
    """Closing the panel should emit the dismissed signal exactly once."""

    panel = InfoPanel()
    dismissed = []
    panel.dismissed.connect(lambda: dismissed.append(True))

    panel.show()
    panel.close_button.click()
    qapp.processEvents()

    assert dismissed == [True]


def test_info_panel_centers_on_parent(qapp: QApplication) -> None:
    """The panel should center itself over its parent on first show."""

    from PySide6.QtWidgets import QMainWindow

    parent = QMainWindow()
    parent.setGeometry(200, 200, 800, 600)
    parent.show()

    panel = InfoPanel(parent)
    panel.show()
    qapp.processEvents()

    parent_center = parent.geometry().center()
    panel_center = panel.geometry().center()

    assert abs(panel_center.x() - parent_center.x()) <= 120
    assert abs(panel_center.y() - parent_center.y()) <= 120

    panel.close()
    parent.close()


def test_info_panel_has_shadow_margin(qapp: QApplication) -> None:
    """The root layout should reserve right/bottom margins for the shadow."""

    panel = InfoPanel()
    layout = panel.layout()
    margins = layout.contentsMargins()
    shadow = InfoPanel._SHADOW_SIZE
    assert margins.left() == 0
    assert margins.top() == 0
    assert margins.right() == shadow
    assert margins.bottom() == shadow
    panel.close()


def test_info_panel_video_shows_lens_spec_string_when_no_model_name(qapp: QApplication) -> None:
    """When only a lens spec string (e.g. Fujifilm LensInfo '23mm f/2') is available,
    the lens label must be visible with the spec text."""

    panel = InfoPanel()
    meta = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "is_video": True,
        "make": "FUJIFILM",
        "model": "X-T4",
        "lens": "23mm f/2",
        "w": 1920,
        "h": 1080,
        "bytes": 12_000_000,
        "codec": "h264",
        "frame_rate": 25.0,
        "dur": 10.0,
    }

    panel.set_asset_metadata(meta)

    assert not panel._lens_label.isHidden()
    assert "23mm f/2" in panel._lens_label.text()
    panel.close()


def test_info_panel_lens_spec_string_not_duplicated_when_focal_and_fnumber_also_present(
    qapp: QApplication,
) -> None:
    """When the lens string is a spec string (e.g. '23mm f/2') AND separate
    focal_length / f_number fields are also present, the label must show
    the lens string exactly once — not a garbled duplication like '2323 22'."""

    panel = InfoPanel()
    meta = {
        "rel": "clip.MOV",
        "name": "clip.MOV",
        "is_video": True,
        "make": "FUJIFILM",
        "model": "X-T4",
        "lens": "23mm f/2",
        "focal_length": 23.0,
        "f_number": 2.0,
        "w": 1920,
        "h": 1080,
        "bytes": 12_000_000,
        "codec": "h264",
        "frame_rate": 25.0,
        "dur": 10.0,
    }

    panel.set_asset_metadata(meta)

    label_text = panel._lens_label.text()
    assert not panel._lens_label.isHidden()
    assert label_text == "23mm f/2"
    panel.close()


def test_info_panel_named_lens_model_gets_focal_appended(
    qapp: QApplication,
) -> None:
    """A named lens model string like 'XF23mmF2 R WR' should have the separate
    focal_length / f_number fields appended because it is not a complete spec
    string (no 'f/' prefix in the aperture token).  The old broad _FOCAL_LENGTH_RE
    would have incorrectly suppressed the append."""

    panel = InfoPanel()
    meta = {
        "rel": "img.jpg",
        "name": "img.jpg",
        "is_video": False,
        "make": "FUJIFILM",
        "model": "X-T4",
        "lens": "XF23mmF2 R WR",
        "focal_length": 23.0,
        "f_number": 2.0,
    }

    panel.set_asset_metadata(meta)

    label_text = panel._lens_label.text()
    assert not panel._lens_label.isHidden()
    # The named model should be present and enriched with focal + aperture info.
    assert "XF23mmF2 R WR" in label_text
    assert "23" in label_text   # focal length must appear
    assert "ƒ2" in label_text  # aperture must appear
    panel.close()
