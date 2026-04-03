"""Tests for video-specific edit sidebar layout changes."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")

from PySide6.QtWidgets import QApplication

from iPhoto.gui.ui.widgets.edit_sidebar import EditSidebar
from iPhoto.gui.ui.widgets.edit_bw_section import EditBWSection


@pytest.fixture
def qapp():
    """Provide a QApplication instance for widget tests."""

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


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
