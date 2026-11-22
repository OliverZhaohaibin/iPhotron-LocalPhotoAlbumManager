"""Tests for video-specific edit sidebar layout changes."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.models.edit_session import EditSession
from iPhoto.gui.ui.widgets.edit_bw_section import EditBWSection
from iPhoto.gui.ui.widgets.edit_sharpen_section import EditSharpenSection
from iPhoto.gui.ui.widgets.edit_sidebar import EditSidebar
from iPhoto.gui.ui.widgets.edit_vignette_section import EditVignetteSection
from iPhoto.gui.ui.widgets.edit_wb_section import EditWBSection


@pytest.fixture(scope="module")
def qapp():
    """Provide a QApplication instance for widget tests."""

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_crop_controls_can_scroll_in_a_short_sidebar(qapp, qtbot) -> None:
    sidebar = EditSidebar()
    qtbot.addWidget(sidebar)
    sidebar.set_mode("crop")
    sidebar.resize(sidebar.sizeHint().width(), 220)
    sidebar.show()
    qapp.processEvents()

    assert sidebar.height() == 220
    crop_scroll = sidebar._stack.currentWidget()
    scrollbar = crop_scroll.verticalScrollBar()
    assert scrollbar.maximum() > 0
    scrollbar.setValue(scrollbar.maximum())
    assert crop_scroll.widget().geometry().bottom() == crop_scroll.viewport().rect().bottom()


def test_crop_wheel_scrolls_over_slider_until_it_has_focus(qapp, qtbot) -> None:
    sidebar = EditSidebar()
    qtbot.addWidget(sidebar)
    session = EditSession(sidebar)
    sidebar.set_session(session)
    sidebar.set_mode("crop")
    sidebar.resize(sidebar.sizeHint().width(), 220)
    sidebar.show()
    sidebar.activateWindow()
    qapp.processEvents()

    crop_scroll = sidebar._stack.currentWidget()
    scrollbar = crop_scroll.verticalScrollBar()
    slider = sidebar._perspective_controls._straighten_row._slider
    crop_scroll.setFocus()
    assert not slider.hasFocus()
    position = slider.mapTo(sidebar, slider.rect().center())
    angle_delta = QPoint(0, -120)

    QTest.wheelEvent(sidebar.windowHandle(), position, angle_delta)
    assert scrollbar.value() > 0
    assert session.value("Crop_Straighten") == 0.0

    scrollbar.setValue(0)
    slider.setFocus()
    assert slider.hasFocus()

    QTest.wheelEvent(sidebar.windowHandle(), position, angle_delta)
    assert scrollbar.value() == 0
    assert session.value("Crop_Straighten") == pytest.approx(-0.9)


def test_video_edit_mode_flattens_first_three_sections(qapp) -> None:
    """Video editing should collapse the first three sections and hide their masters."""

    sidebar = EditSidebar()

    sidebar.set_video_edit_mode(True)

    assert sidebar._light_section_container.is_expanded() is False
    assert sidebar._color_section_container.is_expanded() is False
    assert sidebar._bw_section_container.is_expanded() is False

    for section in (
        sidebar._light_section,
        sidebar._color_section,
        sidebar._bw_section,
    ):
        assert section.master_slider.isHidden() is True
        assert section.options_section.header_visible() is False
        assert section.options_section.is_expanded() is True


def test_disabling_video_edit_mode_restores_image_layout(qapp) -> None:
    """Leaving video mode should restore the original image-edit hierarchy."""

    sidebar = EditSidebar()

    sidebar.set_video_edit_mode(True)
    sidebar.set_video_edit_mode(False)

    assert sidebar._light_section_container.is_expanded() is True
    assert sidebar._color_section_container.is_expanded() is True
    assert sidebar._bw_section_container.is_expanded() is True

    for section in (
        sidebar._light_section,
        sidebar._color_section,
        sidebar._bw_section,
    ):
        assert section.master_slider.isHidden() is False
        assert section.options_section.header_visible() is True
        assert section.options_section.is_expanded() is False


def test_bw_video_mode_keeps_flat_slider_group_when_unbound(qapp) -> None:
    """The hidden B&W option header should stay expanded even without a session."""

    section = EditBWSection()

    section.set_video_mode(True)
    section.bind_session(None)

    assert section.master_slider.isHidden() is True
    assert section.options_section.header_visible() is False
    assert section.options_section.is_expanded() is True


def test_wb_mode_ids_survive_retranslate(qapp) -> None:
    """White-balance mode logic must not depend on translated combo text."""

    section = EditWBSection()

    section._combo.setCurrentIndex(2)
    section.retranslate_ui()

    assert section._combo.currentData() == "temperature_tint"
    assert section._current_mode == "temperature_tint"
    assert section._combo.itemData(0) == "neutral_gray"
    assert section._combo.itemData(1) == "skin_tone"
    assert section._combo.itemData(2) == "temperature_tint"
    assert section._combo.itemText(2) == "Temperature/Tint"


def test_edit_slider_rows_match_light_spacing(qapp) -> None:
    """B&W, Sharpen and Vignette sliders should use the Light row spacing."""

    bw_section = EditBWSection()
    bw_options_layout = bw_section.options_section._content.layout()
    sharpen_section = EditSharpenSection()
    vignette_section = EditVignetteSection()

    assert bw_options_layout is not None
    assert bw_options_layout.spacing() == 1
    assert sharpen_section.layout().spacing() == 1
    assert vignette_section.layout().spacing() == 1
