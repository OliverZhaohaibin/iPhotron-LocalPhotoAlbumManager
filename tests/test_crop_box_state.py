import pytest
from PySide6.QtCore import QPointF

from iPhotos.src.iPhoto.gui.ui.widgets.gl_crop_utils import CropBoxState


def test_zoom_about_point_shrinks_towards_anchor():
    state = CropBoxState()
    state.width = 0.8
    state.height = 0.6
    state.cx = 0.5
    state.cy = 0.5

    state.zoom_about_point(0.25, 0.75, 2.0)

    assert state.width == pytest.approx(0.4)
    assert state.height == pytest.approx(0.3)
    assert state.cx == pytest.approx(0.375)
    assert state.cy == pytest.approx(0.625)


def test_zoom_about_point_expands_and_clamps():
    state = CropBoxState()
    state.width = 0.3
    state.height = 0.2
    state.cx = 0.4
    state.cy = 0.6

    state.zoom_about_point(0.0, 0.0, 0.2)

    assert state.width == pytest.approx(1.0)
    assert state.height == pytest.approx(1.0)
    assert state.cx == pytest.approx(0.5)
    assert state.cy == pytest.approx(0.5)


def test_translate_pixels_moves_state():
    state = CropBoxState()
    state.width = 0.4
    state.height = 0.3
    state.cx = 0.5
    state.cy = 0.5

    state.translate_pixels(QPointF(100.0, 200.0), (1000, 1000))

    assert state.cx == pytest.approx(0.6)
    assert state.cy == pytest.approx(0.7)
