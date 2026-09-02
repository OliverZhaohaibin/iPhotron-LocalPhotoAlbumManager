"""Camera/overlay invariants, exercised through real Python map frontends."""

import math

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from maps.map_widget.map_widget import MapWidget
from maps.map_widget.map_gl_widget import MapGLWidget
from maps.map_widget.qt_location_map_widget import QtLocationMapWidget


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("widget_type", [MapWidget, MapGLWidget])
@pytest.mark.parametrize("dpr", [1.0, 2.0])
@pytest.mark.parametrize("zoom", [2.0, 2.25, 5.0])
@pytest.mark.parametrize("direction", [-1, 1])
def test_python_pan_matches_projection_at_both_limits(
    qapp, widget_type, dpr, zoom, direction, monkeypatch
):
    widget = widget_type()
    widget.resize(800, 600)
    monkeypatch.setattr(widget, "devicePixelRatioF", lambda: dpr)
    widget.set_zoom(zoom)
    camera = widget._controller
    world = camera._world_size()
    limit = 600 / (2 * world)
    camera._center_y = limit + 30 / world if direction == 1 else 1 - limit - 30 / world
    lon, lat = camera.center_lonlat()
    position = widget.project_lonlat(lon, lat)
    deltas, finishes = [], []

    def moved(delta):
        nonlocal position
        position += delta
        deltas.append(QPointF(delta))

    widget.panned.connect(moved)
    widget.panFinished.connect(lambda: finishes.append(True))
    try:
        for dy in [100, 100, 100, -20]:
            camera._on_pan_requested(QPointF(0, dy * direction))
            assert (position - widget.project_lonlat(lon, lat)).manhattanLength() <= 1e-6
        assert math.isclose(deltas[0].y(), direction * 30, abs_tol=1e-6)
        assert abs(deltas[1].y()) <= 1e-6
        assert finishes == []
        camera._notify_pan_finished()
        assert finishes == [True]
        widget.pan_by_pixels(0, -10 * direction)
        assert finishes == [True, True]
    finally:
        widget.shutdown()
        widget.close()


@pytest.mark.parametrize("widget_type", [MapWidget, MapGLWidget])
def test_python_pan_wrap_and_tall_viewport(qapp, widget_type):
    widget = widget_type()
    widget.resize(800, 1600)
    camera = widget._controller
    camera._center_x, camera._center_y = 0.99, 0.5
    point = widget.project_lonlat(-179, 0)
    deltas = []
    widget.panned.connect(lambda d: deltas.append(QPointF(d)))
    try:
        widget.pan_by_pixels(-60, 200)
        assert len(deltas) == 1
        assert abs(deltas[0].y()) < 1e-6
        assert (point + deltas[0] - widget.project_lonlat(-179, 0)).manhattanLength() < 1e-6
    finally:
        widget.shutdown()
        widget.close()


def test_qt_location_pan_uses_applied_delta_without_loading_qml():
    # Exercise this frontend's camera path without a network/QML dependency.
    class Signal:
        def __init__(self):
            self.values = []

        def emit(self, *values):
            self.values.append(values)

    class Camera:
        _center_x, _center_y = 0.99, 0.32
        _zoom = 2.0
        panned, panFinished = Signal(), Signal()

        def _world_size(self):
            return 1024.0

        def height(self):
            return 600

        def _sync_map_camera(self):
            pass

        def _emit_view_change(self):
            pass

        _wrap_center = QtLocationMapWidget._wrap_center
        _apply_pan = QtLocationMapWidget._apply_pan

    camera = Camera()
    expected_y = (camera._center_y - 600 / 2048) * 1024
    QtLocationMapWidget.pan_by_pixels(camera, -60, 100)
    delta = camera.panned.values[0][0]
    assert delta.x() == pytest.approx(-60)
    assert delta.y() == pytest.approx(expected_y)
    assert len(camera.panFinished.values) == 1
