"""Tests for layered theme and immersive viewer backdrop state."""

from PySide6.QtGui import QColor

from iPhoto.gui.ui.widgets.gl_image_viewer.fullscreen_handler import FullscreenHandler


def _make_handler(default: str = "#ffffff") -> tuple[FullscreenHandler, list[str]]:
    stylesheets: list[str] = []
    handler = FullscreenHandler(
        QColor(default),
        stylesheets.append,
        lambda: None,
    )
    return handler, stylesheets


def test_immersive_round_trip_restores_persistent_theme_override() -> None:
    handler, stylesheets = _make_handler()
    handler.set_surface_color_override("#f0f1f2")

    handler.set_immersive_background(True)
    assert handler.backdrop_color == QColor("#000000")

    handler.set_immersive_background(False)

    assert handler.backdrop_color == QColor("#f0f1f2")
    assert stylesheets[-1] == "background-color: #f0f1f2; border: none;"


def test_theme_change_during_immersive_mode_is_applied_after_exit() -> None:
    handler, _stylesheets = _make_handler()
    handler.set_surface_color_override("#eeeeee")
    handler.set_immersive_background(True)

    handler.set_surface_color_override("#d0d1d2")
    assert handler.backdrop_color == QColor("#000000")

    handler.set_immersive_background(False)

    assert handler.backdrop_color == QColor("#d0d1d2")
